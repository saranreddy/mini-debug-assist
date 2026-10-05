"""
Demo FastAPI application with planted bugs.

This service demonstrates three types of bugs that the agent should detect:
1. KeyError: Missing 'email' field in user data
2. Performance: Recomputation inside a loop
3. Feature flag: Division by zero when DISCOUNT_V2='on'

Maps to Uber's production services monitored by Healthline.
"""

import logging
import time
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pythonjsonlogger import jsonlogger

from demo_app.config import config

# ===== Structured JSON Logging =====
# Maps to Uber's ClickHouse-based logging platform that the agent queries

logger = logging.getLogger("demo_app")
logHandler = logging.StreamHandler()
formatter = jsonlogger.JsonFormatter(
    "%(asctime)s %(name)s %(levelname)s %(message)s %(pathname)s %(lineno)d"
)
logHandler.setFormatter(formatter)
logger.addHandler(logHandler)
logger.setLevel(logging.INFO)


# ===== FastAPI App =====

app = FastAPI(
    title="Mini Debug Assist Demo",
    description="Demo service with planted bugs for agent debugging",
    version="0.1.0"
)


# ===== Optional AWS X-Ray Tracing =====
# Maps to Uber's Jaeger distributed tracing (queried via jaeger MCP)

USE_XRAY = config.use_aws and True  # Enable if you have X-Ray daemon

if USE_XRAY:
    try:
        from aws_xray_sdk.core import xray_recorder
        from aws_xray_sdk.ext.flask.middleware import XRayMiddleware
        
        xray_recorder.configure(service="MiniDebugAssist-Demo")
        # Note: FastAPI support is limited, but this shows the pattern
        logger.info("X-Ray tracing enabled")
    except ImportError:
        logger.warning("aws-xray-sdk not installed, tracing disabled")
        USE_XRAY = False


# ===== Middleware for logging =====

@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log all requests with timing information."""
    start_time = time.time()
    
    # Log request
    logger.info(
        "Request started",
        extra={
            "method": request.method,
            "path": request.url.path,
            "client": request.client.host if request.client else None,
        }
    )
    
    # Process request
    response = await call_next(request)
    
    # Log response
    duration_ms = (time.time() - start_time) * 1000
    logger.info(
        "Request completed",
        extra={
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
        }
    )
    
    return response


# ===== Mock Data Store =====
# Simulates a database or cache

USERS_DB = {
    "1": {"id": "1", "name": "Alice", "email": "alice@example.com"},
    "2": {"id": "2", "name": "Bob", "email": "bob@example.com"},
    # Bug: User 3 is missing the 'email' field
    "3": {"id": "3", "name": "Charlie"},
    "4": {"id": "4", "name": "Diana", "email": "diana@example.com"},
}


# ===== ENDPOINT 1: KeyError Bug =====
# BUG: Assumes all users have an 'email' field, but user '3' doesn't

@app.get("/user/{user_id}")
async def get_user(user_id: str):
    """
    Get user information by ID.
    
    BUG: Will raise KeyError for user_id='3' because that user
    has no 'email' field in the database.
    
    This maps to common production crashes where data assumptions
    are violated (missing fields, schema changes, etc.).
    """
    logger.info(f"Fetching user {user_id}")
    
    if user_id not in USERS_DB:
        logger.warning(f"User {user_id} not found")
        raise HTTPException(status_code=404, detail="User not found")
    
    user = USERS_DB[user_id]
    
    # BUG: This assumes 'email' always exists
    email = user["email"]  # KeyError when user_id='3'
    
    logger.info(f"User {user_id} retrieved successfully")
    
    return {
        "id": user["id"],
        "name": user["name"],
        "email": email,
    }


# ===== ENDPOINT 2: Performance Bug =====
# BUG: Recomputes an expensive list operation inside a loop

@app.get("/report")
async def generate_report(entries: int = 100):
    """
    Generate a report by processing entries.
    
    BUG: Recomputes `all_user_ids` inside the loop on every iteration.
    This is O(n*m) when it should be O(n).
    
    This maps to performance regressions that cause timeouts or high latency.
    """
    logger.info(f"Generating report for {entries} entries")
    
    start = time.time()
    results = []
    
    for i in range(entries):
        # BUG: This list comprehension is recomputed every iteration!
        # Should be moved outside the loop
        all_user_ids = [uid for uid in USERS_DB.keys()]
        
        # Simulate some work
        user_count = len(all_user_ids)
        results.append({
            "entry": i,
            "user_count": user_count,
            "timestamp": time.time(),
        })
    
    duration = time.time() - start
    
    logger.info(f"Report generated in {duration:.3f}s")
    
    return {
        "entries": entries,
        "duration_seconds": duration,
        "results": results[:10],  # Return first 10 for brevity
    }


# ===== ENDPOINT 3: Feature Flag Bug =====
# BUG: Division by zero when DISCOUNT_V2='on' and certain conditions occur

@app.get("/discount")
async def calculate_discount(price: float, user_id: Optional[str] = None):
    """
    Calculate discount for a purchase.
    
    BUG: When DISCOUNT_V2='on', the new discount calculation can
    divide by zero if the user has no purchases.
    
    This maps to feature-flag-gated bugs that only appear after rollout.
    Uber's agent can propose rolling back the Flipr flag as mitigation.
    """
    logger.info(f"Calculating discount for price={price}, user_id={user_id}")
    
    discount_version = config.get_flag("DISCOUNT_V2", "off")
    
    if discount_version == "on":
        logger.info("Using DISCOUNT_V2 algorithm")
        
        # New algorithm (buggy)
        if user_id:
            # Fetch user purchase history
            purchase_count = _get_purchase_count(user_id)
            
            # BUG: If purchase_count is 0, this divides by zero!
            discount_multiplier = 100 / purchase_count
            discount = min(price * 0.01 * discount_multiplier, price * 0.5)
        else:
            discount = price * 0.05
    else:
        logger.info("Using legacy discount algorithm")
        # Old algorithm (safe)
        discount = price * 0.1 if price > 100 else price * 0.05
    
    final_price = price - discount
    
    logger.info(f"Discount calculated: {discount:.2f}, final price: {final_price:.2f}")
    
    return {
        "original_price": price,
        "discount": discount,
        "final_price": final_price,
        "discount_version": discount_version,
    }


def _get_purchase_count(user_id: str) -> int:
    """
    Mock function to get purchase count for a user.
    
    In a real system, this would query a database.
    User '1' has no purchases, triggering the bug.
    """
    purchase_counts = {
        "2": 5,
        "3": 10,
        "4": 2,
    }
    return purchase_counts.get(user_id, 0)  # Returns 0 for user '1'


# ===== Health Check =====

@app.get("/health")
async def health_check():
    """Health check endpoint for monitoring."""
    return {
        "status": "healthy",
        "service": "mini-debug-assist-demo",
        "version": "0.1.0",
    }


# ===== Error Handler =====

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    Global exception handler that logs errors in structured format.
    
    These logs are what the agent's context_collector will ingest
    (maps to Uber's CloudWatch Logs Insights queries).
    """
    logger.error(
        f"Unhandled exception: {exc}",
        extra={
            "exception_type": type(exc).__name__,
            "exception_message": str(exc),
            "path": request.url.path,
            "method": request.method,
        },
        exc_info=True,
    )
    
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "detail": str(exc),
            "type": type(exc).__name__,
        }
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
