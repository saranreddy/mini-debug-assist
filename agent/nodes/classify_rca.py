"""
Classify/RCA Node (LLM: Claude Sonnet, max 20 turns)

Maps to Uber's classify/RCA node:
- Categorizes the issue
- Determines if code fix is needed
- Performs initial root cause analysis
- Triggers fan-out to parallel subagents via Send

Uber uses Claude Sonnet with structured XML output and fans out to ~30 subagents.
"""

import logging

from agent.config import AgentConfig
from agent.state import AgentState, RCAResult

logger = logging.getLogger(__name__)


def classify_rca_node(state: AgentState, config: AgentConfig) -> AgentState:
    """
    Perform initial root cause analysis.

    In mock mode: returns a fixture RCA result
    In real mode: uses Claude Sonnet via Amazon Bedrock with MCP tool access

    Uber's setup:
    - Claude Sonnet, max 20 turns
    - Queries jaeger MCP, logging MCP, crash-analytics MCP, incident-data MCP
    - Fans out to ~30 parallel subagents (breadcrumbs, release correlation, etc.)
    - Outputs structured XML with category, confidence, requires_code_fix

    Note: Fan-out to subagents happens via conditional edge, not in this node.
    """
    logger.info(f"Performing initial RCA for issue {state.issue_id}")

    # Initialize turn counter
    state.turn_count["classify_rca"] = 0

    # Perform initial RCA (this becomes the primary hypothesis)
    if config.mode == "mock":
        state.rca_result = _mock_rca_result(state)
        state.turn_count["classify_rca"] = 1
        logger.info(
            f"Initial RCA (mock): {state.rca_result.category}, confidence {state.rca_result.confidence}"
        )
    else:
        rca_result, turns = _perform_rca_with_llm(state, config)
        state.rca_result = rca_result
        state.turn_count["classify_rca"] = turns
        logger.info(
            f"Initial RCA: {state.rca_result.category}, confidence {state.rca_result.confidence}, turns={turns}"
        )

    logger.info("RCA complete, will fan out to subagents next")
    return state


def _mock_rca_result(state: AgentState) -> RCAResult:
    """
    Generate mock RCA result based on issue data.

    For learning: demonstrates what the LLM would produce.
    """
    issue_data = state.issue_data
    exception_type = issue_data.get("exception_type", "")

    if exception_type == "KeyError":
        return RCAResult(
            category="code_bug",
            requires_code_fix=True,
            confidence=0.95,
            root_cause=(
                "KeyError accessing user['email'] field. "
                "User data in USERS_DB is inconsistent - user '3' has no 'email' field. "
                "The code assumes all users have an email, which is not validated."
            ),
            summary=(
                "The /user/{user_id} endpoint crashes with KeyError when accessing "
                "user data that lacks the 'email' field. This is a data schema violation "
                "that should be handled gracefully."
            ),
            evidence=[
                {
                    "type": "stack_trace",
                    "content": issue_data.get("stack_trace", ""),
                },
                {
                    "type": "log",
                    "content": "Multiple occurrences when accessing user_id='3'",
                },
                {
                    "type": "code_analysis",
                    "content": "Line 156: email = user['email'] - no default or validation",
                },
            ],
        )

    # Default for other exception types
    return RCAResult(
        category="code_bug",
        requires_code_fix=True,
        confidence=0.7,
        root_cause="Exception detected in logs, requires investigation",
        summary=f"{exception_type} occurred in {issue_data.get('service', 'unknown service')}",
        evidence=[],
    )


def _perform_rca_with_llm(state: AgentState, config: AgentConfig) -> tuple[RCAResult, int]:
    """
    Perform RCA using Claude Sonnet via Bedrock with bounded tool-use loop.

    Uses Bedrock Converse API with:
    - Tool calling for code search, log queries, etc.
    - Structured JSON output validated with Pydantic
    - Bounded turns (max_turns_classify)
    - Retry on parse failure
    - Low-confidence fallback that triggers escalation
    """
    import json

    from agent.llm import invoke_with_tools, parse_rca_result

    # Build system prompt
    system_prompt = """You are a debugging assistant analyzing production errors.

Analyze the provided logs, traces, and code to determine the root cause.

You have access to tools:
- search_code: Search for code patterns
- query_logs: Query CloudWatch Logs
- get_recent_commits: Check recent code changes

Once you've gathered enough context, return your analysis in JSON format inside <result> tags:

<result>
{
  "category": "code_bug" | "third_party" | "infra" | "network",
  "requires_code_fix": true | false,
  "confidence": 0.0 to 1.0,
  "root_cause": "Brief description of the root cause",
  "summary": "One-line summary",
  "evidence": [{"type": "string", "content": "string"}]
}
</result>"""

    # Build initial message with context
    user_message = f"""Issue: {state.issue_title}

Exception: {state.issue_data.get('exception_type', 'Unknown')} - {state.issue_data.get('exception_message', '')}

Stack Trace:
{state.issue_data.get('stack_trace', 'N/A')}

Logs (recent errors):
{json.dumps(state.logs[:10], indent=2) if state.logs else "No logs available"}

Traces (failed requests):
{json.dumps(state.traces[:5], indent=2) if state.traces else "No traces available"}

Code context:
{json.dumps({k: v[:500] + "..." for k, v in state.code_context.items()}, indent=2) if state.code_context else "No code context"}

Analyze this error and determine the root cause."""

    messages = [
        {
            "role": "user",
            "content": [{"text": user_message}],
        }
    ]

    # Define tools (simplified for demonstration)
    tools = [
        {
            "toolSpec": {
                "name": "search_code",
                "description": "Search for code patterns in the repository",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "pattern": {
                                "type": "string",
                                "description": "Code pattern to search for",
                            },
                            "file_path": {
                                "type": "string",
                                "description": "Optional file path to search in",
                            },
                        },
                        "required": ["pattern"],
                    }
                },
            }
        },
        {
            "toolSpec": {
                "name": "query_logs",
                "description": "Query CloudWatch Logs for additional context",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Log query string"},
                            "time_range_minutes": {
                                "type": "integer",
                                "description": "How far back to search",
                            },
                        },
                        "required": ["query"],
                    }
                },
            }
        },
    ]

    # Invoke with bounded tool-use loop
    try:
        llm_output, turns = invoke_with_tools(
            model_id=config.model_classify,
            region=config.bedrock_region,
            system_prompt=system_prompt,
            messages=messages,
            tools=tools,
            max_turns=config.max_turns_classify,
            node_name="classify_rca",
            response_schema=RCAResult,
        )

        # Parse result with retry capability
        def retry_parse():
            retry_msg = {
                "role": "user",
                "content": [
                    {"text": "Please format your response as valid JSON inside <result> tags."}
                ],
            }
            messages.append(retry_msg)
            retry_output, _ = invoke_with_tools(
                model_id=config.model_classify,
                region=config.bedrock_region,
                system_prompt=system_prompt,
                messages=messages,
                tools=None,  # No tools on retry
                max_turns=1,
                node_name="classify_rca_retry",
                response_schema=RCAResult,
            )
            return retry_output

        parsed_result = parse_rca_result(llm_output, retry_fn=retry_parse)

        # Convert to RCAResult (it's already a dict with all fields)
        result = RCAResult(**parsed_result)
        return result, turns

    except Exception as e:
        logger.error(f"Error in RCA analysis: {e}", exc_info=True)
        # Fallback: low confidence triggers escalation
        return (
            RCAResult(
                category="unknown",
                requires_code_fix=False,
                confidence=0.3,
                root_cause=f"Analysis error: {str(e)[:100]}",
                summary="RCA failed - requires manual review",
                evidence=[],
            ),
            1,
        )
