"""Regression tests for session resume correctness and concurrency protection."""

from unittest.mock import AsyncMock, patch

import pytest

from dc_agent.adapters.base import Platform, ProblemMetadata
from dc_agent.config import get_canonical_cycle_date, get_settings
from dc_agent.core.orchestrator import DailyOrchestrator
from dc_agent.db.repository import Repository
from dc_agent.db.session import get_db
from dc_agent.providers.schemas import GeneratedSolution, SolutionReview


@pytest.mark.asyncio
async def test_session_resume_always_uses_original_problem() -> None:
    """CRITICAL REGRESSION: An incomplete existing session must NEVER pick a new problem B.

    It must always resume its original associated Problem A.
    """
    settings = get_settings()
    cycle_date = get_canonical_cycle_date(settings)
    orchestrator = DailyOrchestrator(settings=settings)

    with get_db() as db:
        repo = Repository(db)
        # Create original problem A
        prob_a = repo.create_or_get_problem(
            platform="codeforces",
            external_id="1000_A",
            title="Original Problem A",
            slug="1000-a",
            url="https://codeforces.com/problemset/problem/1000/A",
            difficulty_label="800",
            description="Statement for Problem A: solve x + y.",
            test_cases=[{"input_data": "1 2\n", "expected_output": "3\n", "is_verified": True}],
        )
        session = repo.create_daily_session("codeforces", "hkvariant", cycle_date, prob_a)
        session.status = "LOCAL_FAILED"
        session_id = session.id
        db.commit()

    # Now run orchestrator to resume.
    # Mock problem selector: If called, it would return Problem B!
    prob_b_meta = ProblemMetadata(
        platform=Platform.CODEFORCES,
        external_id="2000_B",
        title="Disaster Problem B",
        slug="2000-b",
        url="https://codeforces.com/problemset/problem/2000/B",
        difficulty_label="900",
        raw_content="Statement for Problem B.",
    )

    mock_sol = GeneratedSolution(
        problem_understanding="Solve problem",
        algorithm="Math",
        intuition="Addition",
        python_code="print(3)",
        time_complexity="O(1)",
        space_complexity="O(1)",
    )
    mock_rev = SolutionReview(
        is_correct=True,
        correctness_assessment="OK",
        complexity_assessment="O(1)",
    )

    with (
        patch.object(
            orchestrator, "_select_codeforces_problem", AsyncMock(return_value=prob_b_meta)
        ) as mock_select,
        patch.object(
            orchestrator.solver,
            "solve_problem",
            AsyncMock(return_value=(mock_sol, mock_rev, mock_sol.python_code)),
        ),
    ):
        result = await orchestrator.run_daily()

        # 1. Selector must NEVER be called when an existing session is resumed
        mock_select.assert_not_called()

        # 2. Resumed problem title and ID must be Problem A, never Problem B
        assert result["problem_title"] == "Original Problem A"
        assert result["session_id"] == session_id

        # 3. Check database: The session's associated problem remains Problem A
        with get_db() as db:
            refreshed = db.get(type(session), session_id)
            assert refreshed is not None
            assert refreshed.problem.title == "Original Problem A"
            assert refreshed.problem.external_id == "1000_A"


@pytest.mark.asyncio
async def test_session_resume_does_not_regenerate_already_passed_solution() -> None:
    """If a session already passed local tests, resuming it must return the solution without re-solving."""
    settings = get_settings()
    cycle_date = get_canonical_cycle_date(settings)
    orchestrator = DailyOrchestrator(settings=settings)

    with get_db() as db:
        repo = Repository(db)
        prob = repo.create_or_get_problem(
            platform="leetcode",
            external_id="two-sum",
            title="Two Sum",
            slug="two-sum",
            url="https://leetcode.com/problems/two-sum/",
            difficulty_label="Easy",
            description="Given nums and target...",
        )
        session = repo.create_daily_session("leetcode", "hardikkaurani_", cycle_date, prob)
        repo.record_attempt(
            daily_session=session,
            source_code="class Solution:\n    def twoSum(self, nums, target): return [0, 1]",
            local_verdict="PASS",
            execution_metrics={"passed_tests": 1, "total_tests": 1},
        )
        session.status = "LOCAL_PASSED"
        db.commit()

    with patch.object(orchestrator.solver, "solve_problem", AsyncMock()) as mock_solve:
        result = await orchestrator.run_daily()

        # AI solver must NOT be re-invoked
        mock_solve.assert_not_called()
        assert result["status"] == "LOCAL_PASSED"
        assert result["already_passed"] is True
        assert result["resumed"] is True


@pytest.mark.asyncio
async def test_concurrent_execution_lock_prevents_duplicate_runs() -> None:
    """When a session is marked RUNNING, concurrent execution attempts must be blocked."""
    settings = get_settings()
    cycle_date = get_canonical_cycle_date(settings)
    orchestrator = DailyOrchestrator(settings=settings)

    with get_db() as db:
        repo = Repository(db)
        prob = repo.create_or_get_problem(
            platform="leetcode",
            external_id="1",
            title="Problem",
            slug="prob",
            url="http://lc/1",
            difficulty_label="Easy",
            description="desc",
        )
        session = repo.create_daily_session("leetcode", "user", cycle_date, prob)
        session.status = "RUNNING"
        db.commit()

    with pytest.raises(RuntimeError, match="currently running in another process"):
        await orchestrator.run_daily()
