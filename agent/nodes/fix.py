"""
Fix Node (LLM: Claude Opus, max 30-50 turns)

Maps to Uber's fix node:
- Searches codebase via MCP Sourcegraph
- If strong correlation with feature flag, proposes rollback via flipr-mcp
- Generates code fix, gated behind a feature flag
- May require human approval for certain actions

Uber uses Claude Opus (more capable) with higher turn cap.
"""

import logging
from typing import Any

from agent.config import AgentConfig
from agent.state import AgentState, FixResult

logger = logging.getLogger(__name__)


def fix_node(state: AgentState, config: AgentConfig) -> AgentState:
    """
    Generate fix for the issue.
    
    In mock mode: returns a fixture fix
    In real mode: uses Claude Opus via Bedrock with MCP tool access
    
    Uber's setup:
    - Claude Opus, max 30-50 turns
    - Access to Sourcegraph MCP for code search
    - Access to flipr-mcp for feature flag operations
    - Fixes are gated behind new feature flags
    """
    logger.info(f"Generating fix for issue {state.issue_id}")
    
    # Initialize turn counter
    state.turn_count["fix"] = 0
    
    if config.mode == "mock":
        # Mock mode: return fixture fix
        state.fix_result = _mock_fix_result(state)
        logger.info(f"Fix generated (mock): {len(state.fix_result.changes)} changes")
    else:
        # Real mode: use Bedrock with MCP tools
        fix_result, turns = _generate_fix_with_llm(state, config)
        state.fix_result = fix_result
        state.turn_count["fix"] = turns
        logger.info(f"Fix generated: {len(state.fix_result.changes)} changes, turns={turns}")
    
    return state


def _mock_fix_result(state: AgentState) -> FixResult:
    """
    Generate mock fix based on RCA result.
    
    For learning: demonstrates what the LLM would produce.
    """
    if state.rca_result is None:
        return FixResult(
            fix_applied=False,
            changes=[],
        )
    
    # Generate fix based on issue type
    issue_type = state.issue_data.get("exception_type", "")
    
    if issue_type == "KeyError":
        return FixResult(
            fix_applied=True,
            changes=[
                {
                    "file": "demo_app/main.py",
                    "diff": """--- a/demo_app/main.py
+++ b/demo_app/main.py
@@ -153,7 +153,10 @@ async def get_user(user_id: str):
     
     user = USERS_DB[user_id]
     
-    # BUG: This assumes 'email' always exists
-    email = user["email"]  # KeyError when user_id='3'
+    # Fixed: Use .get() with default value to handle missing email field
+    # This prevents KeyError when user data is incomplete
+    email = user.get("email", None)
     
     logger.info(f"User {user_id} retrieved successfully")
""",
                }
            ],
            mitigation=None,
            requires_approval=False,
        )
    
    # Default fix
    return FixResult(
        fix_applied=True,
        changes=[],
    )


def _generate_fix_with_llm(state: AgentState, config: AgentConfig) -> tuple[FixResult, int]:
    """
    Generate fix using Claude Opus via Bedrock with bounded tool-use loop.
    
    Uses Opus for more capable code generation:
    1. Searches codebase via github MCP/tools
    2. Generates unified diff patches
    3. Applies to temp checkout for validation
    4. Returns FixResult with real diffs
    """
    from agent.llm import invoke_with_tools
    import json
    import tempfile
    import subprocess
    import os
    
    # Build system prompt
    system_prompt = """You are a code fixing assistant.

Generate a minimal fix that addresses the root cause.

Tools available:
- read_file: Read source files
- search_code: Search for code patterns
- get_recent_commits: Check recent changes

Once you've analyzed the code, return your fix in JSON format inside <result> tags:

<result>
{
  "fix_applied": true,
  "changes": [
    {
      "file": "path/to/file.py",
      "diff": "unified diff format starting with --- and +++"
    }
  ],
  "mitigation": "optional mitigation strategy",
  "requires_approval": false
}
</result>

The diff should be a valid unified diff that can be applied with `patch`."""
    
    # Build initial message with previous attempt feedback
    previous_attempts_text = ""
    if state.fix_history:
        previous_attempts_text = "\n\n**PREVIOUS FIX ATTEMPTS (FAILED):**\n"
        for i, attempt in enumerate(state.fix_history, 1):
            previous_attempts_text += f"\n--- Attempt {i} ---\n"
            previous_attempts_text += f"Diff:\n{attempt.get('diff', 'N/A')[:500]}\n\n"
            previous_attempts_text += f"Failure Reason:\n{attempt.get('failure_reason', 'N/A')[:500]}\n"
            if attempt.get('test_output'):
                previous_attempts_text += f"Test Output:\n{attempt.get('test_output')[:500]}\n"
    
    user_message = f"""Issue: {state.issue_title}

Root Cause: {state.rca_result.root_cause if state.rca_result else 'Unknown'}
Confidence: {state.rca_result.confidence if state.rca_result else 0}

Code Context:
{json.dumps({k: v[:1000] + "..." for k, v in state.code_context.items()}, indent=2) if state.code_context else "No code context"}
{previous_attempts_text}

Generate a fix for this issue. {" **Learn from previous failures above.**" if state.fix_history else ""}"""
    
    messages = [
        {
            "role": "user",
            "content": [{"text": user_message}],
        }
    ]
    
    # Define tools
    tools = [
        {
            "toolSpec": {
                "name": "read_file",
                "description": "Read a source file",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "File path to read"},
                        },
                        "required": ["path"]
                    }
                }
            }
        },
        {
            "toolSpec": {
                "name": "search_code",
                "description": "Search for code patterns",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "pattern": {"type": "string", "description": "Pattern to search for"},
                        },
                        "required": ["pattern"]
                    }
                }
            }
        },
    ]
    
    try:
        # Invoke with bounded tool-use loop
        llm_output, turns = invoke_with_tools(
            model_id=config.model_fix,
            region=config.bedrock_region,
            system_prompt=system_prompt,
            messages=messages,
            tools=tools,
            max_turns=config.max_turns_fix,
            node_name="fix",
        )
        
        # Parse fix result
        fix_result = _parse_fix_result(llm_output)
        
        # Apply fix to temp checkout to generate real unified diff
        if fix_result.get("fix_applied") and fix_result.get("changes"):
            fix_result["changes"] = _apply_and_generate_diffs(fix_result["changes"])
        
        result = FixResult(**fix_result)
        return result, turns
        
    except Exception as e:
        logger.error(f"Error generating fix: {e}", exc_info=True)
        return FixResult(
            fix_applied=False,
            changes=[],
        ), 1


def _parse_fix_result(llm_output: dict[str, Any]) -> dict[str, Any]:
    """Parse fix result from LLM output."""
    from agent.llm import _extract_json_from_text
    
    # If already structured
    if "fix_applied" in llm_output:
        return llm_output
    
    # If forced (hit turn cap)
    if llm_output.get("forced"):
        return {
            "fix_applied": False,
            "changes": [],
            "mitigation": "Fix generation incomplete (turn limit reached)",
            "requires_approval": True,
        }
    
    # Try to parse from text
    text = llm_output.get("text", "")
    
    try:
        parsed = _extract_json_from_text(text)
        if parsed:
            # Ensure required fields
            return {
                "fix_applied": parsed.get("fix_applied", False),
                "changes": parsed.get("changes", []),
                "mitigation": parsed.get("mitigation"),
                "requires_approval": parsed.get("requires_approval", False),
            }
    except Exception as e:
        logger.warning(f"Failed to parse fix result: {e}")
    
    # Fallback: no fix
    return {
        "fix_applied": False,
        "changes": [],
        "mitigation": "Parse failure",
        "requires_approval": True,
    }


def _apply_and_generate_diffs(changes: list[dict[str, str]]) -> list[dict[str, str]]:
    """
    Apply changes to temp checkout and generate real unified diffs.
    
    For demonstration, returns the changes as-is.
    In production, would:
    1. Clone/checkout to temp directory
    2. Apply proposed changes
    3. Run `git diff` to generate real unified diffs
    """
    # For now, return changes as-is
    # Real implementation would apply to temp git checkout
    return changes
