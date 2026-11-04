"""Unit tests for SolverService, CorrectionEngine, and code harnesses."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from dc_agent.adapters.base import TestCase
from dc_agent.core.correction import CorrectionEngine
from dc_agent.core.harness import wrap_leetcode_code
from dc_agent.core.solver import SolverService
from dc_agent.executor.base import ExecutionSummary, LocalExecutionVerdict, TestCaseResult
from dc_agent.providers.schemas import DiagnosticReport, GeneratedSolution, SolutionReview


def test_wrap_leetcode_code_appends_driver() -> None:
    """Ensure LeetCode solution class is wrapped with stdin driver."""
    solution = "class Solution:\n    def twoSum(self, nums, target):\n        return [0, 1]"
    wrapped = wrap_leetcode_code(solution)

    assert "class Solution:" in wrapped
    assert "if __name__ == '__main__':" in wrapped
    assert "sys.stdin" in wrapped

    # If code already contains __main__, do not duplicate
    already_wrapped = wrap_leetcode_code(wrapped)
    assert already_wrapped == wrapped


@pytest.mark.asyncio
async def test_solver_service_generates_and_reviews() -> None:
    """Ensure SolverService orchestrates generation and review."""
    mock_provider = MagicMock()
    mock_provider.provider_name = "mock_ai"
    mock_provider.model_name = "test_model"

    mock_sol = GeneratedSolution(
        problem_understanding="Understand",
        algorithm="Binary Search",
        intuition="Divide and conquer",
        python_code="def solve(): pass",
        time_complexity="O(log N)",
        space_complexity="O(1)",
    )
    mock_rev = SolutionReview(
        is_correct=True,
        correctness_assessment="Correct",
        complexity_assessment="Optimal",
    )

    mock_provider.generate_solution = AsyncMock(return_value=mock_sol)
    mock_provider.review_solution = AsyncMock(return_value=mock_rev)

    service = SolverService(provider=mock_provider)
    sol, rev, code = await service.solve_problem(
        platform="codeforces",
        title="Sample",
        description="Sample Desc",
    )

    assert sol.algorithm == "Binary Search"
    assert rev.is_correct is True
    assert code == "def solve(): pass"


@pytest.mark.asyncio
async def test_correction_engine_fixes_failing_code() -> None:
    """Ensure CorrectionEngine requests diagnosis and returns corrected code when tests fail."""
    mock_provider = MagicMock()
    mock_sandbox = MagicMock()

    # Initial test execution fails
    fail_summary = ExecutionSummary(
        overall_verdict=LocalExecutionVerdict.FAIL,
        total_tests=1,
        passed_tests=0,
        failed_tests=1,
        results=[
            TestCaseResult(
                test_index=1,
                verdict=LocalExecutionVerdict.FAIL,
                input_data="1",
                expected_output="2",
                actual_output="1",
                error_message="Mismatch",
            )
        ],
    )

    # Corrected test execution passes
    pass_summary = ExecutionSummary(
        overall_verdict=LocalExecutionVerdict.PASS,
        total_tests=1,
        passed_tests=1,
        failed_tests=0,
        results=[
            TestCaseResult(
                test_index=1,
                verdict=LocalExecutionVerdict.PASS,
                input_data="1",
                expected_output="2",
                actual_output="2",
            )
        ],
    )

    mock_sandbox.run_tests = MagicMock(side_effect=[fail_summary, pass_summary])

    diag = DiagnosticReport(
        error_summary="Off by one",
        failing_test_analysis="Failed on 1",
        root_cause="Missing increment",
        suggested_fix="Add 1",
        corrected_code="print(int(input()) + 1)",
    )
    mock_provider.diagnose_and_fix = AsyncMock(return_value=diag)

    engine = CorrectionEngine(provider=mock_provider, sandbox=mock_sandbox)
    final_code, summary, reports = await engine.run_and_correct_loop(
        platform="codeforces",
        title="Test Problem",
        description="Desc",
        initial_code="print(input())",
        test_cases=[TestCase(input_data="1", expected_output="2")],
        max_retries=2,
    )

    assert summary.overall_verdict == LocalExecutionVerdict.PASS
    assert final_code == "print(int(input()) + 1)"
    assert len(reports) == 1
