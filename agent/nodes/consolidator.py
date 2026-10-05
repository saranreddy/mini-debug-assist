"""
Consolidator Node (DETERMINISTIC with optional retry)

Maps to Uber's consolidation logic after parallel subagents complete.

Merges results from parallel subagents and decides:
1. Proceed to fix (high confidence, subagents agree)
2. Retry weakest subagent (one cheap retry)
3. Escalate to human with evidence packet (disagreement or low confidence)

Uber's approach: if subagents disagree or confidence is low, do one cheap
retry then escalate rather than burning more LLM turns.
"""

import logging
from typing import Any

from agent.config import AgentConfig
from agent.state import AgentState, SubagentResult

logger = logging.getLogger(__name__)


# Confidence threshold for proceeding to fix
CONFIDENCE_THRESHOLD = 0.7

# Agreement threshold (% of subagents that must agree)
AGREEMENT_THRESHOLD = 0.67  # At least 2 out of 3


def consolidator_node(state: AgentState, config: AgentConfig) -> AgentState:
    """
    Consolidate RCA and subagent results, decide next action.

    Deterministic logic that:
    1. Merges subagent results
    2. Checks for agreement between subagents
    3. Handles disagreements with retry or escalation
    4. Validates confidence scores

    Decision tree:
    - High confidence + agreement → proceed to fix
    - Low confidence OR disagreement → check retry budget
    - Retry budget exhausted → escalate with evidence packet
    """
    logger.info(f"Consolidating RCA + {len(state.subagent_results)} subagent results")

    if state.rca_result is None:
        logger.error("No RCA result available")
        state.needs_human_escalation = True
        state.escalation_reason = "RCA node did not produce a result"
        return state

    # Merge subagent results with primary RCA
    merged = _merge_subagent_results(state)

    # Check for agreement between subagents
    agreement = _check_subagent_agreement(state)

    # Compute overall confidence (weighted average)
    overall_confidence = _compute_overall_confidence(state)

    logger.info(
        f"Consolidation: confidence={overall_confidence:.2f}, " f"agreement={agreement:.2f}"
    )

    # Decision logic
    if overall_confidence < CONFIDENCE_THRESHOLD:
        # Low confidence
        return _handle_low_confidence(state, overall_confidence, merged)

    if agreement < AGREEMENT_THRESHOLD:
        # Disagreement between subagents
        return _handle_disagreement(state, agreement, merged)

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
        f"✓ Consolidation successful (confidence: {overall_confidence:.2f}, "
        f"agreement: {agreement:.2f}), proceeding to fix"
    )
    return state


def _merge_subagent_results(state: AgentState) -> dict[str, Any]:
    """
    Merge subagent results into consolidated evidence.

    Returns merged data with:
    - All hypotheses
    - All evidence
    - Confidence scores
    """
    merged = {
        "primary_hypothesis": state.rca_result.summary if state.rca_result else "",
        "subagent_hypotheses": {},
        "all_evidence": [],
        "confidence_scores": {},
    }

    # Add primary RCA evidence
    if state.rca_result:
        merged["all_evidence"].extend(state.rca_result.evidence)
        merged["confidence_scores"]["primary_rca"] = state.rca_result.confidence

    # Add subagent results
    for subagent_type, result in state.subagent_results.items():
        if isinstance(result, dict):
            hypothesis = result.get("hypothesis", "")
            confidence = result.get("confidence", 0)
            evidence = result.get("evidence", [])
        else:
            hypothesis = result.hypothesis
            confidence = result.confidence
            evidence = result.evidence

        merged["subagent_hypotheses"][subagent_type] = hypothesis
        merged["all_evidence"].extend(evidence)
        merged["confidence_scores"][subagent_type] = confidence

    return merged


def _check_subagent_agreement(state: AgentState) -> float:
    """
    Check if subagents agree with the primary RCA.

    Returns agreement score (0.0 to 1.0).

    Agreement heuristics:
    - Do subagent hypotheses support the primary hypothesis?
    - Are confidence scores similar?
    - Do they point to the same root cause category?
    """
    if not state.subagent_results:
        return 1.0  # No subagents, consider it agreement

    # Simple heuristic: check if most subagents have similar confidence
    confidences = []

    if state.rca_result:
        confidences.append(state.rca_result.confidence)

    for result in state.subagent_results.values():
        if isinstance(result, dict):
            confidences.append(result.get("confidence", 0))
        else:
            confidences.append(result.confidence)

    if not confidences:
        return 0.5

    # Check variance in confidence
    mean_confidence = sum(confidences) / len(confidences)
    variance = sum((c - mean_confidence) ** 2 for c in confidences) / len(confidences)

    # High variance = disagreement
    # Map variance (0-0.25) to agreement (1.0-0.0)
    agreement = max(0.0, 1.0 - (variance * 4))

    return agreement


def _compute_overall_confidence(state: AgentState) -> float:
    """
    Compute weighted average confidence from RCA + subagents.

    Primary RCA gets 40% weight, subagents split remaining 60%.
    """
    if not state.rca_result:
        return 0.0

    # Primary RCA weight
    total_confidence = state.rca_result.confidence * 0.4
    total_weight = 0.4

    # Subagent weights
    if state.subagent_results:
        subagent_weight = 0.6 / len(state.subagent_results)

        for result in state.subagent_results.values():
            if isinstance(result, dict):
                confidence = result.get("confidence", 0)
            else:
                confidence = result.confidence

            total_confidence += confidence * subagent_weight
            total_weight += subagent_weight

    return total_confidence / total_weight if total_weight > 0 else 0.0


def _handle_low_confidence(state: AgentState, confidence: float, merged: dict) -> AgentState:
    """Handle low confidence case: retry or escalate."""
    logger.warning(f"Low overall confidence ({confidence:.2f})")

    # Check if we've already retried
    retry_count = state.turn_count.get("consolidator_retry", 0)

    if retry_count == 0:
        # One cheap retry of weakest subagent
        weakest_name = _find_weakest_subagent(state)
        logger.info(f"Attempting one retry of weakest subagent: {weakest_name}")

        # Re-run with higher turn cap and other hypotheses as context
        retry_result = _retry_subagent(state, weakest_name, merged)

        if retry_result:
            # Update state with retry result
            state.subagent_results[weakest_name] = retry_result
            state.turn_count["consolidator_retry"] = 1

            # Re-check agreement and confidence
            new_agreement = _check_subagent_agreement(state)
            new_confidence = _compute_overall_confidence(state)

            logger.info(
                f"After retry: confidence={new_confidence:.2f}, " f"agreement={new_agreement:.2f}"
            )

            # If improved, proceed
            if new_confidence >= CONFIDENCE_THRESHOLD and new_agreement >= AGREEMENT_THRESHOLD:
                logger.info("✓ Retry improved results, proceeding to fix")
                return state

    # Escalate with evidence packet
    state.needs_human_escalation = True
    state.escalation_reason = (
        f"Low confidence ({confidence:.2f}) after consolidation. "
        f"Tried {retry_count} retries. Evidence packet:\n"
        f"- Primary: {merged['primary_hypothesis']}\n"
        f"- Subagents: {', '.join(merged['subagent_hypotheses'].keys())}\n"
        f"- Suggested next check: Review {_find_weakest_subagent(state)} evidence"
    )

    return state


def _handle_disagreement(state: AgentState, agreement: float, merged: dict) -> AgentState:
    """Handle disagreement case: retry or escalate."""
    logger.warning(f"Subagents disagree (agreement={agreement:.2f})")

    # Check if we've already retried
    retry_count = state.turn_count.get("consolidator_retry", 0)

    if retry_count == 0:
        # One cheap retry of weakest subagent
        weakest_name = _find_weakest_subagent(state)
        logger.info(f"Attempting one retry of weakest subagent: {weakest_name}")

        # Re-run with context from other subagents
        retry_result = _retry_subagent(state, weakest_name, merged)

        if retry_result:
            # Update state with retry result
            state.subagent_results[weakest_name] = retry_result
            state.turn_count["consolidator_retry"] = 1

            # Re-check agreement and confidence
            new_agreement = _check_subagent_agreement(state)
            new_confidence = _compute_overall_confidence(state)

            logger.info(
                f"After retry: confidence={new_confidence:.2f}, " f"agreement={new_agreement:.2f}"
            )

            # If improved, proceed
            if new_confidence >= CONFIDENCE_THRESHOLD and new_agreement >= AGREEMENT_THRESHOLD:
                logger.info("✓ Retry improved agreement, proceeding to fix")
                return state

    # Escalate with evidence packet showing disagreement
    hypotheses_str = "\n".join(
        [f"  - {k}: {v[:100]}..." for k, v in merged["subagent_hypotheses"].items()]
    )

    state.needs_human_escalation = True
    state.escalation_reason = (
        f"Subagents disagree (agreement={agreement:.2f}). "
        f"Evidence packet:\n"
        f"Primary hypothesis: {merged['primary_hypothesis'][:100]}...\n"
        f"Subagent hypotheses:\n{hypotheses_str}\n"
        f"Suggested: Review conflicting evidence and determine primary cause"
    )

    return state


def _find_weakest_subagent(state: AgentState) -> str:
    """Find subagent with lowest confidence for retry."""
    if not state.subagent_results:
        return "none"

    weakest = min(
        state.subagent_results.items(),
        key=lambda x: (
            x[1].confidence if hasattr(x[1], "confidence") else x[1].get("confidence", 1.0)
        ),
    )

    return weakest[0]


def _retry_subagent(state: AgentState, subagent_name: str, merged: dict) -> SubagentResult | None:
    """
    Re-run a subagent with higher turn cap and context from other subagents.

    Args:
        state: Current agent state
        subagent_name: Name of subagent to retry (breadcrumbs, flag_correlation, offending_commit)
        merged: Merged hypotheses from all subagents

    Returns:
        New SubagentResult or None if retry fails
    """
    import json

    from agent.config import AgentConfig
    from agent.nodes.subagents.common import invoke_subagent_llm

    logger.info(f"Retrying {subagent_name} with context from other subagents")

    # Get config (use from state or create default)
    # In real implementation, would pass from consolidator_node params
    config = AgentConfig.from_env(state.issue_data.get("mode", "aws"))

    # Build context from other subagents
    other_hypotheses = []
    for name, result in state.subagent_results.items():
        if name != subagent_name:
            if isinstance(result, dict):
                hyp = result.get("hypothesis", "Unknown")
                conf = result.get("confidence", 0)
            else:
                hyp = result.hypothesis
                conf = result.confidence
            other_hypotheses.append(f"{name}: {hyp} (confidence: {conf:.2f})")

    context_text = "\n".join(other_hypotheses) if other_hypotheses else "No other hypotheses"

    # Build retry prompt with context
    system_prompt = f"""You are re-analyzing as the {subagent_name} subagent.

Other subagents have proposed:
{context_text}

Primary RCA: {state.rca_result.root_cause if state.rca_result else 'Unknown'}

Your first analysis had low confidence or disagreed. 
Re-analyze with this additional context.

Return your analysis in JSON format inside <result> tags:

<result>
{{
  "hypothesis": "Your refined hypothesis",
  "confidence": 0.0 to 1.0,
  "evidence": ["Supporting evidence"]
}}
</result>"""

    user_message = f"""Issue: {state.issue_data.get('exception_type', 'Unknown')}

Logs: {json.dumps(state.logs[:10], indent=2) if state.logs else "No logs"}

Code Context: {json.dumps({k: v[:200] for k, v in state.code_context.items()}, indent=2) if state.code_context else "No code context"}

Re-analyze with the context from other subagents."""

    try:
        result, turns = invoke_subagent_llm(
            subagent_type=subagent_name,
            system_prompt=system_prompt,
            user_message=user_message,
            config=config._replace(max_turns_subagent=10),  # Higher turn cap for retry
        )

        logger.info(f"Retry complete: confidence={result.confidence:.2f}, turns={turns}")
        return result

    except Exception as e:
        logger.error(f"Retry failed: {e}")
        return None
