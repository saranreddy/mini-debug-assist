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
        state.fix_result = _generate_fix_with_llm(state, config)
        logger.info(f"Fix generated: {len(state.fix_result.changes)} changes")
    
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


def _generate_fix_with_llm(state: AgentState, config: AgentConfig) -> FixResult:
    """
    Generate fix using Claude Opus via Bedrock with MCP tool access.
    
    This would:
    1. Search the codebase for relevant code (via github MCP)
    2. Check if issue correlates with a feature flag (via appconfig MCP)
    3. Generate the fix with proper testing
    4. If flag-related, propose rollback or gated fix
    
    For production, you'd use langchain with tools and agentic loops.
    """
    import boto3
    import json
    
    bedrock = boto3.client("bedrock-runtime", region_name=config.bedrock_region)
    
    # Build prompt
    prompt = _build_fix_prompt(state)
    
    try:
        response = bedrock.invoke_model(
            modelId=config.model_fix,
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 8192,
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
        
        # Parse LLM output into FixResult
        return _parse_fix_output(content)
        
    except Exception as e:
        logger.error(f"Error generating fix with LLM: {e}")
        state.errors.append(f"Fix generation failed: {e}")
        
        return FixResult(
            fix_applied=False,
            changes=[],
        )


def _build_fix_prompt(state: AgentState) -> str:
    """Build the fix prompt from state."""
    return f"""You are a debugging agent generating a fix.

Issue: {state.issue_title}
Root Cause: {state.rca_result.root_cause if state.rca_result else 'Unknown'}

Code Context:
{_format_code_context(state.code_context)}

Generate a fix that:
1. Addresses the root cause
2. Follows best practices
3. Includes proper error handling
4. Is minimal and focused

Provide the fix as a unified diff."""


def _format_code_context(code_context: dict[str, str]) -> str:
    """Format code context for prompt."""
    return "\n\n".join([
        f"File: {file}\n{content}"
        for file, content in code_context.items()
    ])


def _parse_fix_output(content: str) -> FixResult:
    """Parse LLM output into FixResult."""
    # Simplified - production would parse structured output
    return FixResult(
        fix_applied=True,
        changes=[
            {
                "file": "unknown",
                "diff": content,
            }
        ],
    )
