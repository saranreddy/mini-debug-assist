#!/usr/bin/env python3
"""
Clear the agent's dedup records (DynamoDB table mini-debug-assist-dedup).

The agent investigates each error signature at most once per dedup window
(15 minutes by default; DEDUP_WINDOW_SECONDS on the task overrides it). While a
record is live, a re-triggered alarm starts the agent task but it exits early
with "Skipping duplicate investigation". Run this to allow an immediate rerun:

    make reset-dedup

Uses the AWS CLI with your current credentials and region.
"""

import json
import subprocess
import sys
from collections.abc import Callable

DEDUP_TABLE_NAME = "mini-debug-assist-dedup"
KEY_ATTRIBUTE = "error_signature"

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def _aws(run: Runner, *args: str) -> str:
    result = run(["aws", "dynamodb", *args], capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "aws CLI failed")
    return result.stdout


def list_signatures(run: Runner = subprocess.run) -> list[str]:
    """Every error_signature in the table (the CLI paginates the scan)."""
    output = _aws(
        run,
        "scan",
        "--table-name",
        DEDUP_TABLE_NAME,
        "--projection-expression",
        KEY_ATTRIBUTE,
        "--output",
        "json",
    )
    items = json.loads(output or "{}").get("Items", [])
    return [item[KEY_ATTRIBUTE]["S"] for item in items if KEY_ATTRIBUTE in item]


def reset_dedup(run: Runner = subprocess.run) -> int:
    """Delete every dedup record; return how many were deleted."""
    signatures = list_signatures(run)
    for signature in signatures:
        key = json.dumps({KEY_ATTRIBUTE: {"S": signature}})
        _aws(run, "delete-item", "--table-name", DEDUP_TABLE_NAME, "--key", key)
    return len(signatures)


def main() -> int:
    try:
        deleted = reset_dedup()
    except FileNotFoundError:
        print("❌ AWS CLI not found. Install it first.", file=sys.stderr)
        return 1
    except RuntimeError as e:
        print(f"❌ Could not clear {DEDUP_TABLE_NAME}: {e}", file=sys.stderr)
        print("   Is the stack deployed in this account/region? (make deploy)", file=sys.stderr)
        return 1
    print(f"✅ Cleared {deleted} dedup record(s) from {DEDUP_TABLE_NAME}")
    print("   The next alarm (make trigger-bug) starts a fresh investigation.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
