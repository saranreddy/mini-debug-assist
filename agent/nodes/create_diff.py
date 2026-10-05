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

from github.InputGitTreeElement import InputGitTreeElement

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
        state.pr_url = "https://github.com/mock-org/mock-repo/pull/42"
        logger.info(f"Diff created (mock): {state.pr_url}")
    else:
        # Real mode: create actual PR
        state.pr_url = _create_github_pr(state, config)
        logger.info(f"Diff created: {state.pr_url}")

    return state


def _create_github_pr(state: AgentState, config: AgentConfig) -> str | None:
    """
    Create a GitHub pull request with real branch and commit.

    Steps:
    1. Create a new branch from base
    2. Commit the file changes to the branch via GitHub API
    3. Create PR via GitHub API
    """
    if not config.github_token or not config.github_repo:
        logger.warning("GitHub credentials not configured, skipping PR creation")
        return None

    if not state.fix_result or not state.fix_result.changes:
        logger.warning("No changes to commit, skipping PR creation")
        return None

    try:
        from github import Github

        gh = Github(config.github_token)
        repo = gh.get_repo(config.github_repo)

        # Get default branch
        default_branch = repo.default_branch
        base_sha = repo.get_branch(default_branch).commit.sha

        # Branch name
        branch_name = f"fix/debug-assist-{state.issue_id.lower()}"
        logger.info(f"Creating branch {branch_name} from {default_branch} @ {base_sha[:8]}")

        # Create new branch (via API)
        try:
            ref = repo.create_git_ref(ref=f"refs/heads/{branch_name}", sha=base_sha)
            logger.info(f"Created branch: {branch_name}")
        except Exception as e:
            # Branch might already exist, try to get it
            logger.warning(f"Branch creation failed (may exist): {e}")
            try:
                ref = repo.get_git_ref(f"heads/{branch_name}")
                logger.info(f"Using existing branch: {branch_name}")
            except Exception as e2:
                logger.error(f"Failed to get existing branch: {e2}")
                raise

        # Commit changes to the branch via GitHub API
        # Using Git Database API: create blobs -> create tree -> create commit -> update ref
        logger.info(f"Committing {len(state.fix_result.changes)} file changes to branch")

        # Get the base tree
        base_commit = repo.get_git_commit(base_sha)
        base_tree = base_commit.tree

        # Create new tree with file changes
        tree_elements: list[InputGitTreeElement] = []

        for change in state.fix_result.changes:
            file_path = change.get("file", "")
            diff_content = change.get("diff", "")

            if not file_path or not diff_content:
                logger.warning("Skipping change with missing file or diff")
                continue

            # Get current file content from base branch
            try:
                file_content = repo.get_contents(file_path, ref=default_branch)
                if isinstance(file_content, list):
                    raise ValueError(f"Expected single file, got directory for {file_path}")
                current_content = file_content.decoded_content.decode("utf-8")
            except Exception as e:
                logger.warning(f"File {file_path} not found in base branch, assuming new file: {e}")
                current_content = ""

            # Apply unified diff to get new content
            # For simplicity, we'll use the patch command via subprocess
            import subprocess
            import tempfile

            with tempfile.TemporaryDirectory() as tmpdir:
                # Write current content
                current_file = f"{tmpdir}/current"
                with open(current_file, "w") as f:
                    f.write(current_content)

                # Write diff
                diff_file = f"{tmpdir}/fix.patch"
                with open(diff_file, "w") as f:
                    f.write(diff_content)

                # Apply patch
                patch_result = subprocess.run(
                    ["patch", "-o", f"{tmpdir}/patched", current_file, diff_file],
                    capture_output=True,
                    text=True,
                )

                if patch_result.returncode != 0:
                    err = patch_result.stderr
                    logger.warning(
                        f"Patch failed for {file_path}, trying alternative method: {err}"
                    )
                    # Fallback: try patch stdin
                    subprocess.run(["cp", current_file, f"{tmpdir}/patched"], check=True)
                    patch_result2 = subprocess.run(
                        ["patch", f"{tmpdir}/patched"],
                        input=diff_content,
                        capture_output=True,
                        text=True,
                        cwd=tmpdir,
                    )

                    if patch_result2.returncode != 0:
                        logger.error(
                            f"Failed to apply patch for {file_path}: {patch_result2.stderr}"
                        )
                        continue

                # Read patched content
                with open(f"{tmpdir}/patched") as f:
                    new_content = f.read()

            # Create blob for new content
            blob = repo.create_git_blob(new_content, "utf-8")

            # Add to tree elements
            tree_elements.append(
                InputGitTreeElement(
                    path=file_path,
                    mode="100644",  # Regular file
                    type="blob",
                    sha=blob.sha,
                )
            )
            logger.info(f"Created blob for {file_path}: {blob.sha[:8]}")

        if not tree_elements:
            logger.error("No tree elements created, cannot commit")
            return None

        # Create new tree
        new_tree = repo.create_git_tree(tree_elements, base_tree)
        logger.info(f"Created tree: {new_tree.sha[:8]}")

        # Create commit
        commit_message = (
            f"Fix {state.issue_id}: {state.issue_title}\n\nAutomated fix by Mini Debug Assist agent"
        )
        new_commit = repo.create_git_commit(
            message=commit_message,
            tree=new_tree,
            parents=[base_commit],
        )
        logger.info(f"Created commit: {new_commit.sha[:8]}")

        # Update branch ref to point to new commit
        ref.edit(sha=new_commit.sha, force=False)
        logger.info(f"Updated branch {branch_name} to commit {new_commit.sha[:8]}")

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
            body += change["diff"]
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

*This PR was automatically generated by the Debug Assist agent. Please review*
*carefully before merging.*
"""

    return body
