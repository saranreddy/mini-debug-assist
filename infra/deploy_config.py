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

It also resolves the GitHub token secret's complete ARN. ECS needs the full ARN
(with the 6-character suffix Secrets Manager adds) to inject a secret field; the
partial ARN that Secret.from_secret_name_v2 produces is not enough. `make deploy`
looks it up with `aws secretsmanager describe-secret` and passes it to CDK as
GITHUB_TOKEN_SECRET_ARN. Without AWS credentials (CI, a laptop without a
profile) `cdk synth` uses a placeholder ARN so it still works offline.

Used by infra/app.py (every synth/deploy), `make deploy` (pre-check), and
`make doctor` (informational).

Run directly to check your setup:
    python3 infra/deploy_config.py               # check GITHUB_REPO and the secret
    python3 infra/deploy_config.py --secret-arn  # print only the secret's ARN
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping
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


# Created by `make setup-secrets` (scripts/setup_github_token.sh)
GITHUB_TOKEN_SECRET_NAME = "mini-debug-assist/github-token"

# Used only when synthesizing without AWS credentials (CI). Deploying it would
# give the agent a secret that does not exist, so infra/app.py looks up the real
# ARN whenever credentials are present.
PLACEHOLDER_SECRET_ARN = (
    "arn:aws:secretsmanager:us-east-1:123456789012:secret:" f"{GITHUB_TOKEN_SECRET_NAME}-SYNTH0"
)
# The account CI and the tests synthesize with (CDK_DEFAULT_ACCOUNT); not a real one.
PLACEHOLDER_ACCOUNT = "123456789012"

SECRET_ARN_PATTERN = re.compile(
    r"^arn:aws[a-z-]*:secretsmanager:[a-z0-9-]+:\d{12}:secret:"
    + re.escape(GITHUB_TOKEN_SECRET_NAME)
    + r"-[A-Za-z0-9]{6}$"
)

SETUP_SECRETS_HINT = "Run `make setup-secrets` first (same AWS account and region as the deploy)."


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


Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def lookup_secret_arn(run: Runner = subprocess.run) -> str:
    """Complete ARN of the GitHub token secret, via `aws secretsmanager describe-secret`."""
    cmd = [
        "aws",
        "secretsmanager",
        "describe-secret",
        "--secret-id",
        GITHUB_TOKEN_SECRET_NAME,
        "--query",
        "ARN",
        "--output",
        "text",
    ]
    try:
        result = run(cmd, capture_output=True, text=True, timeout=30)
    except FileNotFoundError as e:
        raise DeployConfigError(
            "The AWS CLI is not installed, so the GitHub token secret can't be looked up."
        ) from e
    except subprocess.TimeoutExpired as e:
        raise DeployConfigError("Timed out looking up the GitHub token secret.") from e

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        if "ResourceNotFoundException" in stderr:
            raise DeployConfigError(
                f"Secret {GITHUB_TOKEN_SECRET_NAME} does not exist in this account/region.\n"
                + SETUP_SECRETS_HINT
            )
        raise DeployConfigError(
            f"Could not look up secret {GITHUB_TOKEN_SECRET_NAME}: {stderr or 'aws CLI failed'}\n"
            + SETUP_SECRETS_HINT
        )

    arn = (result.stdout or "").strip()
    if not SECRET_ARN_PATTERN.match(arn):
        raise DeployConfigError(f"Unexpected ARN for {GITHUB_TOKEN_SECRET_NAME}: '{arn}'")
    return arn


def has_aws_credentials(environ: Mapping[str, str]) -> bool:
    """
    Whether this synth is (probably) a real deploy.

    The CDK CLI sets CDK_DEFAULT_ACCOUNT when it finds credentials; CI and the
    tests use the 123456789012 placeholder account and no credentials.
    """
    account = environ.get("CDK_DEFAULT_ACCOUNT", "")
    return bool(
        environ.get("AWS_ACCESS_KEY_ID")
        or environ.get("AWS_PROFILE")
        or (account and account != PLACEHOLDER_ACCOUNT)
    )


def resolve_github_token_secret_arn(
    environ: Mapping[str, str] | None = None, run: Runner = subprocess.run
) -> tuple[str, bool]:
    """
    Return (complete secret ARN, is_placeholder).

    1. GITHUB_TOKEN_SECRET_ARN (set by `make deploy`), validated; the literal
       "placeholder" (set by `make bootstrap`/`make destroy`) skips the lookup
    2. With AWS credentials: looked up with the AWS CLI (fails with a fix-it message)
    3. Without credentials: PLACEHOLDER_SECRET_ARN, so `cdk synth` works offline
    """
    environ = os.environ if environ is None else environ
    arn = environ.get("GITHUB_TOKEN_SECRET_ARN", "").strip()
    if arn == "placeholder":
        # `make bootstrap` / `make destroy`: the app is synthesized but no agent
        # task is deployed, so the secret doesn't need to exist.
        return PLACEHOLDER_SECRET_ARN, True
    if arn:
        if not SECRET_ARN_PATTERN.match(arn):
            raise DeployConfigError(
                f"GITHUB_TOKEN_SECRET_ARN is '{arn}', which is not the complete ARN of "
                f"{GITHUB_TOKEN_SECRET_NAME} (it must end in a 6-character suffix).\n"
                "Unset it and run `make deploy`, which looks the ARN up for you."
            )
        return arn, False
    if has_aws_credentials(environ):
        return lookup_secret_arn(run), False
    return PLACEHOLDER_SECRET_ARN, True


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv

    if argv == ["--secret-arn"]:
        try:
            print(lookup_secret_arn())
        except DeployConfigError as e:
            print(f"❌ {e}", file=sys.stderr)
            return 1
        return 0

    ok = True
    try:
        repo = resolve_github_repo()
        print(f"✅ GITHUB_REPO={repo} (the agent opens PRs here)")
    except DeployConfigError as e:
        print(f"❌ {e}", file=sys.stderr)
        ok = False

    try:
        arn = lookup_secret_arn()
        print(f"✅ GitHub token secret: {arn}")
    except DeployConfigError as e:
        print(f"❌ {e}", file=sys.stderr)
        ok = False

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
