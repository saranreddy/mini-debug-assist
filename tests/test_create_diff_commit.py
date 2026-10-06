"""
Tests for create_diff node with real GitHub commit via API.

Uses mocked PyGithub client to test the commit logic.
"""

from unittest.mock import MagicMock, patch

from agent.config import AgentConfig
from agent.nodes.create_diff import create_diff_node
from agent.state import AgentState, FixResult


class TestCreateDiffCommit:
    """Test GitHub commit logic in create_diff node."""

    @patch("github.Github")
    def test_create_diff_commits_changes(self, mock_github_class):
        """create_diff applies the diff with real `git apply` and commits via the GitHub API."""
        # Setup state with fix result
        state = AgentState(
            issue_id="TEST-101",
            issue_title="Test commit",
            issue_data={"exception_type": "KeyError", "alarm_event_id": "c4c1c1c9-6542"},
        )

        state.fix_result = FixResult(
            fix_applied=True,
            changes=[
                {
                    "file": "demo_app/main.py",
                    "diff": """--- a/demo_app/main.py
+++ b/demo_app/main.py
@@ -2,1 +2,1 @@
-    email = user["email"]
+    email = user.get("email")
""",
                }
            ],
            mitigation=None,
        )

        # Mock GitHub API
        mock_gh = MagicMock()
        mock_github_class.return_value = mock_gh

        mock_repo = MagicMock()
        mock_gh.get_repo.return_value = mock_repo

        mock_repo.default_branch = "main"

        mock_branch = MagicMock()
        mock_branch.commit.sha = "abc123"
        mock_repo.get_branch.return_value = mock_branch

        # Mock create_git_ref
        mock_ref = MagicMock()
        mock_repo.create_git_ref.return_value = mock_ref

        # Mock get file contents
        mock_file = MagicMock()
        mock_file.decoded_content = (
            b"""def get_user(user):\n    email = user["email"]\n    return email\n"""
        )
        mock_repo.get_contents.return_value = mock_file

        # Mock blob creation
        mock_blob = MagicMock()
        mock_blob.sha = "blob456"
        mock_repo.create_git_blob.return_value = mock_blob

        # Mock tree creation
        mock_base_tree = MagicMock()
        mock_base_tree.sha = "tree789"

        mock_commit = MagicMock()
        mock_commit.tree = mock_base_tree
        mock_repo.get_git_commit.return_value = mock_commit

        mock_new_tree = MagicMock()
        mock_new_tree.sha = "newtree123"
        mock_repo.create_git_tree.return_value = mock_new_tree

        # Mock commit creation
        mock_new_commit = MagicMock()
        mock_new_commit.sha = "newcommit456"
        mock_repo.create_git_commit.return_value = mock_new_commit

        # Mock PR creation
        mock_pr = MagicMock()
        mock_pr.html_url = "https://github.com/test/repo/pull/42"
        mock_repo.create_pull.return_value = mock_pr

        # Config
        config = AgentConfig(mode="aws", github_token="fake_token", github_repo="test/repo")

        # Run create_diff
        result_state = create_diff_node(state, config)

        # Verify PR was created
        assert result_state.pr_url == "https://github.com/test/repo/pull/42"

        # Verify GitHub API calls
        mock_gh.get_repo.assert_called_once_with("test/repo")
        mock_repo.create_git_ref.assert_called_once()
        mock_repo.create_git_blob.assert_called_once_with(
            'def get_user(user):\n    email = user.get("email")\n    return email\n', "utf-8"
        )
        # Unique branch per alarm event
        branch_ref = mock_repo.create_git_ref.call_args.kwargs["ref"]
        assert branch_ref == "refs/heads/fix/debug-assist-test-101-c4c1c1c9"
        assert (
            mock_repo.create_pull.call_args.kwargs["head"] == "fix/debug-assist-test-101-c4c1c1c9"
        )
        mock_repo.create_git_tree.assert_called_once()
        mock_repo.create_git_commit.assert_called_once()
        mock_ref.edit.assert_called_once()
        mock_repo.create_pull.assert_called_once()

    def test_create_diff_requires_github_creds(self):
        """Test that create_diff skips PR creation without credentials."""
        state = AgentState(
            issue_id="TEST-102",
            issue_title="Test no creds",
            issue_data={},
        )

        state.fix_result = FixResult(
            fix_applied=True,
            changes=[{"file": "test.py", "diff": "some diff"}],
            mitigation=None,
        )

        # No GitHub credentials
        config = AgentConfig(mode="aws", github_token=None, github_repo=None)

        # Run create_diff
        result_state = create_diff_node(state, config)

        # Should not create PR
        assert result_state.pr_url is None

    def test_create_diff_skips_without_changes(self):
        """Test that create_diff skips PR creation without changes."""
        state = AgentState(
            issue_id="TEST-103",
            issue_title="Test no changes",
            issue_data={},
        )

        # No fix result
        state.fix_result = None

        config = AgentConfig(mode="aws", github_token="fake_token", github_repo="test/repo")

        # Run create_diff
        result_state = create_diff_node(state, config)

        # Should not create PR
        assert result_state.pr_url is None
