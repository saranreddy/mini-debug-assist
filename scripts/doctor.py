#!/usr/bin/env python3
"""
Doctor script - check prerequisites for Mini Debug Assist.

Checks:
- AWS CLI and credentials
- Node.js and CDK
- Docker
- Python and dependencies
- Bedrock model access for configured models
"""

import json
import subprocess
import sys


def check_command(cmd, name, install_hint):
    """Check if a command exists."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        return True, result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False, f"❌ {name} not found. Install: {install_hint}"


def check_aws_credentials():
    """Check if AWS credentials are configured."""
    try:
        result = subprocess.run(
            ["aws", "sts", "get-caller-identity"], capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            identity = json.loads(result.stdout)
            return (
                True,
                f"✅ AWS credentials valid (Account: {identity['Account']}, User: {identity['Arn']})",
            )
        else:
            return False, "❌ AWS credentials not configured. Run: aws configure"
    except Exception as e:
        return False, f"❌ Error checking AWS credentials: {e}"


def check_bedrock_access():
    """Check Bedrock model access for configured models."""
    from agent.config import AgentConfig

    config = AgentConfig.from_env("aws")

    models_to_check = [
        ("Sonnet", config.model_classify),
        ("Opus", config.model_fix),
    ]

    results = []
    all_ok = True

    for name, model_id in models_to_check:
        # Extract base model ID (remove us./global. prefix if present)
        base_model = model_id.split(".")[-1] if "." in model_id else model_id

        try:
            result = subprocess.run(
                [
                    "aws",
                    "bedrock",
                    "get-foundation-model",
                    "--model-identifier",
                    base_model,
                    "--region",
                    config.bedrock_region,
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode == 0:
                results.append(f"  ✅ {name} ({model_id}) - accessible")
            else:
                all_ok = False
                error = result.stderr.strip()
                if "AccessDeniedException" in error or "Access Denied" in error:
                    results.append(f"  ❌ {name} ({model_id}) - access denied")
                    results.append(
                        "     Fix: Request model access in Bedrock console → Model access"
                    )
                else:
                    results.append(f"  ❌ {name} ({model_id}) - {error}")

        except Exception as e:
            all_ok = False
            results.append(f"  ❌ {name} ({model_id}) - error: {e}")

    return all_ok, "\n".join(results)


def main():
    """Run all checks."""
    print("🔍 Mini Debug Assist - Prerequisites Check\n")

    checks = []

    # AWS CLI
    ok, msg = check_command(
        ["aws", "--version"],
        "AWS CLI",
        "https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html",
    )
    checks.append((ok, msg))
    print(msg)

    # AWS Credentials
    ok, msg = check_aws_credentials()
    checks.append((ok, msg))
    print(msg)

    # Node.js
    ok, msg = check_command(["node", "--version"], "Node.js", "https://nodejs.org/")
    checks.append((ok, msg))
    print(msg if not ok else f"✅ Node.js: {msg}")

    # CDK
    ok, msg = check_command(["cdk", "--version"], "AWS CDK", "npm install -g aws-cdk")
    checks.append((ok, msg))
    print(msg if not ok else f"✅ AWS CDK: {msg}")

    # Docker
    ok, msg = check_command(
        ["docker", "--version"], "Docker", "https://docs.docker.com/get-docker/"
    )
    checks.append((ok, msg))
    print(msg if not ok else f"✅ Docker: {msg}")

    # Python
    ok, msg = check_command(
        [sys.executable, "--version"], "Python", "https://www.python.org/downloads/"
    )
    checks.append((ok, msg))
    print(msg if not ok else f"✅ Python: {msg}")

    # Python dependencies
    try:
        import boto3
        import fastapi
        import langgraph

        print("✅ Python dependencies installed")
        checks.append((True, "Dependencies OK"))
    except ImportError as e:
        msg = f"❌ Missing Python dependencies: {e}. Run: pip install -r requirements.txt"
        print(msg)
        checks.append((False, msg))

    # Bedrock access (only if AWS credentials work)
    if checks[1][0]:  # AWS credentials check passed
        print("\n🔑 Checking Bedrock model access...")
        ok, msg = check_bedrock_access()
        checks.append((ok, msg))
        print(msg)
    else:
        print("\n⏭️  Skipping Bedrock check (AWS credentials not configured)")

    # Summary
    print("\n" + "=" * 60)
    passed = sum(1 for ok, _ in checks if ok)
    total = len(checks)

    if passed == total:
        print(f"✅ All checks passed ({passed}/{total})")
        print("\nYou're ready to deploy! Next steps:")
        print("  1. make setup-secrets  # Store GitHub token")
        print("  2. make bootstrap      # One-time CDK setup")
        print("  3. make deploy         # Deploy agent")
        return 0
    else:
        print(f"⚠️  {total - passed} check(s) failed ({passed}/{total} passed)")
        print("\nFix the issues above before deploying.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
