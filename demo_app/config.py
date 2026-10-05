"""
Configuration management for the demo app.

Reads from AWS AppConfig with env-var fallback for local development.
Maps to Uber's Flipr feature flag system.
"""

import os

import boto3
from botocore.exceptions import ClientError


class Config:
    """
    Application configuration with AWS AppConfig integration.

    Uber's Debug Assist integrates with Flipr (their feature flag system).
    This demonstrates the same pattern using AWS AppConfig.
    """

    def __init__(self):
        self.use_aws = os.getenv("USE_AWS_APPCONFIG", "false").lower() == "true"
        self.appconfig_app = os.getenv("APPCONFIG_APPLICATION", "MiniDebugAssist")
        self.appconfig_env = os.getenv("APPCONFIG_ENVIRONMENT", "dev")
        self.appconfig_config = os.getenv("APPCONFIG_CONFIGURATION", "feature-flags")

        # Cache for flags
        self._flag_cache: dict[str, str] = {}

    def get_flag(self, flag_name: str, default: str = "off") -> str:
        """
        Get a feature flag value.

        In real mode: reads from AWS AppConfig.
        In local mode: reads from environment variables.

        Args:
            flag_name: Name of the feature flag
            default: Default value if not found

        Returns:
            Flag value (typically "on" or "off", but can be any string)
        """
        # Check cache first
        if flag_name in self._flag_cache:
            return self._flag_cache[flag_name]

        # Try environment variable first (local override)
        env_value = os.getenv(flag_name)
        if env_value is not None:
            self._flag_cache[flag_name] = env_value
            return env_value

        # If AWS AppConfig is enabled, try fetching from there
        if self.use_aws:
            try:
                value = self._fetch_from_appconfig(flag_name)
                if value is not None:
                    self._flag_cache[flag_name] = value
                    return value
            except Exception as e:
                # Log error but don't fail - fall back to default
                print(f"Error fetching flag {flag_name} from AppConfig: {e}")

        # Fall back to default
        self._flag_cache[flag_name] = default
        return default

    def _fetch_from_appconfig(self, flag_name: str) -> str | None:
        """
        Fetch a configuration value from AWS AppConfig.

        Note: In production, you'd use AppConfig's client-side SDK with caching
        and polling. This is a simplified version for learning.
        """
        try:
            client = boto3.client("appconfig")

            # Start configuration session
            response = client.start_configuration_session(
                ApplicationIdentifier=self.appconfig_app,
                EnvironmentIdentifier=self.appconfig_env,
                ConfigurationProfileIdentifier=self.appconfig_config,
            )

            # Get configuration
            config_token = response["InitialConfigurationToken"]
            config_response = client.get_latest_configuration(ConfigurationToken=config_token)

            # Parse configuration (simplified - assumes JSON format)
            import json

            config_data = json.loads(config_response["Configuration"].read())
            return config_data.get(flag_name)

        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                return None
            raise


# Global config instance
config = Config()
