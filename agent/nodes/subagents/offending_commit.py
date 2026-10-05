"""
Offending Commit Finder Subagent

Uses git blame and recent commits to find which change introduced the bug.
Maps to Uber's offending-diff subagent.
"""

import logging
from typing import Any

from agent.config import AgentConfig
from agent.state import SubagentResult

logger = logging.getLogger(__name__)


# Turn cap for this subagent
MAX_TURNS = 5


def offending_commit_subagent(state_dict: dict[str, Any], config: AgentConfig) -> dict[str, Any]:
    """
    Find the commit that introduced the bug.

    In mock mode: uses fixture commit data
    In real mode: would use GitHub MCP to blame and search recent commits

    Returns:
        Updated state dict with subagent result
    """
    logger.info("Running offending commit subagent")

    issue_data = state_dict["issue_data"]
    code_context = state_dict.get("code_context", {})

    if config.mode == "mock":
        result = _mock_commit_analysis(issue_data)
        turns = 1
    else:
        result, turns = _find_commit_with_github_mcp(issue_data, code_context, config)

    # Add result to subagent_results
    subagent_results = state_dict.get("subagent_results", {})
    subagent_results["offending_commit"] = result

    # Track turns
    turn_count = state_dict.get("turn_count", {})
    turn_count["subagent_offending_commit"] = turns

    return {
        "subagent_results": subagent_results,
        "turn_count": turn_count,
    }


def _mock_commit_analysis(issue_data: dict) -> SubagentResult:
    """
    Mock commit analysis.

    Simulates git blame on the error line.
    """
    commit_sha = issue_data.get("commit_sha", "abc123def456")
    exception_type = issue_data.get("exception_type", "")

    if exception_type == "KeyError":
        hypothesis = (
            f"Likely introduced in commit {commit_sha[:7]}. "
            "The buggy line (user['email']) has been present since initial implementation. "
            "Not a recent regression, but a long-standing data validation issue."
        )
        confidence = 0.70
        evidence = [
            f"git blame: line 156 last modified in {commit_sha[:7]}",
            "Commit message: 'Add user endpoint'",
            "No recent changes to this code path",
            "Bug was dormant until user '3' was added to database",
        ]
    else:
        hypothesis = f"Unable to pinpoint exact commit for {exception_type}"
        confidence = 0.5
        evidence = [
            "Code area stable for 6+ months",
            "May be data or config related rather than code change",
        ]

    return SubagentResult(
        subagent_type="offending_commit",
        hypothesis=hypothesis,
        confidence=confidence,
        evidence=evidence,
        supporting_data={
            "commits_analyzed": 10,
            "suspected_sha": commit_sha,
        },
    )


def _find_commit_with_github_mcp(
    issue_data: dict, code_context: dict, config: AgentConfig
) -> SubagentResult:
    """
    Find offending commit using bounded tool-use.

    Analyzes recent commits and correlates with error timeline.
    """
    import json

    from agent.nodes.subagents.common import invoke_subagent_llm

    system_prompt = """You are a commit analyzer finding the bug introduction.

Use git blame and recent commits to identify which change caused the bug.

Return your analysis in JSON format inside <result> tags:

<result>
{
  "hypothesis": "Which commit likely introduced the bug",
  "confidence": 0.0 to 1.0,
  "evidence": ["List of supporting evidence from commits"]
}
</result>"""

    user_message = f"""Issue: {issue_data.get('exception_type', 'Unknown')}
Stack Trace: {issue_data.get('stack_trace', 'N/A')[:500]}

Code Context:
{json.dumps({k: v[:200] for k, v in code_context.items()}, indent=2) if code_context else "No code context"}

Find which recent commit introduced this bug."""

    return invoke_subagent_llm(
        subagent_type="offending_commit",
        system_prompt=system_prompt,
        user_message=user_message,
        config=config,
    )
