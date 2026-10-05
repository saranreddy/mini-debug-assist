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
        logger.info(
            f"Validation (mock): {'PASSED' if state.validation_result.passed else 'FAILED'}"
        )
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

    1. Copy repo (demo_app + tests) to a temp directory
    2. Apply the diff using git apply or patch -p1
    3. Run pytest in that temp directory
    4. Parse test output
    5. Check for symptom-hiding patterns
    6. Store failure in fix_history if tests fail
    """
    import os
    import shutil
    import tempfile

    temp_dir = None

    try:
        # Create temp directory for testing
        temp_dir = tempfile.mkdtemp(prefix="debug-assist-validate-")
        logger.info(f"Created temp directory: {temp_dir}")

        # Copy demo_app and tests to temp dir
        workspace_root = os.getcwd()

        # Copy directories we need, ignoring .git, venv, __pycache__
        def ignore_patterns(directory, files):
            ignored = []
            for f in files:
                if f in [
                    ".git",
                    "venv",
                    "__pycache__",
                    ".pytest_cache",
                    ".mypy_cache",
                    "node_modules",
                ]:
                    ignored.append(f)
                elif f.endswith(".pyc") or f.endswith(".pyo"):
                    ignored.append(f)
            return ignored

        # Copy demo_app
        demo_src = os.path.join(workspace_root, "demo_app")
        demo_dst = os.path.join(temp_dir, "demo_app")
        if os.path.exists(demo_src):
            shutil.copytree(demo_src, demo_dst, ignore=ignore_patterns)
            logger.info(f"Copied demo_app to {demo_dst}")

        # Copy tests
        tests_src = os.path.join(workspace_root, "tests")
        tests_dst = os.path.join(temp_dir, "tests")
        if os.path.exists(tests_src):
            shutil.copytree(tests_src, tests_dst, ignore=ignore_patterns)
            logger.info(f"Copied tests to {tests_dst}")

        # Apply diff if present
        apply_success = True
        apply_error = ""

        if state.fix_result and state.fix_result.changes:
            for change in state.fix_result.changes:
                file_path = change.get("file", "")
                diff_content = change.get("diff", "")

                if not diff_content:
                    continue

                # Write diff to a temp file
                diff_file = os.path.join(temp_dir, "fix.patch")
                with open(diff_file, "w") as f:
                    f.write(diff_content)

                logger.info(f"Applying diff to {file_path}")

                # Try git apply --check first
                check_result = subprocess.run(
                    ["git", "apply", "--check", diff_file],
                    cwd=temp_dir,
                    capture_output=True,
                    text=True,
                )

                if check_result.returncode == 0:
                    # Apply patch with git apply
                    apply_result = subprocess.run(
                        ["git", "apply", diff_file],
                        cwd=temp_dir,
                        capture_output=True,
                        text=True,
                    )

                    if apply_result.returncode != 0:
                        apply_success = False
                        apply_error = f"git apply failed: {apply_result.stderr}"
                        logger.error(apply_error)
                        break
                    else:
                        logger.info(f"Successfully applied patch to {file_path}")
                else:
                    # Fallback to patch -p1
                    logger.warning(
                        f"git apply --check failed, trying patch -p1: {check_result.stderr}"
                    )
                    patch_result = subprocess.run(
                        ["patch", "-p1"],
                        input=diff_content,
                        cwd=temp_dir,
                        capture_output=True,
                        text=True,
                    )

                    if patch_result.returncode != 0:
                        apply_success = False
                        apply_error = f"patch -p1 failed: {patch_result.stderr}"
                        logger.error(apply_error)
                        break
                    else:
                        logger.info(f"Successfully applied patch (via patch -p1) to {file_path}")

        # If patch application failed, return early
        if not apply_success:
            issues = [f"Patch application failed: {apply_error}"]

            # Store in fix_history
            failure_entry = {
                "attempt": state.validation_attempts,
                "diff": "\n".join(
                    [
                        c.get("diff", "")
                        for c in (state.fix_result.changes if state.fix_result else [])
                    ]
                )[:1000],
                "test_output": apply_error[:1000],
                "failure_reason": "Patch did not apply cleanly",
                "issues": issues,
            }
            state.fix_history.append(failure_entry)

            return ValidationResult(
                passed=False,
                test_output=apply_error,
                issues=issues,
            )

        # Run pytest in temp directory
        logger.info("Running pytest in temp directory")
        result = subprocess.run(
            ["pytest", "-xvs", "--tb=short", "tests/"],
            cwd=temp_dir,
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

        # If validation failed, store in fix_history for retry feedback
        if not passed:
            failure_entry = {
                "attempt": state.validation_attempts,
                "diff": "\n".join(
                    [
                        c.get("diff", "")
                        for c in (state.fix_result.changes if state.fix_result else [])
                    ]
                )[:1000],
                "test_output": test_output[:1000],
                "failure_reason": (
                    "Tests failed" if result.returncode != 0 else f"Symptom hiding: {issues[0]}"
                ),
                "issues": issues,
            }
            state.fix_history.append(failure_entry)
            logger.warning(
                f"Validation failed, stored in fix_history (attempt {state.validation_attempts})"
            )

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
        logger.error(f"Error running tests: {e}", exc_info=True)
        return ValidationResult(
            passed=False,
            test_output=str(e),
            issues=[f"Test execution error: {e}"],
        )

    finally:
        # Clean up temp directory
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
                logger.info(f"Cleaned up temp directory: {temp_dir}")
            except Exception as e:
                logger.warning(f"Failed to clean up temp directory {temp_dir}: {e}")


def _check_symptom_hiding(state: AgentState) -> list[str]:
    """
    Check if the fix is hiding symptoms rather than fixing root cause.

    Uber's validation includes guards against:
    - Broad try/except that swallows errors
    - Defensive checks that hide the real issue
    - Returning default values without logging
    - Adding try/except: pass (silent failure)

    This prevents patches that mask errors instead of fixing them.
    """
    issues = []

    if state.fix_result is None:
        return issues

    for change in state.fix_result.changes:
        diff = change.get("diff", "")
        file_path = change.get("file", "unknown")

        # Split diff into added lines (start with +)
        added_lines = [
            line[1:].strip()
            for line in diff.split("\n")
            if line.startswith("+") and not line.startswith("+++")
        ]

        # Check 1: Bare except: or except Exception: without handling
        for i, line in enumerate(added_lines):
            if "except:" in line or "except Exception:" in line:
                # Look ahead for proper handling in next few lines
                next_lines = added_lines[i + 1 : i + 5] if i + 1 < len(added_lines) else []
                has_logging = any("log" in line.lower() for line in next_lines)
                has_raise = any("raise" in line for line in next_lines)
                has_pass = any(line.strip() == "pass" for line in next_lines)

                if has_pass and not has_logging:
                    issues.append(
                        f"File {file_path}: Symptom hiding - 'try/except: pass' "
                        f"swallows errors without logging. This masks the real issue."
                    )
                elif not has_logging and not has_raise:
                    issues.append(
                        f"File {file_path}: Broad exception handling without logging or re-raise"
                    )

        # Check 2: Silent pass statements added (without comment)
        for line in added_lines:
            if line.strip() == "pass" or line.endswith("pass"):
                # Check if there's a comment explaining it
                if "#" not in line:
                    issues.append(f"File {file_path}: Silent 'pass' without explanation")

        # Check 3: Returning None/default without investigation
        for i, line in enumerate(added_lines):
            if "return None" in line or "return ''" in line or "return []" in line:
                prev_lines = added_lines[max(0, i - 3) : i]
                # If returning default after except without logging, flag it
                if any("except" in line for line in prev_lines):
                    has_logging = any("log" in line.lower() for line in prev_lines)
                    if not has_logging:
                        issues.append(
                            f"File {file_path}: Returning default value after exception "
                            f"without logging root cause"
                        )

        # Check 4: Overly defensive conditionals that mask bugs
        # E.g., wrapping entire function in try/except
        if "try:" in diff:
            lines_with_try = [i for i, line in enumerate(added_lines) if "try:" in line]
            for try_idx in lines_with_try:
                # Check if function definition is right before try
                if try_idx > 0 and "def " in added_lines[try_idx - 1]:
                    issues.append(
                        f"File {file_path}: Wrapping entire function in try/except "
                        f"masks specific error location"
                    )

    return issues
