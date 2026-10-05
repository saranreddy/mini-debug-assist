"""
Breadcrumbs/Log Timeline Subagent

Analyzes the sequence of user actions and log events leading to the crash.
Maps to Uber's breadcrumb analyzer subagent.
"""

import logging
from typing import Any

from agent.config import AgentConfig
from agent.state import SubagentResult

logger = logging.getLogger(__name__)


# Turn cap for this subagent
MAX_TURNS = 5


def breadcrumbs_subagent(state_dict: dict[str, Any], config: AgentConfig) -> dict[str, Any]:
    """
    Analyze breadcrumbs and log timeline.
    
    In mock mode: analyzes fixture breadcrumbs
    In real mode: would use Claude to analyze log sequence
    
    Returns:
        Updated state dict with subagent result
    """
    logger.info("Running breadcrumbs subagent")
    
    issue_data = state_dict["issue_data"]
    logs = state_dict.get("logs", [])
    
    if config.mode == "mock":
        result = _mock_breadcrumb_analysis(issue_data, logs)
        turns = 1
    else:
        result, turns = _analyze_breadcrumbs_with_llm(issue_data, logs, config)
    
    # Add result to subagent_results
    subagent_results = state_dict.get("subagent_results", {})
    subagent_results["breadcrumbs"] = result
    
    # Track turns
    turn_count = state_dict.get("turn_count", {})
    turn_count["subagent_breadcrumbs"] = turns
    
    return {
        "subagent_results": subagent_results,
        "turn_count": turn_count,
    }


def _mock_breadcrumb_analysis(issue_data: dict, logs: list) -> SubagentResult:
    """
    Mock breadcrumb analysis.
    
    Examines the sequence of events leading to the crash.
    """
    breadcrumbs = issue_data.get("breadcrumbs", [])
    exception_type = issue_data.get("exception_type", "")
    
    # Analyze pattern
    if exception_type == "KeyError":
        # Successful requests followed by crash
        hypothesis = (
            "User successfully accessed /user/1, then crashed on /user/3. "
            "Suggests data inconsistency rather than code logic error. "
            "User '3' likely has different schema than user '1'."
        )
        confidence = 0.85
        evidence = [
            "Successful GET /user/1 → 200 OK",
            "Failed GET /user/3 → 500 Error (KeyError)",
            "Pattern suggests schema variation between users",
        ]
    else:
        hypothesis = f"Log timeline shows {exception_type} occurred after normal operations"
        confidence = 0.6
        evidence = [f"Breadcrumbs: {len(breadcrumbs)} events logged"]
    
    return SubagentResult(
        subagent_type="breadcrumbs",
        hypothesis=hypothesis,
        confidence=confidence,
        evidence=evidence,
        supporting_data={
            "breadcrumbs_analyzed": len(breadcrumbs),
            "logs_analyzed": len(logs),
        }
    )


def _analyze_breadcrumbs_with_llm(
    issue_data: dict,
    logs: list,
    config: AgentConfig
) -> SubagentResult:
    """
    Analyze breadcrumbs using LLM.
    
    Would use Claude with turn cap to analyze the event sequence.
    """
    # In production, would call Bedrock with turn limit
    logger.warning("LLM breadcrumb analysis not implemented, using mock")
    return _mock_breadcrumb_analysis(issue_data, logs)
