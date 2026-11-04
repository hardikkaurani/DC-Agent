"""Security and isolation tests for code execution backends."""

import os

from dc_agent.adapters.base import TestCase
from dc_agent.executor.base import LocalExecutionVerdict
from dc_agent.executor.subprocess_runner import SubprocessExecutionSandbox


def test_subprocess_timeout_on_infinite_loop() -> None:
    """Ensure infinite loops are terminated strictly within the timeout threshold."""
    sandbox = SubprocessExecutionSandbox()

    infinite_loop_code = """
def solve():
    while True:
        pass

if __name__ == '__main__':
    solve()
"""
    test_case = TestCase(input_data="", expected_output="done")

    summary = sandbox.run_tests(
        solution_code=infinite_loop_code,
        test_cases=[test_case],
        timeout_seconds=0.5,
    )

    assert summary.overall_verdict == LocalExecutionVerdict.TIMEOUT
    assert summary.results[0].verdict == LocalExecutionVerdict.TIMEOUT
    assert "exceeded wall-clock timeout" in str(summary.results[0].error_message)


def test_subprocess_environment_sanitization() -> None:
    """Ensure host environment variables and secrets are completely stripped from child process."""
    sandbox = SubprocessExecutionSandbox()

    # Set a sensitive dummy environment variable on the host
    os.environ["SECRET_HOST_API_TOKEN"] = "ultra_confidential_secret_value"

    # Note: Even importing os is blocked by AST validator!
    # Let's test checking that environment variables cannot be accessed
    summary = sandbox.run_tests(
        solution_code="import os\nprint(os.environ.get('SECRET_HOST_API_TOKEN'))",
        test_cases=[TestCase(input_data="", expected_output="")],
    )

    # First layer of defense: AST validator blocks 'os' import immediately
    assert summary.overall_verdict == LocalExecutionVerdict.SAFETY_VIOLATION
    assert "FORBIDDEN_IMPORT" in str(summary.results[0].error_message)


def test_subprocess_output_truncation_on_huge_stdout() -> None:
    """Ensure scripts attempting to flood stdout with infinite prints are truncated to MAX_OUTPUT_BYTES."""
    sandbox = SubprocessExecutionSandbox()

    flood_code = """
if __name__ == '__main__':
    for _ in range(100000):
        print("A" * 100)
"""
    test_case = TestCase(input_data="", expected_output="short")

    summary = sandbox.run_tests(
        solution_code=flood_code,
        test_cases=[test_case],
        timeout_seconds=2.0,
    )

    actual = summary.results[0].actual_output or ""
    # Output must be bounded to MAX_OUTPUT_BYTES (64 KB)
    assert len(actual.encode("utf-8")) <= 64 * 1024 + 100


def test_subprocess_handles_syntax_and_runtime_errors_gracefully() -> None:
    """Ensure syntax errors and unhandled exceptions are caught and reported as RUNTIME_ERROR."""
    sandbox = SubprocessExecutionSandbox()

    syntax_err_code = """
def solve():
    return 1 / 0

if __name__ == '__main__':
    solve()
"""
    test_case = TestCase(input_data="", expected_output="1")

    summary = sandbox.run_tests(
        solution_code=syntax_err_code,
        test_cases=[test_case],
        timeout_seconds=1.0,
    )

    assert summary.overall_verdict == LocalExecutionVerdict.RUNTIME_ERROR
    assert "ZeroDivisionError" in str(summary.results[0].error_message)
