"""Database repository providing transactional business entity operations."""

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from dc_agent.db.models import (
    CurriculumProgress,
    DailySession,
    LocalTestCase,
    Problem,
    SolutionAttempt,
    UserAccount,
)

logger = logging.getLogger(__name__)


class Repository:
    """Encapsulates database access operations for DC-Agent."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_or_create_user(self, platform: str, handle: str) -> UserAccount:
        """Fetch existing user account or create a new one."""
        stmt = select(UserAccount).where(
            UserAccount.platform == platform, UserAccount.handle == handle
        )
        user = self.session.scalar(stmt)
        if not user:
            user = UserAccount(platform=platform, handle=handle)
            self.session.add(user)
            self.session.flush()

            # Initialize curriculum progress for codeforces
            if platform.lower() == "codeforces":
                progress = CurriculumProgress(
                    account_id=user.id,
                    current_tier=1,
                    current_rating_target=800,
                    consecutive_accepted=0,
                    consecutive_failed=0,
                )
                self.session.add(progress)
                self.session.flush()

        return user

    def get_curriculum_progress(self, handle: str) -> CurriculumProgress:
        """Fetch curriculum progress for a Codeforces handle."""
        user = self.get_or_create_user("codeforces", handle)
        stmt = select(CurriculumProgress).where(CurriculumProgress.account_id == user.id)
        prog = self.session.scalar(stmt)
        if not prog:
            prog = CurriculumProgress(
                account_id=user.id,
                current_tier=1,
                current_rating_target=800,
                consecutive_accepted=0,
                consecutive_failed=0,
            )
            self.session.add(prog)
            self.session.flush()
        return prog

    def get_daily_session(self, platform: str, cycle_date: str) -> DailySession | None:
        """Retrieve existing daily session for a platform on a specific date."""
        stmt = select(DailySession).where(
            DailySession.platform == platform, DailySession.cycle_date == cycle_date
        )
        return self.session.scalar(stmt)

    def get_daily_session_for_date(self, cycle_date: str) -> DailySession | None:
        """Retrieve existing daily session for any platform on a specific local cycle date."""
        stmt = (
            select(DailySession)
            .where(DailySession.cycle_date == cycle_date)
            .order_by(desc(DailySession.id))
        )
        return self.session.scalar(stmt)

    def get_latest_daily_session(self) -> DailySession | None:
        """Retrieve the most recent daily session in historical order."""
        stmt = (
            select(DailySession)
            .order_by(desc(DailySession.cycle_date), desc(DailySession.id))
            .limit(1)
        )
        return self.session.scalar(stmt)

    def create_or_get_problem(
        self,
        platform: str,
        external_id: str,
        title: str,
        slug: str,
        url: str,
        difficulty_label: str,
        numeric_rating: int | None = None,
        tags: list[str] | None = None,
        description: str | None = None,
        metadata_payload: dict[str, Any] | None = None,
        test_cases: list[dict[str, Any]] | None = None,
    ) -> Problem:
        """Persist problem and associated sample test cases."""
        stmt = select(Problem).where(
            Problem.platform == platform, Problem.external_id == external_id
        )
        problem = self.session.scalar(stmt)

        if not problem:
            problem = Problem(
                platform=platform,
                external_id=external_id,
                title=title,
                slug=slug,
                url=url,
                difficulty_label=difficulty_label,
                numeric_rating=numeric_rating,
                description=description,
                tags=tags or [],
                metadata_payload=metadata_payload or {},
            )
            self.session.add(problem)
            self.session.flush()

            if test_cases:
                for idx, tc in enumerate(test_cases, start=1):
                    exp = tc.get("expected_output")
                    is_ver = bool(tc.get("is_verified", bool(exp)))
                    test_obj = LocalTestCase(
                        problem_id=problem.id,
                        test_index=idx,
                        input_data=tc.get("input_data", ""),
                        expected_output=exp,
                        is_sample=tc.get("is_sample", True),
                        is_verified=is_ver,
                        source=tc.get("source", "PLATFORM_API"),
                    )
                    self.session.add(test_obj)
                self.session.flush()
        else:
            # Update description if it was previously missing and is now provided
            if description and not problem.description:
                problem.description = description
                self.session.flush()

        return problem

    def set_problem_statement(
        self,
        problem: Problem,
        description: str,
        test_cases: list[dict[str, Any]] | None = None,
    ) -> None:
        """Update problem description and optionally append verified test cases."""
        problem.description = description
        if test_cases:
            start_idx = len(problem.test_cases) + 1
            for offset, tc in enumerate(test_cases):
                exp = tc.get("expected_output")
                is_ver = bool(tc.get("is_verified", bool(exp)))
                test_obj = LocalTestCase(
                    problem_id=problem.id,
                    test_index=start_idx + offset,
                    input_data=tc.get("input_data", ""),
                    expected_output=exp,
                    is_sample=tc.get("is_sample", True),
                    is_verified=is_ver,
                    source=tc.get("source", "USER_INPUT"),
                )
                problem.test_cases.append(test_obj)
                self.session.add(test_obj)
        self.session.flush()

    def create_daily_session(
        self,
        platform: str,
        handle: str,
        cycle_date: str,
        problem: Problem,
        retry_budget: int = 5,
    ) -> DailySession:
        """Create a new daily session atomically."""
        user = self.get_or_create_user(platform, handle)

        session = DailySession(
            platform=platform,
            account_id=user.id,
            cycle_date=cycle_date,
            problem_id=problem.id,
            status="INITIALIZED",
            retry_budget=retry_budget,
            retry_count=0,
        )
        self.session.add(session)
        self.session.flush()
        return session

    def record_attempt(
        self,
        daily_session: DailySession,
        source_code: str,
        local_verdict: str,
        execution_metrics: dict[str, Any] | None = None,
        platform_verdict: str = "PENDING_MANUAL",
        submission_id: str | None = None,
    ) -> SolutionAttempt:
        """Record an attempt made during a daily problem-solving session."""
        daily_session.retry_count += 1
        attempt_num = daily_session.retry_count

        attempt = SolutionAttempt(
            session_id=daily_session.id,
            attempt_number=attempt_num,
            source_code=source_code,
            language="python3",
            local_verdict=local_verdict,
            execution_metrics=execution_metrics or {},
            platform_verdict=platform_verdict,
            platform_submission_id=submission_id,
        )
        self.session.add(attempt)
        self.session.flush()
        return attempt

    def mark_session_accepted(
        self, daily_session: DailySession, submission_id: str | None = None
    ) -> None:
        """Mark a daily session as officially platform Accepted."""
        daily_session.status = "ACCEPTED"
        daily_session.judge_verdict = "Accepted"
        if submission_id:
            daily_session.submission_id = submission_id
        daily_session.completed_at = datetime.now(UTC)
        self.session.flush()

    def update_session_status(
        self,
        daily_session: DailySession,
        status: str,
        error_message: str | None = None,
        log_entry: str | None = None,
    ) -> None:
        """Update session status and append to execution log."""
        daily_session.status = status
        if error_message is not None:
            daily_session.error_message = error_message
        if log_entry:
            log_item = {
                "timestamp": datetime.now(UTC).isoformat(),
                "status": status,
                "message": log_entry,
            }
            curr_log = list(daily_session.execution_log or [])
            curr_log.append(log_item)
            daily_session.execution_log = curr_log
        self.session.flush()

    def update_submission_verdict(
        self,
        daily_session: DailySession,
        submission_id: str,
        verdict: str,
    ) -> None:
        """Update session and attempt with judge verdict."""
        daily_session.submission_id = submission_id
        daily_session.judge_verdict = verdict
        if daily_session.attempts:
            latest = daily_session.attempts[-1]
            latest.platform_submission_id = submission_id
            latest.platform_verdict = verdict
        self.session.flush()

    def mark_session_calendar_verified(self, daily_session: DailySession) -> None:
        """Mark session as verified on LeetCode calendar."""
        daily_session.status = "CALENDAR_VERIFIED"
        daily_session.calendar_verified_at = datetime.now(UTC)
        self.session.flush()

    def mark_session_done(self, daily_session: DailySession) -> None:
        """Mark session as completely finished and verified."""
        daily_session.status = "DONE"
        daily_session.completed_at = datetime.now(UTC)
        self.session.flush()

    def mark_session_failed(self, daily_session: DailySession, error_msg: str) -> None:
        """Mark session as failed with descriptive error message."""
        daily_session.status = "FAILED"
        daily_session.error_message = error_msg
        daily_session.completed_at = datetime.now(UTC)
        self.session.flush()

    def mark_session_exhausted(self, daily_session: DailySession) -> None:
        """Mark a daily session as exhausted."""
        daily_session.status = "EXHAUSTED"
        daily_session.completed_at = datetime.now(UTC)
        self.session.flush()

    def get_recent_sessions(self, limit: int = 10) -> list[DailySession]:
        """Fetch most recent daily sessions."""
        stmt = (
            select(DailySession)
            .order_by(desc(DailySession.cycle_date), desc(DailySession.id))
            .limit(limit)
        )
        return list(self.session.scalars(stmt).all())
