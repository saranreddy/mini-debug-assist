"""
CLI entrypoint for the Mini Debug Assist agent.

Usage:
    python -m agent.cli --mode mock --issue tests/fixtures/keyerror_issue.yaml
    python -m agent.cli --mode aws --issue-id DEMO-001
"""

import argparse
import logging
import os
import sys
from pathlib import Path

import yaml

from agent.config import get_config
from agent.graph import run_debug_agent

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)


def load_issue_from_file(filepath: str) -> dict:
    """Load issue data from YAML fixture."""
    try:
        with open(filepath) as f:
            data = yaml.safe_load(f)
        logger.info(f"Loaded issue from {filepath}")
        return data
    except Exception as e:
        logger.error(f"Error loading issue file: {e}")
        sys.exit(1)


def load_issue_from_aws(issue_id: str, config) -> dict:
    """
    Load issue from AWS CloudWatch alarm event.

    Reads alarm data from environment variables set by EventBridge:
    - ISSUE_SOURCE: "alarm"
    - ALARM_EVENT_JSON: The full CloudWatch alarm event

    Or falls back to parsing issue_id if provided.
    """
    import json

    # Try to load from environment (event-driven invocation)
    issue_source = os.getenv("ISSUE_SOURCE")

    if issue_source == "alarm":
        alarm_event_json = os.getenv("ALARM_EVENT_JSON")
        if alarm_event_json:
            try:
                alarm_event = json.loads(alarm_event_json)
                return _parse_alarm_event(alarm_event)
            except Exception as e:
                logger.error(f"Error parsing alarm event: {e}")

    # Fall back to querying by issue_id
    logger.error(
        "No alarm event in environment and direct query by ID not implemented. "
        "Set ISSUE_SOURCE=alarm and ALARM_EVENT_JSON for event-driven mode."
    )
    sys.exit(1)


def _parse_alarm_event(alarm_event: dict) -> dict:
    """
    Parse CloudWatch alarm event into issue data.

    Alarm event structure:
    {
        "source": "aws.cloudwatch",
        "detail-type": "CloudWatch Alarm State Change",
        "detail": {
            "alarmName": "...",
            "state": {"value": "ALARM"},
            "configuration": {...},
            ...
        }
    }
    """
    detail = alarm_event.get("detail", {})
    alarm_name = detail.get("alarmName", "Unknown")
    state_value = detail.get("state", {}).get("value", "UNKNOWN")
    state_reason = detail.get("state", {}).get("reason", "")
    timestamp = alarm_event.get("time", "")

    # Extract metric info
    configuration = detail.get("configuration", {})
    metric_name = (
        configuration.get("metrics", [{}])[0]
        .get("metricStat", {})
        .get("metric", {})
        .get("name", "")
    )
    namespace = (
        configuration.get("metrics", [{}])[0]
        .get("metricStat", {})
        .get("metric", {})
        .get("namespace", "")
    )
    dimensions = (
        configuration.get("metrics", [{}])[0]
        .get("metricStat", {})
        .get("metric", {})
        .get("dimensions", {})
    )

    # Parse dimensions for context
    error_type = dimensions.get("error_type", "Unknown")
    endpoint = dimensions.get("endpoint", "Unknown")

    return {
        "issue_id": f"ALARM-{alarm_name}-{timestamp[:10]}",
        "title": f"CloudWatch Alarm: {alarm_name}",
        "source": "cloudwatch_alarm",
        "alarm_name": alarm_name,
        "alarm_state": state_value,
        "alarm_reason": state_reason,
        "timestamp": timestamp,
        "metric_name": metric_name,
        "metric_namespace": namespace,
        "error_type": error_type,
        "endpoint": endpoint,
        "service": "mini-debug-assist-demo",
        "environment": "production",
    }


def main():
    parser = argparse.ArgumentParser(description="Mini Debug Assist - Automated debugging agent")
    parser.add_argument(
        "--mode",
        choices=["mock", "aws"],
        default="mock",
        help="Execution mode: mock (fixture data) or aws (real AWS services)",
    )
    parser.add_argument(
        "--issue",
        help="Path to issue YAML file (for mock mode)",
    )
    parser.add_argument(
        "--issue-id",
        help="Issue ID to load from tracking system (for aws mode)",
    )
    parser.add_argument(
        "--output",
        help="Output directory for results (default: ./output)",
        default="./output",
    )

    args = parser.parse_args()

    # Validate arguments
    if args.mode == "mock" and not args.issue:
        parser.error("--issue required in mock mode")
    # In aws mode, issue_id is optional if running from alarm event
    if args.mode == "aws" and not args.issue_id and not os.getenv("ISSUE_SOURCE"):
        parser.error("--issue-id required in aws mode (or set ISSUE_SOURCE=alarm)")

    # Get configuration
    config = get_config(mode=args.mode)

    logger.info("=" * 60)
    logger.info("Mini Debug Assist - Starting Agent")
    logger.info("=" * 60)
    logger.info(f"Mode: {config.mode}")
    logger.info(f"Agent Type: {config.agent_type}")

    # Load issue data
    if args.mode == "mock":
        issue_data = load_issue_from_file(args.issue)
        issue_id = issue_data.get("issue_id", "UNKNOWN")
        issue_title = issue_data.get("title", "No title")
    else:
        # AWS mode: from alarm event or by ID
        if os.getenv("ISSUE_SOURCE") == "alarm":
            issue_data = load_issue_from_aws(None, config)
            issue_id = issue_data.get("issue_id", "UNKNOWN")
            issue_title = issue_data.get("title", "No title")
        else:
            issue_id = args.issue_id
            issue_data = load_issue_from_aws(issue_id, config)
            issue_title = issue_data.get("title", "No title")

    logger.info(f"Processing Issue: {issue_id} - {issue_title}")
    logger.info("=" * 60)

    # Deduplication check (AWS mode only)
    if args.mode == "aws":
        from agent.dedup import get_error_signature, should_investigate

        error_signature = get_error_signature(
            alarm_name=issue_data.get("alarm_name", issue_id),
            error_type=issue_data.get("error_type", ""),
            endpoint=issue_data.get("endpoint", ""),
        )

        if not should_investigate(error_signature):
            logger.info("Skipping duplicate investigation (recent investigation exists)")
            sys.exit(0)

    # Run agent
    try:
        final_state = run_debug_agent(
            issue_id=issue_id,
            issue_title=issue_title,
            issue_data=issue_data,
            config=config,
        )

        # Save results
        output_dir = Path(args.output)
        output_dir.mkdir(parents=True, exist_ok=True)

        result_file = output_dir / f"{issue_id}_result.yaml"
        _save_results(final_state, result_file)

        logger.info(f"Results saved to {result_file}")

        # Exit code based on outcome
        # Handle both dict and AgentState
        needs_escalation = (
            final_state.get("needs_human_escalation", False)
            if isinstance(final_state, dict)
            else final_state.needs_human_escalation
        )
        pr_url = final_state.get("pr_url") if isinstance(final_state, dict) else final_state.pr_url
        errors = (
            final_state.get("errors", []) if isinstance(final_state, dict) else final_state.errors
        )

        if needs_escalation:
            logger.warning("Agent escalated to human review")
            sys.exit(2)
        elif pr_url:
            logger.info("Agent completed successfully with PR")
            sys.exit(0)
        elif errors:
            logger.error("Agent completed with errors")
            sys.exit(1)
        else:
            logger.info("Agent completed")
            sys.exit(0)

    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        sys.exit(1)


def _save_results(state, output_file: Path) -> None:
    """Save agent results to YAML file."""
    # LangGraph returns a dict
    if isinstance(state, dict):
        issue_id = state.get("issue_id", "UNKNOWN")
        issue_title = state.get("issue_title", "")
        needs_escalation = state.get("needs_human_escalation", False)
        escalation_reason = state.get("escalation_reason")
        rca_result = state.get("rca_result")
        fix_result = state.get("fix_result")
        validation_result = state.get("validation_result")
        validation_attempts = state.get("validation_attempts", 0)
        pr_url = state.get("pr_url")
        errors = state.get("errors", [])
    else:
        issue_id = state.issue_id
        issue_title = state.issue_title
        needs_escalation = state.needs_human_escalation
        escalation_reason = state.escalation_reason
        rca_result = state.rca_result
        fix_result = state.fix_result
        validation_result = state.validation_result
        validation_attempts = state.validation_attempts
        pr_url = state.pr_url
        errors = state.errors

    results = {
        "issue_id": issue_id,
        "issue_title": issue_title,
        "escalated": needs_escalation,
        "escalation_reason": escalation_reason,
    }

    if rca_result:
        if hasattr(rca_result, "category"):
            results["rca"] = {
                "category": rca_result.category,
                "requires_code_fix": rca_result.requires_code_fix,
                "confidence": rca_result.confidence,
                "root_cause": rca_result.root_cause,
                "summary": rca_result.summary,
            }
        else:
            results["rca"] = {
                "category": rca_result.get("category"),
                "requires_code_fix": rca_result.get("requires_code_fix"),
                "confidence": rca_result.get("confidence"),
                "root_cause": rca_result.get("root_cause"),
                "summary": rca_result.get("summary"),
            }

    if fix_result:
        if hasattr(fix_result, "fix_applied"):
            results["fix"] = {
                "applied": fix_result.fix_applied,
                "changes": fix_result.changes,
                "mitigation": fix_result.mitigation,
            }
        else:
            results["fix"] = {
                "applied": fix_result.get("fix_applied"),
                "changes": fix_result.get("changes"),
                "mitigation": fix_result.get("mitigation"),
            }

    if validation_result:
        if hasattr(validation_result, "passed"):
            results["validation"] = {
                "passed": validation_result.passed,
                "attempts": validation_attempts,
                "issues": validation_result.issues,
            }
        else:
            results["validation"] = {
                "passed": validation_result.get("passed"),
                "attempts": validation_attempts,
                "issues": validation_result.get("issues"),
            }

    if pr_url:
        results["pr_url"] = pr_url

    if errors:
        results["errors"] = errors

    with open(output_file, "w") as f:
        yaml.dump(results, f, default_flow_style=False)


if __name__ == "__main__":
    main()
