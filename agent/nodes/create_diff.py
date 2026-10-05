"""
Create Diff Node (LLM: Claude Sonnet, max 20 turns)

Maps to Uber's create_diff node:
- Generates a Phabricator diff (we use GitHub PR)
- Attaches RCA summary and evidence
- Links to Jira/issue tracker
- Updates metadata DB
- Notifies team on Slack

Uber's output: diff with Summary (Problem/Fix/Flipr Gating/Test Plan)
"""

import logging
from typing import Optional

from agent.config import AgentConfig
from agent.state import AgentState

logger = logging.getLogger(__name__)


def create_diff_node(state: AgentState, config: AgentConfig) -> AgentState:
    """
    Create a pull request with the fix.
    
    In mock mode: simulates PR creation
    In real mode: creates actual GitHub PR via API
    
    Uber's setup:
    - Creates Phabricator diff
    - Title: "[DebugAssist] Fix P1 crash: ..."
    - Body: Problem / Fix / Flipr Gating / Test Plan
    - Links Jira ticket
    - Notifies on Slack
    - Updates metadata DB
    """
    logger.info(f"Creating diff for issue {state.issue_id}")
    
    if config.mode == "mock":
        # Mock mode: simulate PR creation
        state.pr_url = f"https://github.com/mock-org/mock-repo/pull/42"
        logger.info(f"Diff created (mock): {state.pr_url}")
    else:
        # Real mode: create actual PR
        state.pr_url = _create_github_pr(state, config)
        logger.info(f"Diff created: {state.pr_url}")
    
    return state


def _create_github_pr(state: AgentState, config: AgentConfig) -> Optional[str]:
    """
    Create a GitHub pull request with real branch and commit.
    
    Steps:
    1. Create a new branch
    2. Apply the diff (commit changes)
    3. Push the branch
    4. Create PR via GitHub API
    """
    if not config.github_token or not config.github_repo:
        logger.warning("GitHub credentials not configured, skipping PR creation")
        return None
    
    try:
        import subprocess
        import tempfile
        import os
        
        # Branch name
        branch_name = f"fix/debug-assist-{state.issue_id.lower()}"
        
        # For real implementation, would:
        # 1. Clone repo to temp dir
        # 2. Create branch
        # 3. Apply diff
        # 4. Commit
        # 5. Push
        # 6. Create PR
        
        # Simplified: use GitHub API directly
        from github import Github
        
        gh = Github(config.github_token)
        repo = gh.get_repo(config.github_repo)
        
        # Get default branch
        default_branch = repo.default_branch
        base_sha = repo.get_branch(default_branch).commit.sha
        
        # Create new branch (via API)
        try:
            ref = repo.create_git_ref(
                ref=f"refs/heads/{branch_name}",
                sha=base_sha
            )
            logger.info(f"Created branch: {branch_name}")
        except Exception as e:
            # Branch might already exist
            logger.warning(f"Branch creation failed (may exist): {e}")
        
        # Apply changes to branch (simplified - in real impl would commit files)
        # For demonstration, we'll create PR with existing branch or note in body
        
        # Build PR title and body
        title = _build_pr_title(state)
        body = _build_pr_body(state)
        body += "\n\n---\n**Note:** This PR was created by Debug Assist autonomous agent.\n"
        
        # Create PR
        pr = repo.create_pull(
            title=title,
            body=body,
            head=branch_name,
            base=default_branch,
        )
        
        logger.info(f"Created PR: {pr.html_url}")
        return pr.html_url
        
    except Exception as e:
        logger.error(f"Error creating GitHub PR: {e}", exc_info=True)
        state.errors.append(f"PR creation failed: {e}")
        return None


def _build_pr_title(state: AgentState) -> str:
    """Build PR title."""
    return f"[DebugAssist] Fix {state.issue_id}: {state.issue_title}"


def _build_pr_body(state: AgentState) -> str:
    """
    Build PR body with RCA summary and fix details.
    
    Maps to Uber's diff format:
    - Problem (what crashed/broke)
    - Root Cause (from RCA)
    - Fix (what changed)
    - Feature Flag Gating (if applicable)
    - Test Plan (validation results)
    - Links (issue, evidence)
    """
    rca = state.rca_result
    fix = state.fix_result
    validation = state.validation_result
    
    body = f"""## Problem

{state.issue_title}

**Exception:** `{state.issue_data.get('exception_type', 'Unknown')}`
**Occurrences:** {state.issue_data.get('occurrences', 'Unknown')}
**First Seen:** {state.issue_data.get('first_seen', 'Unknown')}

## Root Cause Analysis

{rca.summary if rca else 'No RCA available'}

**Category:** {rca.category if rca else 'Unknown'}
**Confidence:** {f'{rca.confidence:.0%}' if rca else 'N/A'}

{rca.root_cause if rca else 'No detailed analysis'}

## Fix

"""
    
    if fix and fix.changes:
        for change in fix.changes:
            body += f"### {change['file']}\n\n"
            body += "```diff\n"
            body += change['diff']
            body += "\n```\n\n"
    else:
        body += "No changes applied.\n\n"
    
    # Feature flag gating (if applicable)
    if fix and fix.mitigation:
        body += f"""## Mitigation

{fix.mitigation}

"""
    
    # Test plan
    body += """## Test Plan

"""
    
    if validation:
        status = "✅ PASSED" if validation.passed else "❌ FAILED"
        body += f"**Validation Status:** {status}\n\n"
        
        if validation.issues:
            body += "**Issues:**\n"
            for issue in validation.issues:
                body += f"- {issue}\n"
            body += "\n"
        
        body += "```\n"
        body += validation.test_output[:1000]  # Truncate for brevity
        if len(validation.test_output) > 1000:
            body += "\n... (output truncated)"
        body += "\n```\n\n"
    
    # Links
    body += f"""## Links

- Issue: {state.issue_id}
- Generated by: Mini Debug Assist (automated debugging agent)

---

*This PR was automatically generated by the Debug Assist agent. Please review carefully before merging.*
"""
    
    return body
