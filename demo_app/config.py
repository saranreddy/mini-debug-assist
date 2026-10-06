"""
Configuration management for the demo app.

Reads feature flags from AWS AppConfig (deployed app) with an env-var override
for local development. Maps to Uber's Flipr feature flag system.

Local/mock mode (the default) never touches AWS: set USE_AWS_APPCONFIG=true to
read from AppConfig, as the ECS task in infra/stacks/demo_app_stack.py does.
"""

import logging
import os

from demo_app.feature_flags import (
    DEFAULT_APPLICATION,
    DEFAULT_ENVIRONMENT,
    DEFAULT_PROFILE,
    AppConfigFlagClient,
    aws_region,
)

logger = logging.getLogger(__name__)


class Config:
    """
    Application configuration with AWS AppConfig integration.

    Uber's Debug Assist integrates with Flipr (their feature flag system).
    This demonstrates the same pattern using AWS AppConfig.
    """

    def __init__(self):
        self.use_aws = os.getenv("USE_AWS_APPCONFIG", "false").lower() == "true"
        self.appconfig_app = os.getenv("APPCONFIG_APPLICATION", DEFAULT_APPLICATION)
        self.appconfig_env = os.getenv("APPCONFIG_ENVIRONMENT", DEFAULT_ENVIRONMENT)
        self.appconfig_config = os.getenv("APPCONFIG_CONFIGURATION", DEFAULT_PROFILE)
        self.region = aws_region()
        self._appconfig: AppConfigFlagClient | None = None

    def _appconfig_client(self) -> AppConfigFlagClient:
        if self._appconfig is None:
            self._appconfig = AppConfigFlagClient(
                application=self.appconfig_app,
                environment=self.appconfig_env,
                profile=self.appconfig_config,
                region=self.region,
            )
        return self._appconfig

    def get_flag(self, flag_name: str, default: str = "off") -> str:
        """
        Get a feature flag value ("on" or "off").

        Order: environment variable override (e.g. DISCOUNT_V2=on), then AWS
        AppConfig when USE_AWS_APPCONFIG=true, then ``default``. AppConfig values
        are cached by AppConfigFlagClient and re-polled at AppConfig's interval,
        so a flag flipped in the console shows up within about a minute.
        """
        env_value = os.getenv(flag_name)
        if env_value is not None:
            return env_value

        if self.use_aws:
            try:
                value = self._appconfig_client().get_flag(flag_name)
                if value is not None:
                    return value
            except Exception as e:
                # Don't fail the request because AppConfig is unreachable.
                logger.warning(f"Error fetching flag {flag_name} from AppConfig: {e}")

        return default


# Global config instance
config = Config()
