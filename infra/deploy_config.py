#!/usr/bin/env python3
"""
Deploy-time configuration for Mini Debug Assist.

GITHUB_REPO is the fork the agent opens pull requests in. It is baked into the
agent's ECS task definition at deploy time, so an empty or placeholder value
would deploy an agent that cannot open PRs. This module resolves it and fails
loudly instead.

Resolution order:
1. The GITHUB_REPO environment variable (if non-empty)
2. GITHUB_REPO in the repo-root .env file (created with `cp .env.example .env`)

Used by infra/app.py (every synth/deploy), `make deploy` (pre-check), and
`make doctor` (informational).

Run directly to check your setup:
    python3 infra/deploy_config.py
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOTENV_PATH = REPO_ROOT / ".env"

# The value shipped in .env.example; deploying it would point the agent at a
# repo that does not exist.
PLACEHOLDER_REPO = "your-username/mini-debug-assist"

# owner/name, using GitHub's allowed characters
GITHUB_REPO_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$")

HOW_TO_FIX = (
    "Set it to your fork, owner/name (for example octocat/mini-debug-assist), either in .env:\n"
    "    GITHUB_REPO=octocat/mini-debug-assist\n"
    "or in your shell before deploying:\n"
    "    export GITHUB_REPO=octocat/mini-debug-assist"
)


class DeployConfigError(Exception):
    """Raised when deploy-time configuration is missing or invalid."""


def read_dotenv(path: Path) -> dict[str, str]:
    """Parse a simple KEY=VALUE .env file (comments, blank lines, quotes, `export`)."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, _, value = line.partition("=")
        value = value.strip()
        if value[:1] in {'"', "'"} and value[-1:] == value[:1] and len(value) >= 2:
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].strip()  # inline comment
        values[key.strip()] = value
    return values


def resolve_github_repo(
    environ: Mapping[str, str] | None = None, dotenv_path: Path = DOTENV_PATH
) -> str:
    """Return GITHUB_REPO (owner/name) or raise DeployConfigError with a fix-it message."""
    environ = os.environ if environ is None else environ
    value = environ.get("GITHUB_REPO", "").strip()
    source = "the GITHUB_REPO environment variable"
    if not value:
        value = read_dotenv(dotenv_path).get("GITHUB_REPO", "").strip()
        source = str(dotenv_path)

    if not value:
        raise DeployConfigError(
            "GITHUB_REPO is not set, so the agent would not know which repo to open PRs in.\n"
            + HOW_TO_FIX
        )
    if value == PLACEHOLDER_REPO:
        raise DeployConfigError(
            f"GITHUB_REPO in {source} is still the placeholder '{PLACEHOLDER_REPO}'.\n" + HOW_TO_FIX
        )
    if not GITHUB_REPO_PATTERN.match(value):
        raise DeployConfigError(
            f"GITHUB_REPO in {source} is '{value}', which is not owner/name.\n" + HOW_TO_FIX
        )
    return value


def main() -> int:
    try:
        repo = resolve_github_repo()
    except DeployConfigError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1
    print(f"✅ GITHUB_REPO={repo} (the agent opens PRs here)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
