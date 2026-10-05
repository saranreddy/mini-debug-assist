"""
Validate Node (LLM: Claude Sonnet, max 20 turns)

Maps to Uber's bazel_test node:
- Runs tests in a sandbox
- Checks if tests pass or reproduce the crash
- Feeds failures back to fix node with bounded retries
- Rejects symptom-hiding patches (broad try/except)

Uber runs Bazel tests plus emulator/simulator validation.
"""

import logging
import subprocess

from agent.config import AgentConfig
from agent.state import AgentState, ValidationResult

logger = logging.getLogger(__name__)


def validate_node(state: AgentState, config: AgentConfig) -> AgentState:
    """
    Validate the fix by running tests.
    
    In mock mode: simulates test run
    In real mode: actually runs pytest in a sandbox
    
    Uber's setup:
    - Runs Bazel unit/integration tests
    - Runs mobile E2E on emulators with mocked network/battery/flags
    - Bounded retry loop: if tests fail, feed output back to fix node
    - Guard against symptom-hiding: reject overly broad try/except
    """
    logger.info(f"Validating fix for issue {state.issue_id}")
    
    state.validation_attempts += 1
    
    if config.mode == "mock":
        # Mock mode: simulate successful validation
        state.validation_result = _mock_validation_result(state)
        logger.info(f"Validation (mock): {'PASSED' if state.validation_result.passed else 'FAILED'}")
    else:
        # Real mode: actually run tests
        state.validation_result = _run_tests(state, config)
        logger.info(f"Validation: {'PASSED' if state.validation_result.passed else 'FAILED'}")
    
    return state


def _mock_validation_result(state: AgentState) -> ValidationResult:
    """
    Generate mock validation result.
    
    For learning: simulates test execution.
    """
    # In mock mode, assume the fix is correct
    return ValidationResult(
        passed=True,
        test_output="All tests passed (mock mode)",
        issues=[],
    )


def _run_tests(state: AgentState, config: AgentConfig) -> ValidationResult:
    """
    Actually run pytest to validate the fix.
    
    This would:
    1. Apply the fix to the codebase
    2. Run pytest in a sandbox
    3. Parse test output
    4. Check for symptom-hiding patterns
    """
    try:
        # Run pytest
        result = subprocess.run(
            ["pytest", "-v", "--tb=short"],
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout
        )
        
        passed = result.returncode == 0
        test_output = result.stdout + result.stderr
        
        # Check for symptom-hiding patterns
        issues = _check_symptom_hiding(state)
        
        if issues:
            passed = False
        
        return ValidationResult(
            passed=passed,
            test_output=test_output,
            issues=issues,
        )
        
    except subprocess.TimeoutExpired:
        logger.error("Test execution timed out")
        return ValidationResult(
            passed=False,
            test_output="Test execution timed out after 5 minutes",
            issues=["Timeout - possible infinite loop or hanging test"],
        )
        
    except Exception as e:
        logger.error(f"Error running tests: {e}")
        return ValidationResult(
            passed=False,
            test_output=str(e),
            issues=[f"Test execution error: {e}"],
        )


def _check_symptom_hiding(state: AgentState) -> list[str]:
    """
    Check if the fix is hiding symptoms rather than fixing root cause.
    
    Uber's validation includes guards against:
    - Broad try/except that swallows errors
    - Defensive checks that hide the real issue
    - Returning default values without logging
    
    This is a simplified check for learning.
    """
    issues = []
    
    if state.fix_result is None:
        return issues
    
    for change in state.fix_result.changes:
        diff = change.get("diff", "")
        
        # Check for overly broad exception handling
        if "except:" in diff or "except Exception:" in diff:
            # Make sure there's proper logging or re-raise
            if "logger" not in diff and "raise" not in diff:
                issues.append(
                    f"File {change['file']}: Broad exception handling without logging"
                )
        
        # Check for silent failures
        if "pass" in diff and "#" not in diff:
            issues.append(
                f"File {change['file']}: Silent failure (pass without comment/logging)"
            )
    
    return issues
