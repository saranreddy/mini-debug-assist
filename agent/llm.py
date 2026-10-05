"""
LLM interaction utilities with bounded tool-use loops.

Implements Bedrock Converse API with tool calling, inspired by Uber's
bounded inference approach.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import boto3
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)


class ToolResult(BaseModel):
    """Result from tool execution."""

    tool_use_id: str
    content: list[dict[str, Any]]
    is_error: bool = False


class LLMResponse(BaseModel):
    """Structured LLM response."""

    content: str
    stop_reason: str
    tool_use: list[dict[str, Any]] | None = None


def invoke_with_tools(
    model_id: str,
    region: str,
    system_prompt: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    max_turns: int = 5,
    node_name: str = "unknown",
    response_schema: type[BaseModel] | None = None,
) -> tuple[dict[str, Any], int]:
    """
    Invoke LLM with bounded tool-use loop.

    Args:
        model_id: Bedrock model ID (e.g. inference profile)
        region: AWS region
        system_prompt: System prompt
        messages: Conversation history
        tools: Tool definitions (Bedrock toolConfig format)
        max_turns: Maximum tool-use turns
        node_name: Node name for logging
        response_schema: Optional Pydantic model for structured output

    Returns:
        (final_response_dict, turn_count)
    """
    client = boto3.client("bedrock-runtime", region_name=region)

    conversation = messages.copy()
    turns = 0

    while turns < max_turns:
        turns += 1

        try:
            # Build request
            request_params = {
                "modelId": model_id,
                "system": [{"text": system_prompt}],
                "messages": conversation,
                "inferenceConfig": {
                    "maxTokens": 4096,
                    "temperature": 0.0,  # Deterministic for debugging
                },
            }

            # Add tools if provided
            if tools:
                request_params["toolConfig"] = {"tools": tools}

            logger.info(f"{node_name}: Turn {turns}/{max_turns}, invoking {model_id}")

            response = client.converse(**request_params)

            # Parse response
            output = response["output"]
            stop_reason = response.get("stopReason", "end_turn")

            if "message" not in output:
                logger.error(f"{node_name}: No message in response")
                break

            message = output["message"]
            conversation.append(message)

            # Check stop reason
            if stop_reason == "end_turn":
                # Model finished, extract final answer
                return _extract_final_answer(message, response_schema, node_name), turns

            elif stop_reason == "tool_use":
                # Model wants to use tools
                tool_results = _execute_tools(message.get("content", []))

                if not tool_results:
                    logger.warning(f"{node_name}: No tool results, forcing final answer")
                    return _force_final_answer(conversation, node_name), turns

                # Add tool results to conversation
                conversation.append({"role": "user", "content": tool_results})

                # Continue loop
                continue

            elif stop_reason == "max_tokens":
                logger.warning(f"{node_name}: Hit max tokens")
                return _force_final_answer(conversation, node_name), turns

            else:
                logger.warning(f"{node_name}: Unexpected stop reason: {stop_reason}")
                return _force_final_answer(conversation, node_name), turns

        except Exception as e:
            logger.error(f"{node_name}: Error in turn {turns}: {e}", exc_info=True)
            return _force_final_answer(conversation, node_name), turns

    # Hit max turns
    logger.warning(f"{node_name}: Hit max turns ({max_turns}), forcing final answer")
    return _force_final_answer(conversation, node_name), turns


def _execute_tools(content_blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Execute tool use requests via MCP.

    Calls tools through the MCP client, which manages server lifecycle
    and tool execution.
    """
    from agent.mcp_client import get_mcp_client

    mcp_client = get_mcp_client()
    tool_results = []

    for block in content_blocks:
        if block.get("toolUse"):
            tool_use = block["toolUse"]
            tool_use_id = tool_use["toolUseId"]
            tool_name = tool_use["name"]
            tool_input = tool_use.get("input", {})

            logger.info(f"Executing tool: {tool_name} with input: {tool_input}")

            try:
                # Call tool via MCP
                result = mcp_client.call_tool(tool_name, tool_input)

                # Format result for Bedrock
                result_text = json.dumps(result, indent=2)
                result_content = {"type": "text", "text": result_text}

                tool_results.append(
                    {"toolResult": {"toolUseId": tool_use_id, "content": [result_content]}}
                )

            except Exception as e:
                logger.error(f"Error executing tool {tool_name}: {e}")
                # Return error result
                error_content = {
                    "type": "text",
                    "text": json.dumps({"error": str(e), "success": False}),
                }
                tool_results.append(
                    {
                        "toolResult": {
                            "toolUseId": tool_use_id,
                            "content": [error_content],
                            "status": "error",
                        }
                    }
                )

    return tool_results


def _extract_final_answer(
    message: dict[str, Any],
    response_schema: type[BaseModel] | None,
    node_name: str,
) -> dict[str, Any]:
    """Extract final answer from message content."""
    content_blocks = message.get("content", [])

    # Concatenate all text blocks
    text_parts = []
    for block in content_blocks:
        if block.get("text"):
            text_parts.append(block["text"])

    full_text = "\n".join(text_parts)

    # Try to parse as JSON if schema provided
    if response_schema:
        try:
            # Look for JSON in text
            parsed = _extract_json_from_text(full_text)
            if parsed:
                # Validate with Pydantic
                validated = response_schema.model_validate(parsed)
                return validated.model_dump()
        except (json.JSONDecodeError, ValidationError) as e:
            logger.warning(f"{node_name}: Failed to parse structured output: {e}")
            # Fall through to return raw text

    return {"text": full_text, "raw_message": message}


def _extract_json_from_text(text: str) -> dict[str, Any] | None:
    """Extract JSON from text (handles XML tags, code blocks, etc.)."""
    import re

    # Try direct JSON parse
    try:
        return json.loads(text.strip())
    except:
        pass

    # Try to find JSON in XML tags
    xml_match = re.search(r"<result>(.*?)</result>", text, re.DOTALL)
    if xml_match:
        try:
            return json.loads(xml_match.group(1).strip())
        except:
            pass

    # Try to find JSON in code blocks
    code_block_match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if code_block_match:
        try:
            return json.loads(code_block_match.group(1).strip())
        except:
            pass

    return None


def _force_final_answer(conversation: list[dict[str, Any]], node_name: str) -> dict[str, Any]:
    """
    Force a final answer when max turns hit or error.

    Returns a low-confidence result that triggers escalation.
    """
    logger.warning(f"{node_name}: Forcing final answer with low confidence")

    return {
        "text": "Unable to complete analysis within turn limit",
        "forced": True,
        "confidence": 0.3,  # Low confidence triggers escalation
    }


def parse_rca_result(
    llm_output: dict[str, Any],
    retry_fn: callable | None = None,
) -> dict[str, Any]:
    """
    Parse RCA result from LLM output.

    Args:
        llm_output: Output from invoke_with_tools
        retry_fn: Optional function to retry on parse failure

    Returns:
        RCA result dict with required fields
    """
    from agent.state import RCAResult

    # If already structured with required fields
    if "category" in llm_output and "confidence" in llm_output:
        # Ensure all required fields exist
        return _ensure_required_fields(llm_output)

    # If forced (hit turn cap)
    if llm_output.get("forced"):
        return {
            "category": "unknown",
            "requires_code_fix": False,
            "confidence": llm_output.get("confidence", 0.3),
            "root_cause": "Analysis incomplete (turn limit reached)",
            "summary": "Incomplete analysis",
            "evidence": [],
        }

    # Try to parse from text
    text = llm_output.get("text", "")

    # Try structured extraction
    try:
        parsed = _extract_json_from_text(text)
        if parsed:
            # Ensure required fields
            parsed = _ensure_required_fields(parsed)
            # Validate by constructing RCAResult (it's a dataclass, not Pydantic)
            from dataclasses import asdict

            result = RCAResult(**parsed)
            return asdict(result)
    except Exception as e:
        logger.warning(f"Failed to parse RCA result: {e}")

    # Retry once if provided
    if retry_fn:
        logger.info("Retrying RCA parse with clarification")
        try:
            retry_output = retry_fn()
            return parse_rca_result(retry_output, retry_fn=None)
        except:
            pass

    # Fallback: low confidence result
    logger.warning("Falling back to low-confidence RCA result")
    return {
        "category": "unknown",
        "requires_code_fix": False,
        "confidence": 0.3,
        "root_cause": "Unable to determine (parse failure)",
        "summary": "Parse failure",
        "evidence": [],
    }


def _ensure_required_fields(data: dict[str, Any]) -> dict[str, Any]:
    """Ensure all required RCA fields exist."""
    defaults = {
        "category": "unknown",
        "requires_code_fix": False,
        "confidence": 0.5,
        "root_cause": "Not specified",
        "summary": "Not specified",
        "evidence": [],
    }
    result = defaults.copy()
    result.update(data)
    return result
