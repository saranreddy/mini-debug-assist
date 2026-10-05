"""
Context Collector Node (DETERMINISTIC)

Maps to Uber's context_collector: fetches logs, traces, and metadata,
then preprocesses and prunes them to avoid context bloat.

This node is deterministic (no LLM) to save cost and latency.
"""

import logging
from typing import Any

from agent.config import AgentConfig
from agent.state import AgentState

logger = logging.getLogger(__name__)


def context_collector_node(state: AgentState, config: AgentConfig) -> AgentState:
    """
    Collect and preprocess context for the issue.
    
    In real mode:
    - Queries CloudWatch Logs Insights for recent errors
    - Fetches X-Ray traces for the affected requests
    - Retrieves code context via MCP github server
    
    In mock mode:
    - Uses the logs and context from the issue fixture
    
    Uber's design: Keep this deterministic to avoid LLM cost on large logs.
    Prunes MB-to-GB logs down to relevant excerpts.
    """
    logger.info(f"Collecting context for issue {state.issue_id}")
    
    if config.mode == "mock":
        # Use fixture data
        state.logs = state.issue_data.get("recent_logs", [])
        state.traces = state.issue_data.get("traces", [])
        
        # Simulate code context (in real mode, this would be fetched via MCP)
        state.code_context = {
            "demo_app/main.py": _get_mock_code_context(),
        }
        
        logger.info(f"Collected {len(state.logs)} log entries (mock mode)")
    else:
        # Real mode: Query AWS
        state.logs = _fetch_cloudwatch_logs(state.issue_data, config)
        state.traces = _fetch_xray_traces(state.issue_data, config)
        state.code_context = _fetch_code_context(state.issue_data, config)
        
        # Prune logs to avoid context bloat
        state.logs = _prune_logs(state.logs, max_entries=100)
        
        logger.info(
            f"Collected {len(state.logs)} log entries and {len(state.traces)} traces"
        )
    
    return state


def _fetch_cloudwatch_logs(issue_data: dict[str, Any], config: AgentConfig) -> list[dict]:
    """
    Fetch logs from CloudWatch Logs Insights.
    
    In production, this would:
    1. Build a Logs Insights query based on the issue data
    2. Query for logs around the time of the error
    3. Filter for relevant log streams
    """
    import boto3
    
    client = boto3.client("logs", region_name=config.bedrock_region)
    
    # Example query (simplified)
    log_group = "/aws/ecs/mini-debug-assist-demo"
    query = f"""
    fields @timestamp, @message, level, exception_type, path
    | filter exception_type like /{issue_data.get('exception_type', '')}/
    | sort @timestamp desc
    | limit 100
    """
    
    # In a real implementation, you'd execute this query and parse results
    # For now, return empty list as this requires deployed infrastructure
    logger.warning("CloudWatch Logs query not implemented - requires deployment")
    return []


def _fetch_xray_traces(issue_data: dict[str, Any], config: AgentConfig) -> list[dict]:
    """
    Fetch traces from AWS X-Ray.
    
    Maps to Uber's Jaeger distributed tracing integration.
    """
    import boto3
    
    client = boto3.client("xray", region_name=config.bedrock_region)
    
    # In a real implementation, query X-Ray for traces
    logger.warning("X-Ray trace query not implemented - requires deployment")
    return []


def _fetch_code_context(issue_data: dict[str, Any], config: AgentConfig) -> dict[str, str]:
    """
    Fetch code context from the repository.
    
    In production, this would use the MCP github server to:
    1. Search for files mentioned in the stack trace
    2. Read the relevant code sections
    3. Get git blame for recent changes
    """
    # Requires MCP github server integration
    return {}


def _prune_logs(logs: list[dict], max_entries: int = 100) -> list[dict]:
    """
    Prune logs to avoid context window bloat.
    
    Uber's approach: deterministic preprocessing before the LLM sees anything.
    Strategies:
    - Keep only logs around the error timestamp
    - Remove duplicate/similar log lines
    - Filter by log level (keep ERROR/WARN, sample INFO)
    - Truncate very long messages
    """
    # Sort by timestamp
    sorted_logs = sorted(logs, key=lambda x: x.get("timestamp", ""), reverse=True)
    
    # Take most recent entries
    pruned = sorted_logs[:max_entries]
    
    # Could add more sophisticated pruning here
    return pruned


def _get_mock_code_context() -> str:
    """
    Return mock code context for testing.
    
    In mock mode, we simulate what the context_collector would fetch.
    """
    return """
# demo_app/main.py (excerpt around line 156)

@app.get("/user/{user_id}")
async def get_user(user_id: str):
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
"""
