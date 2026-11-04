"""Unit and integration tests for autonomous CloudRunner and state machine."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dc_agent.adapters.base import (
    CalendarVerificationResult,
    LeetCodeSubmissionResult,
    Platform,
    ProblemMetadata,
    TestCase,
)
from dc_agent.config import Settings
from dc_agent.core.cloud_runner import CloudRunner
from dc_agent.db.models import SessionStatus
from dc_agent.db.repository import Repository
from dc_agent.db.session import get_db
from dc_agent.executor.base import ExecutionSummary, LocalExecutionVerdict, TestCaseResult
from dc_agent.providers.schemas import GeneratedSolution, SolutionReview


@pytest.fixture
def mock_leetcode_meta() -> ProblemMetadata:
    return ProblemMetadata(
        platform=Platform.LEETCODE,
        external_id="3505",
        title="Find Substring With Maximum Frequency",
        slug="find-substring-with-maximum-frequency",
        url="https://leetcode.com/problems/find-substring-with-maximum-frequency/",
        difficulty_label="Medium",
        numeric_rating=None,
        tags=["String", "Sliding Window"],
        raw_content="<p>Return maximum frequency.</p>",
        sample_test_cases=[
            TestCase(input_data='"abcde"\n2', expected_output="2", is_verified=True),
        ],
        metadata_payload={
            "question": {
                "questionId": "3717",
                "questionFrontendId": "3505",
            }
        },
    )


@pytest.mark.asyncio
async def test_cloud_runner_full_e2e_success(
    mock_leetcode_meta: ProblemMetadata, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify autonomous end-to-end execution through state machine to DONE."""
    monkeypatch.setenv("LEETCODE_SESSION", "valid_session_cookie")
    monkeypatch.setenv("LEETCODE_CSRF_TOKEN", "valid_csrf_token")
    settings = Settings()

    runner = CloudRunner(settings=settings)

    sol = GeneratedSolution(
        problem_understanding="Find max frequency",
        algorithm="Sliding Window",
        intuition="Greedy window scan",
        python_code="class Solution:\n    def maxFreq(self, s: str, k: int) -> int:\n        return 2\n",
        time_complexity="O(N)",
        space_complexity="O(N)",
    )
    rev = SolutionReview(
        is_correct=True,
        correctness_assessment="Optimal",
        complexity_assessment="O(N) passes easily",
    )

    mock_summary = ExecutionSummary(
        overall_verdict=LocalExecutionVerdict.PASS,
        total_tests=1,
        passed_tests=1,
        failed_tests=0,
        results=[
            TestCaseResult(
                test_index=1,
                verdict=LocalExecutionVerdict.PASS,
                input_data="",
                execution_time_ms=5.0,
            )
        ],
    )

    mock_verdict = LeetCodeSubmissionResult(
        submission_id="99887766",
        state="SUCCESS",
        status_msg="Accepted",
        is_accepted=True,
        total_correct=40,
        total_testcases=40,
        runtime="35 ms",
        memory="16.9 MB",
    )

    mock_cal = CalendarVerificationResult(
        is_active=True,
        submission_count=1,
        streak=15,
        total_active_days=90,
        cycle_date="2026-11-04",
    )

    with (
        patch.object(
            runner.lc_adapter, "fetch_daily_challenge", AsyncMock(return_value=mock_leetcode_meta)
        ),
        patch.object(
            runner.solver, "solve_problem", AsyncMock(return_value=(sol, rev, sol.python_code))
        ),
        patch.object(runner.sandbox, "run_tests", MagicMock(return_value=mock_summary)),
        patch.object(runner.lc_adapter, "submit_solution", AsyncMock(return_value="99887766")),
        patch.object(
            runner.lc_adapter, "poll_submission_verdict", AsyncMock(return_value=mock_verdict)
        ),
        patch.object(
            runner.lc_adapter, "verify_calendar_activity", AsyncMock(return_value=mock_cal)
        ),
    ):
        result = await runner.execute(dry_run=False)

    assert result["status"] == SessionStatus.DONE
    assert result["submission_id"] == "99887766"
    assert result["judge_verdict"] == "Accepted"
    assert result["calendar_verified"] is True
    assert result["streak"] == 15

    # Check database state
    with get_db() as db:
        repo = Repository(db)
        session = repo.get_daily_session_for_date("2026-11-04")
        assert session is not None
        assert session.status == SessionStatus.DONE
        assert session.submission_id == "99887766"
        assert session.judge_verdict == "Accepted"
        assert session.calendar_verified_at is not None


@pytest.mark.asyncio
async def test_cloud_runner_idempotent_done(mock_leetcode_meta: ProblemMetadata) -> None:
    """Verify that when a session is already DONE, execution returns immediately without API calls."""
    settings = Settings()
    runner = CloudRunner(settings=settings)

    with get_db() as db:
        repo = Repository(db)
        problem = repo.create_or_get_problem(
            platform=Platform.LEETCODE,
            external_id="3505",
            title=mock_leetcode_meta.title,
            slug=mock_leetcode_meta.slug,
            url=mock_leetcode_meta.url,
            difficulty_label="Medium",
        )
        session = repo.create_daily_session(
            platform=Platform.LEETCODE,
            handle=settings.leetcode_username,
            cycle_date="2026-11-04",
            problem=problem,
        )
        session.status = SessionStatus.DONE
        session.submission_id = "already_submitted_111"
        session.judge_verdict = "Accepted"
        db.commit()

    with (
        patch.object(runner.lc_adapter, "fetch_daily_challenge", AsyncMock()) as mock_fetch,
        patch.object(runner.lc_adapter, "submit_solution", AsyncMock()) as mock_submit,
    ):
        result = await runner.execute(dry_run=False)
        mock_fetch.assert_not_called()
        mock_submit.assert_not_called()

    assert result["status"] == SessionStatus.DONE
    assert result["already_done"] is True
    assert result["submission_id"] == "already_submitted_111"


@pytest.mark.asyncio
async def test_cloud_runner_resume_from_submitted_polls_existing(
    mock_leetcode_meta: ProblemMetadata, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify resuming a SUBMITTED session polls existing submission_id instead of duplicate submitting."""
    monkeypatch.setenv("LEETCODE_SESSION", "valid_session")
    settings = Settings()
    runner = CloudRunner(settings=settings)

    with get_db() as db:
        repo = Repository(db)
        problem = repo.create_or_get_problem(
            platform=Platform.LEETCODE,
            external_id="3505",
            title=mock_leetcode_meta.title,
            slug=mock_leetcode_meta.slug,
            url=mock_leetcode_meta.url,
            difficulty_label="Medium",
        )
        session = repo.create_daily_session(
            platform=Platform.LEETCODE,
            handle=settings.leetcode_username,
            cycle_date="2026-11-04",
            problem=problem,
        )
        session.status = SessionStatus.SUBMITTED
        session.submission_id = "existing_sub_555"
        repo.record_attempt(session, "class Solution: pass", "PASS")
        db.commit()

    mock_verdict = LeetCodeSubmissionResult(
        submission_id="existing_sub_555",
        state="SUCCESS",
        status_msg="Accepted",
        is_accepted=True,
        runtime="40 ms",
    )
    mock_cal = CalendarVerificationResult(
        is_active=True,
        submission_count=1,
        streak=10,
        total_active_days=50,
        cycle_date="2026-11-04",
    )

    with (
        patch.object(runner.lc_adapter, "submit_solution", AsyncMock()) as mock_submit,
        patch.object(
            runner.lc_adapter, "poll_submission_verdict", AsyncMock(return_value=mock_verdict)
        ) as mock_poll,
        patch.object(
            runner.lc_adapter, "verify_calendar_activity", AsyncMock(return_value=mock_cal)
        ),
    ):
        result = await runner.execute(dry_run=False)
        mock_submit.assert_not_called()
        mock_poll.assert_called_once_with(
            submission_id="existing_sub_555",
            session_cookie="valid_session",
            csrf_token=None,
        )

    assert result["status"] == SessionStatus.DONE
    assert result["submission_id"] == "existing_sub_555"


@pytest.mark.asyncio
async def test_cloud_runner_dry_run_skips_remote_submission(
    mock_leetcode_meta: ProblemMetadata,
) -> None:
    """Verify dry-run completes local testing and skips remote submission."""
    settings = Settings()
    runner = CloudRunner(settings=settings)

    sol = GeneratedSolution(
        problem_understanding="Test understanding",
        algorithm="Direct",
        intuition="Trivial",
        python_code="class Solution:\n    pass\n",
        time_complexity="O(1)",
        space_complexity="O(1)",
    )
    rev = SolutionReview(
        is_correct=True,
        correctness_assessment="OK",
        complexity_assessment="O(1)",
    )
    mock_summary = ExecutionSummary(
        overall_verdict=LocalExecutionVerdict.PASS,
        total_tests=1,
        passed_tests=1,
        failed_tests=0,
        results=[],
    )

    with (
        patch.object(
            runner.lc_adapter, "fetch_daily_challenge", AsyncMock(return_value=mock_leetcode_meta)
        ),
        patch.object(
            runner.solver, "solve_problem", AsyncMock(return_value=(sol, rev, sol.python_code))
        ),
        patch.object(runner.sandbox, "run_tests", MagicMock(return_value=mock_summary)),
        patch.object(runner.lc_adapter, "submit_solution", AsyncMock()) as mock_submit,
    ):
        result = await runner.execute(dry_run=True)
        mock_submit.assert_not_called()

    assert result["dry_run"] is True
    assert result["status"] == SessionStatus.LOCALLY_VERIFIED


@pytest.mark.asyncio
async def test_cloud_runner_missing_session_cookie_sets_needs_attention(
    mock_leetcode_meta: ProblemMetadata, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify missing LEETCODE_SESSION transitions session to NEEDS_ATTENTION cleanly."""
    monkeypatch.delenv("LEETCODE_SESSION", raising=False)
    settings = Settings()
    runner = CloudRunner(settings=settings)

    sol = GeneratedSolution(
        problem_understanding="Test understanding",
        algorithm="Direct",
        intuition="Trivial",
        python_code="class Solution:\n    pass\n",
        time_complexity="O(1)",
        space_complexity="O(1)",
    )
    rev = SolutionReview(
        is_correct=True,
        correctness_assessment="OK",
        complexity_assessment="O(1)",
    )
    mock_summary = ExecutionSummary(
        overall_verdict=LocalExecutionVerdict.PASS,
        total_tests=1,
        passed_tests=1,
        failed_tests=0,
        results=[],
    )

    with (
        patch.object(
            runner.lc_adapter, "fetch_daily_challenge", AsyncMock(return_value=mock_leetcode_meta)
        ),
        patch.object(
            runner.solver, "solve_problem", AsyncMock(return_value=(sol, rev, sol.python_code))
        ),
        patch.object(runner.sandbox, "run_tests", MagicMock(return_value=mock_summary)),
    ):
        result = await runner.execute(dry_run=False)

    assert result["status"] == SessionStatus.NEEDS_ATTENTION
    assert "LEETCODE_SESSION" in result["error"]


@pytest.mark.asyncio
async def test_cloud_runner_production_blocks_ephemeral_sqlite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure production cloud execution in CI fails if DATABASE_URL is ephemeral SQLite."""
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    settings = Settings()
    runner = CloudRunner(settings=settings)

    with pytest.raises(ValueError, match="ephemeral SQLite"):
        runner.validate_cloud_environment(dry_run=False)

    # In dry-run mode, it should not raise, only warn
    warnings = runner.validate_cloud_environment(dry_run=True)
    assert any("ephemeral" in w for w in warnings)
