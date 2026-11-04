"""Integration tests for DailyOrchestrator."""

from unittest.mock import AsyncMock, patch

import pytest

from dc_agent.adapters.base import Platform, ProblemMetadata, TestCase
from dc_agent.config import DailyPolicyType, Settings
from dc_agent.core.orchestrator import DailyOrchestrator
from dc_agent.db.repository import Repository
from dc_agent.db.session import get_db, init_db
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
    """Ensure alternating policy switches deterministically based on persistent history."""
    init_db(test_settings.database_url)
    orchestrator = DailyOrchestrator(settings=test_settings)

    with get_db() as db:
        repo = Repository(db)

        # 1. Fresh start: starts with leetcode
        assert (
            orchestrator.determine_today_platform(cycle_date="2026-01-01", repo=repo) == "leetcode"
        )

        # Record a session for leetcode on 2026-01-01
        p_lc = repo.create_or_get_problem(
            "leetcode", "1", "Two Sum", "two-sum", "http://lc/1", "Easy"
        )
        repo.create_daily_session("leetcode", "hardikkaurani_", "2026-01-01", p_lc)
        db.commit()

        # 2. Next day alternates to codeforces
        assert (
            orchestrator.determine_today_platform(cycle_date="2026-01-02", repo=repo)
            == "codeforces"
        )

        # Record a session for codeforces on 2026-01-02
        p_cf = repo.create_or_get_problem(
            "codeforces", "1_A", "Theatre Square", "1-a", "http://cf/1/A", "800"
        )
        repo.create_daily_session("codeforces", "hkvariant", "2026-01-02", p_cf)
        db.commit()

        # 3. Next day alternates back to leetcode
        assert (
            orchestrator.determine_today_platform(cycle_date="2026-01-03", repo=repo) == "leetcode"
        )


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
        raw_content="Given an array of integers, determine if it can become good.",
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


@pytest.mark.asyncio
async def test_select_codeforces_problem(test_settings: Settings) -> None:
    """Ensure _select_codeforces_problem filters candidates and picks deterministically."""
    from unittest.mock import MagicMock

    init_db(test_settings.database_url)
    orchestrator = DailyOrchestrator(settings=test_settings)

    mock_sub = MagicMock()
    mock_sub.problem_identifier = "1000_A"
    mock_sub.verdict = "Accepted"

    candidate = ProblemMetadata(
        platform=Platform.CODEFORCES,
        external_id="1000_B",
        title="Prob B",
        slug="1000-b",
        url="http://cf/1000/B",
        difficulty_label="800",
        numeric_rating=800,
    )

    with get_db() as db:
        repo = Repository(db)
        with (
            patch.object(
                orchestrator.cf_adapter,
                "fetch_user_submissions",
                AsyncMock(return_value=[mock_sub]),
            ),
            patch.object(
                orchestrator.cf_adapter, "fetch_problemset", AsyncMock(return_value=[candidate])
            ),
        ):
            selected = await orchestrator._select_codeforces_problem(repo, "2026-11-04")
            assert selected.external_id == "1000_B"


@pytest.mark.asyncio
async def test_select_leetcode_problem_success_and_fallback(test_settings: Settings) -> None:
    """Ensure _select_leetcode_problem retrieves daily challenge and provides actionable error on failure."""
    orchestrator = DailyOrchestrator(settings=test_settings)
    mock_lc = ProblemMetadata(
        platform=Platform.LEETCODE,
        external_id="1",
        title="Two Sum",
        slug="two-sum",
        url="http://lc/1",
        difficulty_label="Easy",
    )

    with patch.object(
        orchestrator.lc_adapter, "fetch_daily_challenge", AsyncMock(return_value=mock_lc)
    ):
        res = await orchestrator._select_leetcode_problem()
        assert res.slug == "two-sum"

    with (
        patch.object(
            orchestrator.lc_adapter,
            "fetch_daily_challenge",
            AsyncMock(side_effect=Exception("WAF blocked")),
        ),
        pytest.raises(RuntimeError, match="could not be retrieved automatically"),
    ):
        await orchestrator._select_leetcode_problem()


def test_platform_policy_overrides(test_settings: Settings) -> None:
    """Ensure policy overrides (LEETCODE_ONLY, CODEFORCES_ONLY) are strictly respected."""
    test_settings.daily_policy = DailyPolicyType.LEETCODE_ONLY
    orchestrator = DailyOrchestrator(settings=test_settings)
    assert orchestrator.determine_today_platform() == "leetcode"

    test_settings.daily_policy = DailyPolicyType.CODEFORCES_ONLY
    assert orchestrator.determine_today_platform() == "codeforces"
