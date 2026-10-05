"""
Flag/Config Correlation Subagent

Checks if the issue correlates with feature flag changes or config updates.
Maps to Uber's release correlation subagent.
"""

import logging
from typing import Any

from agent.config import AgentConfig
from agent.state import SubagentResult

logger = logging.getLogger(__name__)


# Turn cap for this subagent
MAX_TURNS = 5


def flag_correlation_subagent(state_dict: dict[str, Any], config: AgentConfig) -> dict[str, Any]:
    """
    Analyze correlation between issue and feature flags.
    
    In mock mode: checks fixture data for flag correlation
    In real mode: would query AppConfig and compare timelines
    
    Returns:
        Updated state dict with subagent result
    """
    logger.info("Running flag correlation subagent")
    
    issue_data = state_dict["issue_data"]
    
    if config.mode == "mock":
        result = _mock_flag_correlation(issue_data)
        turns = 1
    else:
        result, turns = _analyze_flag_correlation_with_mcp(issue_data, config)
    
    # Add result to subagent_results
    subagent_results = state_dict.get("subagent_results", {})
    subagent_results["flag_correlation"] = result
    
    # Track turns
    turn_count = state_dict.get("turn_count", {})
    turn_count["subagent_flag_correlation"] = turns
    
    return {
        "subagent_results": subagent_results,
        "turn_count": turn_count,
    }


def _mock_flag_correlation(issue_data: dict) -> SubagentResult:
    """
    Mock flag correlation analysis.
    
    Checks if error correlates with a feature flag.
    """
    exception_type = issue_data.get("exception_type", "")
    
    # Check if it's a division by zero (likely flag-related)
    if "division" in issue_data.get("exception_message", "").lower() or \
       "ZeroDivisionError" in exception_type:
        hypothesis = (
            "Strong correlation with DISCOUNT_V2 flag. "
            "Error pattern matches new discount algorithm. "
            "Recommend rollback while code fix is developed."
        )
        confidence = 0.95
        evidence = [
            "DISCOUNT_V2 flag enabled 2 hours before first error",
            "95% of errors occur when flag=on",
            "0% error rate when flag=off",
            "Stack trace in flag-gated code path",
        ]
    elif exception_type == "KeyError":
        # No flag correlation
        hypothesis = (
            "No correlation with feature flags found. "
            "Issue appears to be data schema related, not config-driven."
        )
        confidence = 0.75
        evidence = [
            "No recent flag changes in past 24 hours",
            "Error occurs consistently regardless of flags",
            "Data validation issue, not behavior toggle",
        ]
    else:
        hypothesis = "Insufficient data to determine flag correlation"
        confidence = 0.5
        evidence = ["No clear flag pattern identified"]
    
    return SubagentResult(
        subagent_type="flag_correlation",
        hypothesis=hypothesis,
        confidence=confidence,
        evidence=evidence,
        supporting_data={
            "flags_checked": ["DISCOUNT_V2", "EXPERIMENTAL_CACHE"],
        }
    )


def _analyze_flag_correlation_with_mcp(
    issue_data: dict,
    config: AgentConfig
) -> SubagentResult:
    """
    Analyze flag correlation using bounded tool-use.
    
    Checks for correlation with feature flag changes or config updates.
    """
    from agent.nodes.subagents.common import invoke_subagent_llm
    import json
    
    system_prompt = """You are a feature flag correlation analyzer.

Check if the error correlates with recent feature flag or config changes.

Return your analysis in JSON format inside <result> tags:

<result>
{
  "hypothesis": "Is this correlated with a flag/config change",
  "confidence": 0.0 to 1.0,
  "evidence": ["List of supporting evidence"]
}
</result>"""
    
    user_message = f"""Issue: {issue_data.get('exception_type', 'Unknown')}
Timestamp: {issue_data.get('timestamp', 'Unknown')}

Recent Config/Flag Context:
{json.dumps(issue_data.get('recent_changes', []), indent=2)}

Check for correlation with feature flags or config changes."""
    
    return invoke_subagent_llm(
        subagent_type="flag_correlation",
        system_prompt=system_prompt,
        user_message=user_message,
        config=config,
    )
