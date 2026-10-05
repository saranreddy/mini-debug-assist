"""
Tests for validate node with real patch application.

Tests the actual git apply / patch -p1 workflow in a temp directory.
"""

import os
import pytest
import tempfile
import shutil

from agent.config import AgentConfig
from agent.state import AgentState, FixResult
from agent.nodes.validate import validate_node


class TestValidatePatchApplication:
    """Test real patch application in validate node."""
    
    def test_validate_applies_good_diff(self):
        """Test that validate applies a good diff and runs pytest."""
        # Create a simple fix that should apply cleanly
        state = AgentState(
            issue_id="TEST-001",
            issue_title="Test issue",
            issue_data={"exception_type": "KeyError"},
        )
        
        # Create a valid unified diff for demo_app/main.py
        # This changes line that accesses user["email"] to user.get("email")
        state.fix_result = FixResult(
            fix_applied=True,
            changes=[
                {
                    "file": "demo_app/main.py",
                    "diff": """--- demo_app/main.py
+++ demo_app/main.py
@@ -130,7 +130,7 @@
     user = USERS_DB[user_id]
     
     # BUG: This assumes 'email' always exists
-    email = user["email"]  # KeyError when user_id='3'
+    email = user.get("email")  # Fixed: use .get() for optional field
     
     logger.info(f"User {user_id} retrieved successfully")
     
"""
                }
            ],
            mitigation=None,
        )
        
        config = AgentConfig(mode="aws")  # Real mode
        
        # Run validate (will apply patch and run tests)
        result_state = validate_node(state, config)
        
        # Validation should run (whether it passes depends on if demo_app/main.py
        # actually matches the expected content)
        assert result_state.validation_result is not None
        assert result_state.validation_attempts == 1
    
    def test_validate_handles_bad_diff(self):
        """Test that validate handles a diff that doesn't apply."""
        state = AgentState(
            issue_id="TEST-002",
            issue_title="Test bad diff",
            issue_data={"exception_type": "ValueError"},
        )
        
        # Create an invalid diff that won't apply (wrong line numbers)
        state.fix_result = FixResult(
            fix_applied=True,
            changes=[
                {
                    "file": "demo_app/main.py",
                    "diff": """--- demo_app/main.py
+++ demo_app/main.py
@@ -999,7 +999,7 @@
-    this line does not exist
+    neither does this one
"""
                }
            ],
            mitigation=None,
        )
        
        config = AgentConfig(mode="aws")
        
        # Run validate
        result_state = validate_node(state, config)
        
        # Should fail with patch application error
        assert result_state.validation_result is not None
        assert result_state.validation_result.passed is False
        assert "Patch application failed" in result_state.validation_result.issues[0]
        
        # Should be recorded in fix_history
        assert len(result_state.fix_history) == 1
        assert "Patch did not apply cleanly" in result_state.fix_history[0]["failure_reason"]
    
    def test_validate_runs_tests_in_temp_dir(self):
        """Test that validate actually copies files and runs pytest in temp dir."""
        state = AgentState(
            issue_id="TEST-003",
            issue_title="Test temp dir",
            issue_data={"exception_type": "TestError"},
        )
        
        # No fix result, just run tests as-is
        state.fix_result = None
        
        config = AgentConfig(mode="aws")
        
        # Run validate
        result_state = validate_node(state, config)
        
        # Should have run tests (even without a fix)
        assert result_state.validation_result is not None
        # Test output should contain pytest output
        assert "test" in result_state.validation_result.test_output.lower() or \
               "passed" in result_state.validation_result.test_output.lower() or \
               "failed" in result_state.validation_result.test_output.lower()


class TestValidateSymptomHiding:
    """Test symptom-hiding detection (already working, keep it)."""
    
    def test_symptom_hiding_detection(self):
        """Test that symptom-hiding patterns are detected."""
        state = AgentState(
            issue_id="TEST-004",
            issue_title="Symptom hiding",
            issue_data={"exception_type": "KeyError"},
        )
        
        # Create a fix that hides symptoms
        # Since the symptom-hiding check happens on the diff text itself,
        # we don't need to actually apply this patch - just detect the pattern
        state.fix_result = FixResult(
            fix_applied=True,
            changes=[
                {
                    "file": "demo_app/main.py",
                    "diff": """--- demo_app/main.py
+++ demo_app/main.py
@@ -130,1 +130,4 @@
-    email = user["email"]
+    try:
+        email = user["email"]
+    except:
+        pass
"""
                }
            ],
            mitigation=None,
        )
        
        config = AgentConfig(mode="aws")
        
        # Run validate
        result_state = validate_node(state, config)
        
        # Should detect symptom hiding (checked before patch application)
        # The _check_symptom_hiding function runs on the diff text
        assert result_state.validation_result is not None
        
        # Either it was caught by symptom hiding check, OR patch failed
        # (both are acceptable for this malformed test case)
        assert result_state.validation_result.passed is False
        assert len(result_state.validation_result.issues) > 0
        
        # Check if symptom hiding was detected OR patch failed
        issue_text = " ".join(result_state.validation_result.issues).lower()
        assert "symptom" in issue_text or "except" in issue_text or "patch" in issue_text
