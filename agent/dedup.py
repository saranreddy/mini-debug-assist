"""
Deduplication logic for agent invocations.

Prevents duplicate investigations for the same error signature.
Uses DynamoDB with TTL for automatic cleanup.
"""

import hashlib
import logging
import os
import time

import boto3
from botocore.exceptions import ClientError

from agent.config import aws_region

logger = logging.getLogger(__name__)

# One investigation per error signature per 15 minutes. Override with
# DEDUP_WINDOW_SECONDS; clear early with `make reset-dedup`.
DEFAULT_DEDUP_WINDOW_SECONDS = 900


def default_dedup_window() -> int:
    """Dedup window from DEDUP_WINDOW_SECONDS, else the 15-minute default."""
    try:
        return int(os.getenv("DEDUP_WINDOW_SECONDS", DEFAULT_DEDUP_WINDOW_SECONDS))
    except ValueError:
        return DEFAULT_DEDUP_WINDOW_SECONDS


def get_error_signature(alarm_name: str, error_type: str, endpoint: str) -> str:
    """
    Generate a unique signature for an error.

    This signature is used to deduplicate investigations.
    Same error type on same endpoint = same signature.
    """
    signature_parts = [
        alarm_name,
        error_type or "unknown",
        endpoint or "unknown",
    ]
    signature_str = "|".join(signature_parts)

    # Hash for consistent length
    return hashlib.sha256(signature_str.encode()).hexdigest()[:32]


def should_investigate(
    error_signature: str,
    dedup_window_seconds: int | None = None,
) -> bool:
    """
    Check if we should investigate this error.

    Returns False if we've recently investigated the same error signature.

    Args:
        error_signature: Unique identifier for this error
        dedup_window_seconds: How long to suppress duplicates
            (default: DEDUP_WINDOW_SECONDS or 15 minutes)

    Returns:
        True if we should investigate, False if it's a duplicate
    """
    if dedup_window_seconds is None:
        dedup_window_seconds = default_dedup_window()

    table_name = os.getenv("DEDUP_TABLE_NAME")

    if not table_name:
        logger.warning("DEDUP_TABLE_NAME not set, skipping dedup check")
        return True

    try:
        dynamodb = boto3.resource("dynamodb", region_name=aws_region())
        table = dynamodb.Table(table_name)

        current_time = int(time.time())
        ttl = current_time + dedup_window_seconds

        # Try to insert with condition that item doesn't exist or is expired
        try:
            table.put_item(
                Item={
                    "error_signature": error_signature,
                    "first_seen": current_time,
                    "last_updated": current_time,
                    "ttl": ttl,
                },
                ConditionExpression="attribute_not_exists(error_signature) OR #ttl < :now",
                ExpressionAttributeNames={
                    "#ttl": "ttl",
                },
                ExpressionAttributeValues={
                    ":now": current_time,
                },
            )

            logger.info(f"New investigation for signature {error_signature[:8]}...")
            return True

        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                # Item exists and is not expired - duplicate
                logger.info(
                    f"Skipping duplicate investigation for signature {error_signature[:8]}... "
                    f"(within {dedup_window_seconds}s window)"
                )
                return False
            raise

    except Exception as e:
        logger.error(f"Error checking dedup: {e}", exc_info=True)
        # On error, allow investigation (fail open)
        return True
