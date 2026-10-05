"""
Deduplication logic for agent invocations.

Prevents duplicate investigations for the same error signature.
Uses DynamoDB with TTL for automatic cleanup.
"""

import hashlib
import logging
import os
import time
from typing import Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


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
    dedup_window_seconds: int = 3600,
) -> bool:
    """
    Check if we should investigate this error.
    
    Returns False if we've recently investigated the same error signature.
    
    Args:
        error_signature: Unique identifier for this error
        dedup_window_seconds: How long to suppress duplicates (default: 1 hour)
    
    Returns:
        True if we should investigate, False if it's a duplicate
    """
    table_name = os.getenv("DEDUP_TABLE_NAME")
    
    if not table_name:
        logger.warning("DEDUP_TABLE_NAME not set, skipping dedup check")
        return True
    
    try:
        dynamodb = boto3.resource("dynamodb")
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
