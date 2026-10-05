"""
Context Collector Node (DETERMINISTIC)

Maps to Uber's context_collector: fetches logs, traces, and metadata,
then preprocesses and prunes them to avoid context bloat.

This node is deterministic (no LLM) to save cost and latency.
"""

import base64
import json
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Any

import boto3

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

        logger.info(f"Collected {len(state.logs)} log entries and {len(state.traces)} traces")

    return state


def _fetch_cloudwatch_logs(issue_data: dict[str, Any], config: AgentConfig) -> list[dict]:
    """
    Fetch logs from CloudWatch Logs Insights.

    Builds and executes a Logs Insights query for the error window.
    """
    client = boto3.client("logs", region_name=config.bedrock_region)

    # Determine log group
    log_group = os.getenv("DEMO_APP_LOG_GROUP", "/aws/ecs/mini-debug-assist-demo")

    # Determine time window
    if "timestamp" in issue_data:
        try:
            error_time = datetime.fromisoformat(issue_data["timestamp"].replace("Z", "+00:00"))
        except:
            error_time = datetime.utcnow()
    else:
        error_time = datetime.utcnow()

    start_time = error_time - timedelta(minutes=15)
    end_time = error_time + timedelta(minutes=5)

    # Build query
    error_type = issue_data.get("error_type", "")
    endpoint = issue_data.get("endpoint", "")

    if error_type:
        filter_clause = f'| filter exception_type = "{error_type}"'
    elif endpoint:
        filter_clause = f'| filter path = "{endpoint}"'
    else:
        filter_clause = '| filter level = "ERROR"'

    query = f"""
    fields @timestamp, @message, level, exception_type, exception_message, path, method
    {filter_clause}
    | sort @timestamp desc
    | limit 100
    """

    logger.info(f"Querying log group {log_group} from {start_time} to {end_time}")

    try:
        # Start query
        response = client.start_query(
            logGroupName=log_group,
            startTime=int(start_time.timestamp()),
            endTime=int(end_time.timestamp()),
            queryString=query,
        )

        query_id = response["queryId"]
        logger.info(f"Started query {query_id}")

        # Poll for results (max 30 seconds)
        for i in range(30):
            time.sleep(1)

            result_response = client.get_query_results(queryId=query_id)
            status = result_response["status"]

            if status == "Complete":
                results = result_response["results"]
                logger.info(f"Query complete, found {len(results)} log entries")

                # Parse results into dict format
                parsed_logs = []
                for result in results:
                    log_entry = {}
                    for field in result:
                        field_name = field["field"].lstrip("@")
                        log_entry[field_name] = field["value"]
                    parsed_logs.append(log_entry)

                return parsed_logs

            elif status in ["Failed", "Cancelled"]:
                logger.error(f"Query {status.lower()}")
                return []

        logger.warning("Query timed out after 30 seconds")
        return []

    except Exception as e:
        logger.error(f"Error fetching CloudWatch logs: {e}")
        return []


def _fetch_xray_traces(issue_data: dict[str, Any], config: AgentConfig) -> list[dict]:
    """
    Fetch traces from AWS X-Ray.

    Maps to Uber's Jaeger distributed tracing integration.
    """
    client = boto3.client("xray", region_name=config.bedrock_region)

    # Determine time window
    if "timestamp" in issue_data:
        try:
            error_time = datetime.fromisoformat(issue_data["timestamp"].replace("Z", "+00:00"))
        except:
            error_time = datetime.utcnow()
    else:
        error_time = datetime.utcnow()

    start_time = error_time - timedelta(minutes=15)
    end_time = error_time + timedelta(minutes=5)

    # Build filter expression
    endpoint = issue_data.get("endpoint", "")
    if endpoint:
        filter_expression = f'service("MiniDebugAssist-Demo") AND http.url CONTAINS "{endpoint}"'
    else:
        filter_expression = 'service("MiniDebugAssist-Demo") AND error = true'

    logger.info(f"Querying X-Ray traces from {start_time} to {end_time}")

    try:
        # Get trace summaries
        response = client.get_trace_summaries(
            StartTime=start_time,
            EndTime=end_time,
            FilterExpression=filter_expression,
            Sampling=True,
        )

        summaries = response.get("TraceSummaries", [])
        logger.info(f"Found {len(summaries)} trace summaries")

        if not summaries:
            return []

        # Get full traces for error traces (up to 10)
        error_trace_ids = [s["Id"] for s in summaries if s.get("HasError") or s.get("HasFault")][
            :10
        ]

        if not error_trace_ids:
            return []

        traces_response = client.batch_get_traces(TraceIds=error_trace_ids)

        traces = traces_response.get("Traces", [])
        logger.info(f"Retrieved {len(traces)} full traces")

        # Simplify trace data
        simplified_traces = []
        for trace in traces:
            # Check if any segment has an error
            has_error = False
            for seg in trace.get("Segments", []):
                try:
                    # Parse segment document (it's a JSON string)
                    doc = json.loads(seg.get("Document", "{}"))
                    # Check both lowercase and uppercase variants
                    http_data = doc.get("http") or doc.get("Http") or {}
                    response_data = http_data.get("response") or http_data.get("Response") or {}
                    status = response_data.get("status") or response_data.get("Status") or 0
                    if status >= 400:
                        has_error = True
                        break
                except:
                    pass

            simplified_traces.append(
                {
                    "id": trace["Id"],
                    "duration": trace.get("Duration"),
                    "segments": len(trace.get("Segments", [])),
                    "has_error": has_error,
                }
            )

        return simplified_traces

    except Exception as e:
        logger.error(f"Error fetching X-Ray traces: {e}")
        return []


def _fetch_code_context(issue_data: dict[str, Any], config: AgentConfig) -> dict[str, str]:
    """
    Fetch code context from the repository.

    Uses GitHub API to read relevant files based on error data.
    In production, would use the github MCP server.
    """
    import os

    # Get GitHub config
    github_repo = os.getenv("GITHUB_REPO")
    github_token = os.getenv("GITHUB_TOKEN")

    if not github_repo or not github_token:
        logger.warning("GitHub credentials not configured, skipping code context")
        return {}

    try:
        from github import Github

        gh = Github(github_token)
        repo = gh.get_repo(github_repo)

        # Determine which files to fetch based on error data
        endpoint = issue_data.get("endpoint", "")
        error_type = issue_data.get("error_type", "")

        # Map endpoint to likely file
        files_to_fetch = []
        if "/user" in endpoint:
            files_to_fetch.append("demo_app/main.py")
        elif "/report" in endpoint:
            files_to_fetch.append("demo_app/main.py")
        elif "/discount" in endpoint:
            files_to_fetch.append("demo_app/main.py")
        else:
            # Default to main app file
            files_to_fetch.append("demo_app/main.py")

        code_context = {}
        for file_path in files_to_fetch:
            try:
                file_content = repo.get_contents(file_path)
                content = base64.b64decode(file_content.content).decode("utf-8")
                code_context[file_path] = content
                logger.info(f"Fetched code context for {file_path}")
            except Exception as e:
                logger.warning(f"Could not fetch {file_path}: {e}")

        return code_context

    except Exception as e:
        logger.error(f"Error fetching code context: {e}")
        return {}


def _prune_logs(logs: list[dict], max_entries: int = 100) -> list[dict]:
    """
    Prune logs to avoid context window bloat.

    Uber's approach: deterministic preprocessing before the LLM sees anything.
    Strategies:
    - Keep only logs around the error timestamp
    - Remove duplicate/similar log lines (keep first + last + count)
    - Filter by log level (keep ERROR/WARN, sample INFO)
    - Truncate very long messages
    """
    if not logs:
        return []

    # Sort by timestamp
    sorted_logs = sorted(logs, key=lambda x: x.get("timestamp", ""), reverse=True)

    # Deduplicate repeated messages
    deduped_logs = []
    seen_messages = {}

    for log in sorted_logs:
        message = log.get("message", "")
        message_key = message[:100]  # Use first 100 chars as key

        if message_key in seen_messages:
            # Increment count for duplicate
            seen_messages[message_key]["count"] += 1
            seen_messages[message_key]["last_occurrence"] = log
        else:
            # New message
            seen_messages[message_key] = {
                "first": log,
                "count": 1,
                "last_occurrence": log,
            }

    # Reconstruct log list with deduplication info
    for message_key, info in seen_messages.items():
        log = info["first"].copy()

        if info["count"] > 1:
            log["message"] = f"{log.get('message', '')} " f"[repeated {info['count']} times]"
            log["dedup_count"] = info["count"]

        # Truncate very long messages
        if "message" in log and len(log["message"]) > 500:
            log["message"] = log["message"][:500] + "... (truncated)"

        deduped_logs.append(log)

    # Sort by level priority (ERROR > WARN > INFO)
    level_priority = {"ERROR": 0, "WARN": 1, "INFO": 2, "DEBUG": 3}
    deduped_logs.sort(
        key=lambda x: (
            level_priority.get(x.get("level", "INFO"), 4),
            x.get("timestamp", ""),
        )
    )

    # Take top entries
    pruned = deduped_logs[:max_entries]

    logger.info(
        f"Pruned {len(sorted_logs)} logs to {len(pruned)} "
        f"(deduped {len(sorted_logs) - len(seen_messages)} repeated messages)"
    )

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
