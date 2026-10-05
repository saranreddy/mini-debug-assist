"""
Tests for fix retry feedback loop.

Validates that fix node includes previous attempt failures in prompts.
"""

from unittest.mock import Mock, patch

import pytest

from agent.config import AgentConfig
from agent.nodes.fix import _generate_fix_with_llm
from agent.state import AgentState, RCAResult, FixResult


class TestFixRetryFeedback:
    """Test fix retry feedback with validation history."""
    
    @patch("agent.llm.invoke_with_tools")
    def test_first_attempt_no_history(self, mock_invoke):
        """Test that first attempt has no previous history in prompt."""
        # Mock LLM response
        mock_invoke.return_value = (
            {
                "text": '{"fix_applied": true, "changes": [{"file": "test.py", "diff": "mock diff"}]}'
            },
            5
        )
        
        # State with no fix history
        state = AgentState(
            issue_id="TEST-001",
            issue_title="Test error",
            issue_data={},
            logs=[],
            traces=[],
            code_context={},
            rca_result=RCAResult(
                category="code_bug",
                requires_code_fix=True,
                confidence=0.9,
                root_cause="Test root cause",
                summary="Test",
                evidence=[],
            ),
            fix_history=[],  # Empty - first attempt
        )
        
        config = AgentConfig(mode="aws")
        
        result, turns = _generate_fix_with_llm(state, config)
        
        # Check that prompt was called
        assert mock_invoke.called
        
        # Get the user message passed to LLM
        call_args = mock_invoke.call_args
        messages = call_args[1]["messages"]
        user_message = messages[0]["content"][0]["text"]
        
        # First attempt should NOT have "PREVIOUS FIX ATTEMPTS"
        assert "PREVIOUS FIX ATTEMPTS" not in user_message
        assert "FAILED" not in user_message
    
    @patch("agent.llm.invoke_with_tools")
    def test_second_attempt_includes_failure(self, mock_invoke):
        """Test that second attempt includes first attempt's failure in prompt."""
        # Mock LLM response
        mock_invoke.return_value = (
            {
                "text": '{"fix_applied": true, "changes": [{"file": "test.py", "diff": "mock diff 2"}]}'
            },
            5
        )
        
        # State with fix history (first attempt failed)
        state = AgentState(
            issue_id="TEST-002",
            issue_title="Test error",
            issue_data={},
            logs=[],
            traces=[],
            code_context={},
            rca_result=RCAResult(
                category="code_bug",
                requires_code_fix=True,
                confidence=0.9,
                root_cause="Test root cause",
                summary="Test",
                evidence=[],
            ),
            fix_history=[
                {
                    "attempt": 1,
                    "diff": "--- a/test.py\n+++ b/test.py\n@@ -1,1 +1,1 @@\n-old line\n+new line",
                    "test_output": "FAILED tests/test_file.py::test_function - AssertionError: expected 42, got 0",
                    "failure_reason": "Tests failed",
                    "issues": [],
                }
            ],
        )
        
        config = AgentConfig(mode="aws")
        
        result, turns = _generate_fix_with_llm(state, config)
        
        # Check that prompt was called
        assert mock_invoke.called
        
        # Get the user message passed to LLM
        call_args = mock_invoke.call_args
        messages = call_args[1]["messages"]
        user_message = messages[0]["content"][0]["text"]
        
        # Second attempt MUST include previous failure
        assert "PREVIOUS FIX ATTEMPTS" in user_message
        assert "FAILED" in user_message
        assert "Attempt 1" in user_message
        
        # Should include the diff from attempt 1
        assert "old line" in user_message or "new line" in user_message
        
        # Should include the test output
        assert "test_function" in user_message or "AssertionError" in user_message
        
        # Should include failure reason
        assert "Tests failed" in user_message
        
        # Should have learning prompt
        assert "Learn from previous failures" in user_message
    
    @patch("agent.llm.invoke_with_tools")
    def test_third_attempt_includes_two_failures(self, mock_invoke):
        """Test that third attempt includes both previous failures."""
        # Mock LLM response
        mock_invoke.return_value = (
            {
                "text": '{"fix_applied": true, "changes": [{"file": "test.py", "diff": "mock diff 3"}]}'
            },
            5
        )
        
        # State with two previous failed attempts
        state = AgentState(
            issue_id="TEST-003",
            issue_title="Test error",
            issue_data={},
            logs=[],
            traces=[],
            code_context={},
            rca_result=RCAResult(
                category="code_bug",
                requires_code_fix=True,
                confidence=0.9,
                root_cause="Test root cause",
                summary="Test",
                evidence=[],
            ),
            fix_history=[
                {
                    "attempt": 1,
                    "diff": "first attempt diff",
                    "test_output": "first failure output",
                    "failure_reason": "Tests failed",
                    "issues": [],
                },
                {
                    "attempt": 2,
                    "diff": "second attempt diff",
                    "test_output": "second failure output",
                    "failure_reason": "Symptom hiding: broad except",
                    "issues": ["Symptom hiding detected"],
                },
            ],
        )
        
        config = AgentConfig(mode="aws")
        
        result, turns = _generate_fix_with_llm(state, config)
        
        # Get the user message
        call_args = mock_invoke.call_args
        messages = call_args[1]["messages"]
        user_message = messages[0]["content"][0]["text"]
        
        # Should include both attempts
        assert "Attempt 1" in user_message
        assert "Attempt 2" in user_message
        
        # Should include both failure reasons
        assert "first failure output" in user_message
        assert "second failure output" in user_message
        
        # Should include symptom hiding reason
        assert "Symptom hiding" in user_message or "broad except" in user_message
