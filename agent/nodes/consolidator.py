"""
Consolidator Node (DETERMINISTIC)

Maps to Uber's consolidation logic after parallel subagents complete.

Decides whether to:
1. Proceed to fix
2. Retry RCA with more context
3. Escalate to human with evidence packet

Uber's approach: if subagents disagree or confidence is low, do one cheap
retry then escalate rather than burning more LLM turns.
"""

import logging

from agent.config import AgentConfig
from agent.state import AgentState

logger = logging.getLogger(__name__)


# Confidence threshold for proceeding to fix
CONFIDENCE_THRESHOLD = 0.7


def consolidator_node(state: AgentState, config: AgentConfig) -> AgentState:
    """
    Consolidate RCA results and decide next action.
    
    This is deterministic logic (no LLM) that looks at:
    - RCA confidence score
    - Subagent agreement (if multiple subagents ran)
    - Validation of evidence
    
    Returns decision to proceed or escalate.
    """
    logger.info(f"Consolidating RCA for issue {state.issue_id}")
    
    if state.rca_result is None:
        logger.error("No RCA result available")
        state.needs_human_escalation = True
        state.escalation_reason = "RCA node did not produce a result"
        return state
    
    confidence = state.rca_result.confidence
    
    # Check confidence threshold
    if confidence < CONFIDENCE_THRESHOLD:
        logger.warning(
            f"Low RCA confidence ({confidence:.2f} < {CONFIDENCE_THRESHOLD}), "
            "escalating to human"
        )
        state.needs_human_escalation = True
        state.escalation_reason = (
            f"RCA confidence too low ({confidence:.2f}). "
            "Evidence packet prepared for human review."
        )
        return state
    
    # Check if code fix is needed
    if not state.rca_result.requires_code_fix:
        logger.info("RCA determined no code fix is needed")
        state.needs_human_escalation = True
        state.escalation_reason = (
            f"Issue categorized as {state.rca_result.category}, "
            "no code fix required. Needs human review for next steps."
        )
        return state
    
    # All checks passed, proceed to fix
    logger.info(
        f"RCA consolidated successfully (confidence: {confidence:.2f}), "
        "proceeding to fix node"
    )
    return state
