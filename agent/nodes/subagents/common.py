"""
Common utilities for subagents.

Provides shared LLM invocation pattern.
"""

import logging
from typing import Any

from agent.config import AgentConfig
from agent.llm import _extract_json_from_text, invoke_with_tools
from agent.state import SubagentResult

logger = logging.getLogger(__name__)


def invoke_subagent_llm(
    subagent_type: str,
    system_prompt: str,
    user_message: str,
    config: AgentConfig,
) -> tuple[SubagentResult, int]:
    """
    Common pattern for subagent LLM invocation.

    Args:
        subagent_type: Type of subagent (breadcrumbs, flag_correlation, offending_commit)
        system_prompt: System prompt for this subagent
        user_message: User message with issue data
        config: Agent configuration

    Returns:
        (SubagentResult, turns)
    """
    messages = [
        {
            "role": "user",
            "content": [{"text": user_message}],
        }
    ]

    # Subagents get minimal tools for focused analysis
    tools = [
        {
            "toolSpec": {
                "name": "search_code",
                "description": "Search for code patterns",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "pattern": {"type": "string"},
                        },
                        "required": ["pattern"],
                    }
                },
            }
        },
    ]

    try:
        llm_output, turns = invoke_with_tools(
            model_id=config.model_subagent,
            region=config.bedrock_region,
            system_prompt=system_prompt,
            messages=messages,
            tools=tools,
            max_turns=config.max_turns_subagent,
            node_name=f"subagent_{subagent_type}",
        )

        # Parse result
        result_dict = _parse_subagent_result(llm_output, subagent_type)
        result = SubagentResult(**result_dict)

        return result, turns

    except Exception as e:
        logger.error(f"Error in {subagent_type} subagent: {e}", exc_info=True)
        return (
            SubagentResult(
                subagent_type=subagent_type,
                hypothesis="Analysis failed",
                confidence=0.2,
                evidence=[str(e)],
            ),
            1,
        )


def _parse_subagent_result(llm_output: dict[str, Any], subagent_type: str) -> dict[str, Any]:
    """Parse subagent result from LLM output."""
    # If already structured
    if "hypothesis" in llm_output:
        return {**llm_output, "subagent_type": subagent_type}

    # If forced (hit turn cap)
    if llm_output.get("forced"):
        return {
            "subagent_type": subagent_type,
            "hypothesis": "Analysis incomplete (turn limit reached)",
            "confidence": 0.3,
            "evidence": [],
        }

    # Try to parse from text
    text = llm_output.get("text", "")

    try:
        parsed = _extract_json_from_text(text)
        if parsed:
            return {
                "subagent_type": subagent_type,
                "hypothesis": parsed.get("hypothesis", "Unknown"),
                "confidence": parsed.get("confidence", 0.5),
                "evidence": parsed.get("evidence", []),
            }
    except Exception as e:
        logger.warning(f"Failed to parse subagent result: {e}")

    # Fallback: extract from text
    return {
        "subagent_type": subagent_type,
        "hypothesis": text[:200] if text else "No analysis",
        "confidence": 0.5,
        "evidence": [text[:500]] if text else [],
    }
