"""
AWS AppConfig feature-flag helpers.

Shared by the demo app (demo_app/config.py) and the flags MCP server
(mcp_servers/appconfig_flags.py), so both read the same format.

Stored format (the hosted configuration in infra/stacks/demo_app_stack.py) is the
AWS.AppConfig.FeatureFlags schema:

    {"version": "1",
     "flags":  {"discount_v2": {"name": "discount_v2"}},
     "values": {"discount_v2": {"enabled": false}}}

AppConfig Data's GetLatestConfiguration returns the simplified form
``{"discount_v2": {"enabled": false}}``. Flag keys must match
``^[a-z][a-zA-Z\\d_-]{0,63}$``, so code-level names such as ``DISCOUNT_V2`` are
looked up as ``discount_v2``.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Any

# These match the AppConfig resources created by infra/stacks/demo_app_stack.py.
DEFAULT_APPLICATION = "MiniDebugAssist"
DEFAULT_ENVIRONMENT = "production"
DEFAULT_PROFILE = "feature-flags"
DEFAULT_REGION = "us-east-1"

# AppConfig Data rejects poll intervals below 15 seconds.
MIN_POLL_SECONDS = 15

FLAG_KEY_PATTERN = re.compile(r"^[a-z][a-zA-Z\d_-]{0,63}$")


def aws_region() -> str:
    """Region for boto3: AWS_REGION (set on ECS tasks), then AWS_DEFAULT_REGION."""
    return os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or DEFAULT_REGION


def flag_key(flag_name: str) -> str:
    """Map a code-level flag name (``DISCOUNT_V2``) to its AppConfig key (``discount_v2``)."""
    return flag_name.strip().lower()


def build_flags_document(flags: dict[str, bool]) -> dict[str, Any]:
    """Build a hosted AWS.AppConfig.FeatureFlags document from ``{name: enabled}``."""
    keys = {flag_key(name): enabled for name, enabled in flags.items()}
    for key in keys:
        if not FLAG_KEY_PATTERN.match(key):
            raise ValueError(f"Invalid AppConfig flag key: {key!r}")
    return {
        "version": "1",
        "flags": {key: {"name": key} for key in keys},
        "values": {key: {"enabled": bool(enabled)} for key, enabled in keys.items()},
    }


def flag_state(flags: dict[str, Any], flag_name: str) -> str | None:
    """
    Read one flag from GetLatestConfiguration output as ``"on"``/``"off"``.

    Returns None if the flag is not defined.
    """
    entry = flags.get(flag_key(flag_name))
    if entry is None:
        return None
    if isinstance(entry, dict):
        return "on" if entry.get("enabled") else "off"
    if isinstance(entry, bool):
        return "on" if entry else "off"
    return str(entry)


class AppConfigFlagClient:
    """
    Minimal AppConfig Data client: one configuration session per process.

    StartConfigurationSession returns a token; each GetLatestConfiguration call
    returns the next token and an empty body when nothing changed since the last
    poll, so the previous flags are kept. Polls are spaced by the interval
    AppConfig returns (at least ``MIN_POLL_SECONDS``).
    """

    def __init__(
        self,
        application: str = DEFAULT_APPLICATION,
        environment: str = DEFAULT_ENVIRONMENT,
        profile: str = DEFAULT_PROFILE,
        region: str | None = None,
        client: Any = None,
        poll_seconds: int = MIN_POLL_SECONDS,
    ):
        self.application = application
        self.environment = environment
        self.profile = profile
        self.region = region or aws_region()
        self.poll_seconds = max(MIN_POLL_SECONDS, poll_seconds)
        self._client = client
        self._token: str | None = None
        self._flags: dict[str, Any] = {}
        self._next_poll_at = 0.0
        self._lock = threading.Lock()

    def _boto_client(self) -> Any:
        if self._client is None:
            import boto3

            self._client = boto3.client("appconfigdata", region_name=self.region)
        return self._client

    def get_flags(self) -> dict[str, Any]:
        """Return the current flags, polling AppConfig when the interval has passed."""
        with self._lock:
            now = time.monotonic()
            if now < self._next_poll_at:
                return self._flags

            client = self._boto_client()
            try:
                if self._token is None:
                    session = client.start_configuration_session(
                        ApplicationIdentifier=self.application,
                        EnvironmentIdentifier=self.environment,
                        ConfigurationProfileIdentifier=self.profile,
                        RequiredMinimumPollIntervalInSeconds=self.poll_seconds,
                    )
                    self._token = session["InitialConfigurationToken"]

                response = client.get_latest_configuration(ConfigurationToken=self._token)
            except Exception:
                # Expired/invalid token or a transient error: start a new session on
                # the next poll, and back off so failing calls don't slow every request.
                self._token = None
                self._next_poll_at = now + self.poll_seconds
                raise

            self._token = response["NextPollConfigurationToken"]
            interval = int(response.get("NextPollIntervalInSeconds") or self.poll_seconds)
            self._next_poll_at = now + max(MIN_POLL_SECONDS, interval)

            body = response["Configuration"].read()
            if body:
                self._flags = json.loads(body)
            return self._flags

    def get_flag(self, flag_name: str) -> str | None:
        """Return ``"on"``/``"off"`` for one flag, or None if it is not defined."""
        return flag_state(self.get_flags(), flag_name)
