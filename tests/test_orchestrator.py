"""Integration tests for DailyOrchestrator."""

from unittest.mock import AsyncMock, patch

import pytest

from dc_agent.adapters.base import Platform, ProblemMetadata, TestCase
from dc_agent.config import DailyPolicyType, Settings
from dc_agent.core.orchestrator import DailyOrchestrator
from dc_agent.db.session import init_db
from dc_agent.providers.schemas import GeneratedSolution, SolutionReview


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        DATABASE_URL="sqlite:///:memory:",
        DAILY_POLICY=DailyPolicyType.ALTERNATING,
        CODEFORCES_USERNAME="hkvariant",
        LEETCODE_USERNAME="hardikkaurani_",
    )


def test_platform_alternation_policy(test_settings: Settings) -> None:
    """Ensure alternating policy switches deterministically based on day."""
    orchestrator = DailyOrchestrator(settings=test_settings)

    # 2026-01-01 (day 1 -> odd -> leetcode)
    assert orchestrator.determine_today_platform("2026-01-01") == "leetcode"
    # 2026-01-02 (day 2 -> even -> codeforces)
    assert orchestrator.determine_today_platform("2026-01-02") == "codeforces"


@pytest.mark.asyncio
async def test_orchestrator_lifecycle_with_mocked_provider(test_settings: Settings) -> None:
    """Ensure end-to-end orchestration completes, persists session, and is idempotent."""
    init_db(test_settings.database_url)
    orchestrator = DailyOrchestrator(settings=test_settings)

    # Mock problem metadata
    mock_problem = ProblemMetadata(
        platform=Platform.CODEFORCES,
        external_id="1890_A",
        title="Doremy's Paint 3",
        slug="1890-a",
        url="https://codeforces.com/problemset/problem/1890/A",
        difficulty_label="800",
        numeric_rating=800,
        sample_test_cases=[TestCase(input_data="1\n2\n8 9\n", expected_output="Yes\n")],
    )

    mock_sol = GeneratedSolution(
        problem_understanding="Determine if array can become good.",
        algorithm="Greedy",
        intuition="Count frequencies.",
        python_code="import sys\nfor _ in range(int(sys.stdin.readline())):\n    sys.stdin.readline()\n    sys.stdin.readline()\n    print('Yes')",
        time_complexity="O(N)",
        space_complexity="O(1)",
    )

    mock_rev = SolutionReview(
        is_correct=True,
        correctness_assessment="Verified",
        complexity_assessment="Optimal",
    )

    with (
        patch.object(
            orchestrator, "_select_codeforces_problem", AsyncMock(return_value=mock_problem)
        ),
        patch.object(
            orchestrator.solver,
            "solve_problem",
            AsyncMock(return_value=(mock_sol, mock_rev, mock_sol.python_code)),
        ),
    ):
        # First execution: Creates problem, session, runs tests
        res1 = await orchestrator.run_daily(force_platform="codeforces")
        assert res1["status"] in ("LOCAL_PASSED", "LOCAL_FAILED")
        assert res1["problem_title"] == "Doremy's Paint 3"

        # Second execution on the same date: Idempotently returns the existing session
        res2 = await orchestrator.run_daily(force_platform="codeforces")
        assert res2["session_id"] == res1["session_id"]
