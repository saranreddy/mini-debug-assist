"""
Tests for agent graph and subagent orchestration.

Tests the parallel RCA subagent fan-out and consolidation logic.
"""

from agent.config import AgentConfig
from agent.graph import run_debug_agent
from agent.state import AgentState, FixResult, RCAResult, SubagentResult


class TestSubagentFanOut:
    """Test parallel subagent execution and consolidation."""

    def test_subagents_all_agree_high_confidence(self):
        """
        When all subagents agree with high confidence, should proceed to fix.
        """
        # Issue data
        issue_data = {
            "issue_id": "TEST-AGREE",
            "title": "Test agreement case",
            "exception_type": "KeyError",
            "exception_message": "'email'",
            "stack_trace": "KeyError: 'email'",
            "breadcrumbs": [
                {"action": "GET /user/1", "result": "200"},
                {"action": "GET /user/3", "result": "500"},
            ],
        }

        # Run agent
        config = AgentConfig(mode="mock")
        state = run_debug_agent(
            issue_id="TEST-AGREE",
            issue_title="Test agreement case",
            issue_data=issue_data,
            config=config,
        )

        # Check that subagents ran
        assert "breadcrumbs" in state.get("subagent_results", {})
        assert "flag_correlation" in state.get("subagent_results", {})
        assert "offending_commit" in state.get("subagent_results", {})

        # Check that we didn't escalate (subagents agreed)
        assert not state.get(
            "needs_human_escalation", False
        ), "Should not escalate when subagents agree"

        # Check that fix was attempted
        assert state.get("fix_result") is not None, "Should proceed to fix when subagents agree"

    def test_subagents_disagree_low_confidence(self):
        """
        When subagents disagree or have low confidence, should escalate.

        This tests the escalation path with evidence packet.
        """
        # Create a scenario that will cause disagreement
        # Mock mode will have varying confidence scores
        issue_data = {
            "issue_id": "TEST-DISAGREE",
            "title": "Test disagreement case",
            "exception_type": "UnknownError",  # Will cause low confidence
            "exception_message": "Something weird",
            "stack_trace": "Unknown: weird",
            "breadcrumbs": [],
        }

        # Run agent
        config = AgentConfig(mode="mock")

        # Manually create state to simulate disagreement
        state = AgentState(
            issue_id="TEST-DISAGREE",
            issue_title="Test disagreement case",
            issue_data=issue_data,
        )

        # Simulate subagent results with disagreement
        state.rca_result = RCAResult(
            category="code_bug",
            requires_code_fix=True,
            confidence=0.5,  # Low confidence
            root_cause="Unknown cause",
            summary="Unclear",
            evidence=[],
        )

        state.subagent_results = {
            "breadcrumbs": SubagentResult(
                subagent_type="breadcrumbs",
                hypothesis="Might be data issue",
                confidence=0.4,
                evidence=["Insufficient breadcrumbs"],
            ),
            "flag_correlation": SubagentResult(
                subagent_type="flag_correlation",
                hypothesis="Could be config",
                confidence=0.3,
                evidence=["No clear pattern"],
            ),
            "offending_commit": SubagentResult(
                subagent_type="offending_commit",
                hypothesis="Maybe recent deploy",
                confidence=0.6,
                evidence=["Recent changes found"],
            ),
        }

        # Run consolidator
        from agent.nodes.consolidator import consolidator_node

        state = consolidator_node(state, config)

        # Should escalate due to low confidence
        assert state.needs_human_escalation, "Should escalate when confidence is low"

        assert (
            "confidence" in state.escalation_reason.lower()
        ), "Escalation reason should mention confidence"

        assert (
            "evidence packet" in state.escalation_reason.lower()
        ), "Escalation should include evidence packet"


class TestSymptomHiding:
    """Test symptom-hiding detection in validation."""

    def test_reject_try_except_pass(self):
        """
        Should reject patches that add 'try/except: pass' without logging.

        This masks errors instead of fixing them.
        """
        # Create state with a symptom-hiding fix
        state = AgentState(
            issue_id="TEST-SYMPTOM",
            issue_title="Symptom hiding test",
            issue_data={},
        )

        state.fix_result = FixResult(
            fix_applied=True,
            changes=[
                {
                    "file": "demo_app/main.py",
                    "diff": """--- a/demo_app/main.py
+++ b/demo_app/main.py
@@ -150,7 +150,10 @@ async def get_user(user_id: str):
     
     user = USERS_DB[user_id]
     
-    email = user["email"]
+    try:
+        email = user["email"]
+    except:
+        pass
     
     logger.info(f"User retrieved")
""",
                }
            ],
        )

        # Run validation
        from agent.nodes.validate import _check_symptom_hiding

        issues = _check_symptom_hiding(state)

        # Should detect symptom hiding
        assert len(issues) > 0, "Should detect try/except: pass as symptom hiding"

        assert (
            "symptom hiding" in issues[0].lower() or "swallows" in issues[0].lower()
        ), "Should describe the issue as symptom hiding"

    def test_accept_try_except_with_logging(self):
        """
        Should accept try/except if it includes proper logging.
        """
        state = AgentState(
            issue_id="TEST-GOOD",
            issue_title="Good error handling",
            issue_data={},
        )

        state.fix_result = FixResult(
            fix_applied=True,
            changes=[
                {
                    "file": "demo_app/main.py",
                    "diff": """--- a/demo_app/main.py
+++ b/demo_app/main.py
@@ -150,7 +150,12 @@ async def get_user(user_id: str):
     
     user = USERS_DB[user_id]
     
-    email = user["email"]
+    try:
+        email = user["email"]
+    except KeyError:
+        logger.warning(f"User {user_id} has no email field")
+        email = None
     
     logger.info(f"User retrieved")
""",
                }
            ],
        )

        # Run validation
        from agent.nodes.validate import _check_symptom_hiding

        issues = _check_symptom_hiding(state)

        # Should not flag proper error handling
        assert len(issues) == 0, "Should not flag try/except with proper logging"

    def test_reject_silent_pass(self):
        """
        Should reject patches that add 'pass' without explanation.
        """
        state = AgentState(
            issue_id="TEST-PASS",
            issue_title="Silent pass test",
            issue_data={},
        )

        state.fix_result = FixResult(
            fix_applied=True,
            changes=[
                {
                    "file": "demo_app/main.py",
                    "diff": """--- a/demo_app/main.py
+++ b/demo_app/main.py
@@ -150,6 +150,8 @@ async def get_user(user_id: str):
     
     if user_id not in USERS_DB:
+        pass
+        
     user = USERS_DB[user_id]
""",
                }
            ],
        )

        # Run validation
        from agent.nodes.validate import _check_symptom_hiding

        issues = _check_symptom_hiding(state)

        # Should detect silent pass
        assert len(issues) > 0, "Should detect silent pass statements"


class TestSubagentConfidence:
    """Test confidence scoring and agreement metrics."""

    def test_high_agreement_score(self):
        """Test agreement calculation when subagents have similar confidence."""
        state = AgentState(
            issue_id="TEST",
            issue_title="Test",
            issue_data={},
        )

        state.rca_result = RCAResult(
            category="code_bug",
            requires_code_fix=True,
            confidence=0.9,
            root_cause="Test",
            summary="Test",
        )

        state.subagent_results = {
            "breadcrumbs": SubagentResult(
                subagent_type="breadcrumbs",
                hypothesis="Test",
                confidence=0.85,
                evidence=[],
            ),
            "flag_correlation": SubagentResult(
                subagent_type="flag_correlation",
                hypothesis="Test",
                confidence=0.88,
                evidence=[],
            ),
            "offending_commit": SubagentResult(
                subagent_type="offending_commit",
                hypothesis="Test",
                confidence=0.92,
                evidence=[],
            ),
        }

        from agent.nodes.consolidator import _check_subagent_agreement

        agreement = _check_subagent_agreement(state)

        # Should have high agreement (similar confidences)
        assert agreement > 0.8, f"Expected high agreement, got {agreement:.2f}"

    def test_low_agreement_score(self):
        """Test agreement calculation when subagents disagree."""
        state = AgentState(
            issue_id="TEST",
            issue_title="Test",
            issue_data={},
        )

        state.rca_result = RCAResult(
            category="code_bug",
            requires_code_fix=True,
            confidence=0.9,
            root_cause="Test",
            summary="Test",
        )

        state.subagent_results = {
            "breadcrumbs": SubagentResult(
                subagent_type="breadcrumbs",
                hypothesis="Test",
                confidence=0.3,  # Low
                evidence=[],
            ),
            "flag_correlation": SubagentResult(
                subagent_type="flag_correlation",
                hypothesis="Test",
                confidence=0.95,  # High
                evidence=[],
            ),
            "offending_commit": SubagentResult(
                subagent_type="offending_commit",
                hypothesis="Test",
                confidence=0.5,  # Medium
                evidence=[],
            ),
        }

        from agent.nodes.consolidator import _check_subagent_agreement

        agreement = _check_subagent_agreement(state)

        # Should have low agreement (varying confidences)
        # Note: With variance 0.0756, agreement = 0.70 (borderline)
        assert agreement < 0.75, f"Expected low agreement due to variance, got {agreement:.2f}"
