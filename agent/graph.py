"""
LangGraph Pipeline Definition

This is the core orchestration harness that defines the fixed plan.
Maps to Uber's LangGraph pipeline architecture.

Flow:
  START
    ↓
  context_collector (deterministic)
    ↓
  classify_rca (Claude Sonnet, 20 turns)
    ↓
  consolidator (deterministic decision)
    ↓
  [if needs_human] → escalate → END
  [else] → fix (Claude Opus, 30-50 turns)
    ↓
  validate (runs tests)
    ↓
  [if failed and retries left] → fix (retry)
  [else] → create_diff (Claude Sonnet, 20 turns)
    ↓
  END
"""

import logging
from typing import Literal

from langgraph.graph import END, StateGraph

from agent.config import AgentConfig
from agent.nodes.classify_rca import classify_rca_node
from agent.nodes.consolidator import consolidator_node
from agent.nodes.context_collector import context_collector_node
from agent.nodes.create_diff import create_diff_node
from agent.nodes.fix import fix_node
from agent.nodes.subagents.breadcrumbs import breadcrumbs_subagent
from agent.nodes.subagents.flag_correlation import flag_correlation_subagent
from agent.nodes.subagents.offending_commit import offending_commit_subagent
from agent.nodes.validate import validate_node
from agent.state import AgentState

logger = logging.getLogger(__name__)


def _fan_out_to_subagents(state: AgentState, config: AgentConfig) -> list:
    """
    Fan out to parallel subagents using Send.

    This is called by the conditional edge after classify_rca.
    Returns a list of Send objects to trigger parallel execution.
    """
    from langgraph.types import Send

    # Convert state to dict for subagents
    state_dict = {
        "issue_id": state.issue_id,
        "issue_title": state.issue_title,
        "issue_data": state.issue_data,
        "logs": state.logs,
        "traces": state.traces,
        "code_context": state.code_context,
        "rca_result": state.rca_result,
        "subagent_results": state.subagent_results,
    }

    logger.info("Fanning out to 3 parallel subagents")

    # Return list of Send objects
    return [
        Send("breadcrumbs_subagent", (state_dict, config)),
        Send("flag_correlation_subagent", (state_dict, config)),
        Send("offending_commit_subagent", (state_dict, config)),
    ]


def create_debug_agent_graph(config: AgentConfig) -> StateGraph:
    """
    Create the LangGraph pipeline for the debug agent.

    This is the fixed plan that Uber uses:
    - No free-form planning (reduces hallucination)
    - Deterministic nodes where possible
    - Parallel subagent fan-out with Send
    - Clear state passing between nodes
    - Bounded retry loops with guardrails

    Flow:
        context_collector
            ↓
        classify_rca (returns Send list)
            ↓ ↓ ↓ (fan out)
        [breadcrumbs, flag_correlation, offending_commit] (parallel)
            ↓ ↓ ↓ (merge)
        consolidator
            ↓
        fix → validate → create_diff
    """

    # Create state graph
    graph = StateGraph(AgentState)

    # Add main pipeline nodes
    graph.add_node("context_collector", lambda state: context_collector_node(state, config))
    graph.add_node("classify_rca", lambda state: classify_rca_node(state, config))

    # Add parallel subagent nodes
    # These receive (state_dict, config) tuples from Send
    graph.add_node("breadcrumbs_subagent", lambda args: breadcrumbs_subagent(*args))
    graph.add_node("flag_correlation_subagent", lambda args: flag_correlation_subagent(*args))
    graph.add_node("offending_commit_subagent", lambda args: offending_commit_subagent(*args))

    # Add consolidator (merges subagent results)
    graph.add_node("consolidator", lambda state: consolidator_node(state, config))

    # Add remaining nodes
    graph.add_node("fix", lambda state: fix_node(state, config))
    graph.add_node("validate", lambda state: validate_node(state, config))
    graph.add_node("create_diff", lambda state: create_diff_node(state, config))

    # Define edges (the fixed plan)
    graph.set_entry_point("context_collector")
    graph.add_edge("context_collector", "classify_rca")

    # classify_rca fans out to subagents via conditional edge
    graph.add_conditional_edges(
        "classify_rca",
        lambda state: _fan_out_to_subagents(state, config),
    )

    # Subagents all edge to consolidator
    graph.add_edge("breadcrumbs_subagent", "consolidator")
    graph.add_edge("flag_correlation_subagent", "consolidator")
    graph.add_edge("offending_commit_subagent", "consolidator")

    # Consolidator decides: escalate or proceed
    graph.add_conditional_edges(
        "consolidator",
        _should_escalate,
        {
            "escalate": END,
            "proceed": "fix",
        },
    )

    graph.add_edge("fix", "validate")

    # Validation decides: retry, fail, or succeed
    graph.add_conditional_edges(
        "validate",
        _check_validation,
        {
            "retry": "fix",
            "success": "create_diff",
            "failed": END,
        },
    )

    graph.add_edge("create_diff", END)

    return graph


def _should_escalate(state: AgentState) -> Literal["escalate", "proceed"]:
    """
    Decide whether to escalate to human or proceed to fix.

    Uber's approach: escalate if confidence is low or no code fix needed,
    rather than burning more LLM turns on uncertain ground.
    """
    if state.needs_human_escalation:
        logger.info(f"Escalating to human: {state.escalation_reason}")
        return "escalate"
    return "proceed"


def _check_validation(state: AgentState) -> Literal["retry", "success", "failed"]:
    """
    Check validation result and decide next action.

    Uber's approach:
    - If tests pass: proceed to create_diff
    - If tests fail and retries left: feed output back to fix node
    - If tests fail and no retries: fail (needs human)

    Bounded retry loop prevents infinite fixing.
    """
    if state.validation_result is None:
        logger.error("No validation result available")
        return "failed"

    if state.validation_result.passed:
        logger.info("Validation passed, proceeding to create_diff")
        return "success"

    # Check retry budget
    if state.validation_attempts >= state.max_validation_retries:
        logger.warning(
            f"Validation failed after {state.validation_attempts} attempts, " "giving up"
        )
        return "failed"

    logger.info(
        f"Validation failed (attempt {state.validation_attempts}/"
        f"{state.max_validation_retries}), retrying fix"
    )
    return "retry"


def run_debug_agent(
    issue_id: str,
    issue_title: str,
    issue_data: dict,
    config: AgentConfig,
) -> AgentState:
    """
    Run the debug agent on an issue.

    Args:
        issue_id: Unique issue identifier
        issue_title: Human-readable issue title
        issue_data: Issue data (logs, stack trace, etc.)
        config: Agent configuration

    Returns:
        Final agent state after pipeline completes
    """
    logger.info(f"Starting debug agent for issue {issue_id}")

    # Create initial state
    initial_state = AgentState(
        issue_id=issue_id,
        issue_title=issue_title,
        issue_data=issue_data,
    )

    # Create and compile graph
    graph = create_debug_agent_graph(config)
    app = graph.compile()

    # Run the graph
    try:
        final_state = app.invoke(initial_state)

        # Log summary
        _log_execution_summary(final_state)

        return final_state

    except Exception as e:
        logger.error(f"Error running debug agent: {e}", exc_info=True)
        initial_state.errors.append(f"Pipeline error: {e}")
        return initial_state


def _log_execution_summary(state) -> None:
    """Log summary of agent execution."""
    # LangGraph returns a dict, not AgentState
    if isinstance(state, dict):
        issue_id = state.get("issue_id", "UNKNOWN")
        issue_title = state.get("issue_title", "")
        rca_result = state.get("rca_result")
        needs_escalation = state.get("needs_human_escalation", False)
        escalation_reason = state.get("escalation_reason")
        fix_result = state.get("fix_result")
        validation_result = state.get("validation_result")
        validation_attempts = state.get("validation_attempts", 0)
        pr_url = state.get("pr_url")
        errors = state.get("errors", [])
    else:
        issue_id = state.issue_id
        issue_title = state.issue_title
        rca_result = state.rca_result
        needs_escalation = state.needs_human_escalation
        escalation_reason = state.escalation_reason
        fix_result = state.fix_result
        validation_result = state.validation_result
        validation_attempts = state.validation_attempts
        pr_url = state.pr_url
        errors = state.errors

    logger.info("=" * 60)
    logger.info("AGENT EXECUTION SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Issue: {issue_id} - {issue_title}")

    if rca_result:
        confidence = (
            rca_result.confidence
            if hasattr(rca_result, "confidence")
            else rca_result.get("confidence", 0)
        )
        category = (
            rca_result.category
            if hasattr(rca_result, "category")
            else rca_result.get("category", "unknown")
        )
        logger.info(f"RCA: {category} " f"(confidence: {confidence:.0%})")

    if needs_escalation:
        logger.info(f"ESCALATED: {escalation_reason}")
    elif fix_result:
        if hasattr(fix_result, "changes"):
            changes = fix_result.changes
            applied = fix_result.fix_applied
        else:
            changes = fix_result.get("changes", [])
            applied = fix_result.get("fix_applied", False)
        logger.info(f"Fix: {len(changes)} changes, " f"applied: {applied}")

    if validation_result:
        if hasattr(validation_result, "passed"):
            passed = validation_result.passed
        else:
            passed = validation_result.get("passed", False)
        status = "PASSED" if passed else "FAILED"
        logger.info(f"Validation: {status} (attempts: {validation_attempts})")

    if pr_url:
        logger.info(f"PR: {pr_url}")

    if errors:
        logger.warning(f"Errors ({len(errors)}):")
        for error in errors:
            logger.warning(f"  - {error}")

    logger.info("=" * 60)
