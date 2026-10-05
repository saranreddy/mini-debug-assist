"""
CLI entrypoint for the Mini Debug Assist agent.

Usage:
    python -m agent.cli --mode mock --issue tests/fixtures/keyerror_issue.yaml
    python -m agent.cli --mode aws --issue-id DEMO-001
"""

import argparse
import logging
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
        with open(filepath, "r") as f:
            data = yaml.safe_load(f)
        logger.info(f"Loaded issue from {filepath}")
        return data
    except Exception as e:
        logger.error(f"Error loading issue file: {e}")
        sys.exit(1)


def load_issue_from_aws(issue_id: str, config) -> dict:
    """
    Load issue from AWS (CloudWatch, Healthline-equivalent).
    
    In production, this would query your issue tracking system.
    For now, this is a placeholder.
    """
    logger.error("Loading issues from AWS is not yet implemented")
    logger.error("Use --mode mock with --issue <file> instead")
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Mini Debug Assist - Automated debugging agent"
    )
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
    if args.mode == "aws" and not args.issue_id:
        parser.error("--issue-id required in aws mode")
    
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
        issue_id = args.issue_id
        issue_data = load_issue_from_aws(issue_id, config)
        issue_title = issue_data.get("title", "No title")
    
    logger.info(f"Processing Issue: {issue_id} - {issue_title}")
    logger.info("=" * 60)
    
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
        needs_escalation = final_state.get("needs_human_escalation", False) if isinstance(final_state, dict) else final_state.needs_human_escalation
        pr_url = final_state.get("pr_url") if isinstance(final_state, dict) else final_state.pr_url
        errors = final_state.get("errors", []) if isinstance(final_state, dict) else final_state.errors
        
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
        if hasattr(rca_result, 'category'):
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
        if hasattr(fix_result, 'fix_applied'):
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
        if hasattr(validation_result, 'passed'):
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
