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
from typing import Any

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
        logger.info(f"Initial RCA (mock): {state.rca_result.category}, confidence {state.rca_result.confidence}")
    else:
        state.rca_result = _perform_rca_with_llm(state, config)
        logger.info(f"Initial RCA: {state.rca_result.category}, confidence {state.rca_result.confidence}")
    
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


def _perform_rca_with_llm(state: AgentState, config: AgentConfig) -> RCAResult:
    """
    Perform RCA using Claude Sonnet via Bedrock with MCP tool access.
    
    This is where the real LLM interaction happens:
    1. Build prompt with issue data, logs, traces, code context
    2. Give the LLM access to MCP tools (cloudwatch, xray, github)
    3. Let it analyze up to max_turns_classify turns
    4. Parse structured output into RCAResult
    
    For production implementation, you'd use langchain-aws BedrockChat
    with tools bound via MCP servers.
    """
    import boto3
    import json
    
    # Initialize Bedrock client
    bedrock = boto3.client("bedrock-runtime", region_name=config.bedrock_region)
    
    # Build prompt
    prompt = _build_rca_prompt(state)
    
    # Invoke model (simplified - real version would use langchain with tools)
    try:
        response = bedrock.invoke_model(
            modelId=config.model_classify,
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 4096,
                "messages": [
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
            }),
        )
        
        result = json.loads(response["body"].read())
        content = result["content"][0]["text"]
        
        # Parse LLM output into RCAResult
        return _parse_rca_output(content)
        
    except Exception as e:
        logger.error(f"Error performing RCA with LLM: {e}")
        state.errors.append(f"RCA failed: {e}")
        
        # Return low-confidence result
        return RCAResult(
            category="unknown",
            requires_code_fix=False,
            confidence=0.0,
            root_cause=f"RCA failed: {e}",
            summary="Unable to perform automatic RCA",
        )


def _build_rca_prompt(state: AgentState) -> str:
    """Build the RCA prompt from state."""
    return f"""You are a debugging agent performing root cause analysis.

Issue: {state.issue_title}
Exception: {state.issue_data.get('exception_type')} - {state.issue_data.get('exception_message')}

Stack Trace:
{state.issue_data.get('stack_trace', 'N/A')}

Recent Logs:
{_format_logs(state.logs)}

Code Context:
{_format_code_context(state.code_context)}

Analyze this issue and provide:
1. Category (code_bug, third_party, infra, network)
2. Whether a code fix is required
3. Confidence level (0.0-1.0)
4. Root cause explanation
5. Summary

Output in structured format."""


def _format_logs(logs: list[dict]) -> str:
    """Format logs for prompt."""
    return "\n".join([
        f"[{log.get('timestamp')}] {log.get('level')}: {log.get('message')}"
        for log in logs[:10]  # Limit to avoid context bloat
    ])


def _format_code_context(code_context: dict[str, str]) -> str:
    """Format code context for prompt."""
    return "\n\n".join([
        f"File: {file}\n{content}"
        for file, content in code_context.items()
    ])


def _parse_rca_output(content: str) -> RCAResult:
    """
    Parse LLM output into RCAResult.
    
    In production, you'd use structured output or XML parsing.
    For simplicity, we'll do basic text parsing.
    """
    # Simplified parsing - production would use structured output
    return RCAResult(
        category="code_bug",
        requires_code_fix=True,
        confidence=0.8,
        root_cause=content[:500],  # Truncate for demo
        summary=content[:200],
    )
