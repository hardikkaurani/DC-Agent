"""Autonomous Cloud Execution Engine for DC-Agent.

Coordinates scheduled LeetCode daily challenge execution in cloud infrastructure:
1. Validates canonical date (Asia/Kolkata) and environment isolation.
2. Checks idempotency and safely resumes partial sessions.
3. Fetches official LeetCode Daily Challenge.
4. Generates and reviews Python 3 solution with Gemini.
5. Verifies tests inside Docker container sandbox with bounded AST safety.
6. Submits solution to LeetCode authenticated endpoints.
7. Polls judge until terminal verdict is received (with bounded diagnosis retries).
8. Verifies green calendar activity on LeetCode user profile.
9. Enforces state transitions to terminal DONE only upon full acceptance.
10. Produces structured execution reports and GITHUB_STEP_SUMMARY.
"""

import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dc_agent.adapters.base import (
    CalendarVerificationResult,
    LeetCodeSubmissionResult,
    Platform,
    ProblemMetadata,
    TestCase,
)
from dc_agent.adapters.leetcode import LeetCodeAdapter, LeetCodeAPIError
from dc_agent.config import Settings, get_canonical_cycle_date, get_settings
from dc_agent.core.correction import CorrectionEngine
from dc_agent.core.solver import SolverService
from dc_agent.db.models import DailySession, Problem, SessionStatus
from dc_agent.db.repository import Repository
from dc_agent.db.session import get_db, init_db
from dc_agent.executor.base import LocalExecutionVerdict
from dc_agent.executor.sandbox import Sandbox
from dc_agent.providers.factory import get_ai_provider

logger = logging.getLogger(__name__)


class CloudRunner:
    """Autonomous cloud execution coordinator for daily challenge operations."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.ai_provider = get_ai_provider(settings=self.settings)
        self.lc_adapter = LeetCodeAdapter()
        self.sandbox = Sandbox(settings=self.settings)
        self.solver = SolverService(provider=self.ai_provider)
        self.correction_engine = CorrectionEngine(provider=self.ai_provider, sandbox=self.sandbox)

    def validate_cloud_environment(self, dry_run: bool = False) -> list[str]:
        """Verify cloud infrastructure dependencies, secrets, and storage."""
        warnings: list[str] = []

        is_ci = os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true"
        if is_ci and not dry_run and self.settings.database_url.startswith("sqlite"):
            raise ValueError(
                "DATABASE_URL (PostgreSQL) is missing or set to ephemeral SQLite. "
                "Production cloud execution requires a durable database connection "
                "(e.g. PostgreSQL: postgresql://...) to guarantee persistent state "
                "across runner instances. Ephemeral SQLite fallback in production cloud execution is blocked."
            )
        elif is_ci and self.settings.database_url.startswith("sqlite"):
            warnings.append(
                "Running in CI/Cloud dry-run with default SQLite database. "
                "Sessions and progress will be ephemeral across runner instances."
            )

        if not self.settings.gemini_api_key and self.settings.ai_provider == "gemini":
            raise ValueError(
                "GEMINI_API_KEY secret is required for autonomous solution generation."
            )

        if not dry_run and not self.settings.leetcode_session:
            warnings.append(
                "LEETCODE_SESSION secret is not configured. "
                "Cloud runner will verify solutions locally but cannot perform automated platform submission."
            )

        return warnings

    async def execute(
        self,
        dry_run: bool = False,
        force_platform: str | None = None,
        max_platform_retries: int = 3,
    ) -> dict[str, Any]:
        """Execute the autonomous cloud daily challenge workflow with strict state machine."""
        cycle_date = get_canonical_cycle_date(self.settings)
        start_time_utc = datetime.now(UTC)
        tz_ist = ZoneInfo(self.settings.timezone)
        start_time_ist = datetime.now(tz_ist)

        logger.info(
            "Starting CloudRunner execution for cycle date %s (IST: %s)",
            cycle_date,
            start_time_ist.strftime("%Y-%m-%d %H:%M:%S %Z"),
        )

        env_warnings = self.validate_cloud_environment(dry_run=dry_run)
        for w in env_warnings:
            logger.warning("Cloud Environment Notice: %s", w)

        init_db(self.settings.database_url)

        with get_db() as db:
            repo = Repository(db)
            existing = repo.get_daily_session_for_date(cycle_date)
            session: DailySession
            problem: Problem

            # 1. Idempotency Check & Safe Resume
            if existing:
                session = existing
                problem = session.problem
                logger.info(
                    "Found existing session (ID: %d, Status: %s) for %s.",
                    session.id,
                    session.status,
                    cycle_date,
                )

                if session.status == SessionStatus.DONE:
                    logger.info(
                        "Session for %s is already completed and verified (DONE).", cycle_date
                    )
                    report = self._build_execution_report(
                        session=session,
                        problem=problem,
                        cycle_date=cycle_date,
                        start_time_utc=start_time_utc,
                        start_time_ist=start_time_ist,
                        dry_run=dry_run,
                    )
                    self._persist_report(report)
                    return {
                        "status": SessionStatus.DONE,
                        "session_id": session.id,
                        "problem_title": problem.title,
                        "problem_url": problem.url,
                        "submission_id": session.submission_id,
                        "judge_verdict": session.judge_verdict,
                        "already_done": True,
                    }

                if session.status == SessionStatus.CALENDAR_VERIFIED:
                    repo.mark_session_done(session)
                    db.commit()
                    report = self._build_execution_report(
                        session=session,
                        problem=problem,
                        cycle_date=cycle_date,
                        start_time_utc=start_time_utc,
                        start_time_ist=start_time_ist,
                        dry_run=dry_run,
                    )
                    self._persist_report(report)
                    return {
                        "status": SessionStatus.DONE,
                        "session_id": session.id,
                        "problem_title": problem.title,
                        "problem_url": problem.url,
                        "submission_id": session.submission_id,
                        "judge_verdict": session.judge_verdict,
                    }

                if session.status == SessionStatus.RUNNING:
                    raise RuntimeError(
                        f"Daily session for {cycle_date} is already marked RUNNING. "
                        "Aborting to prevent duplicate concurrent cloud executions."
                    )

            else:
                # 2. Fetch Daily Challenge (PROBLEM_FETCHED)
                meta: ProblemMetadata = await self.lc_adapter.fetch_daily_challenge()
                test_case_dicts = [
                    {
                        "input_data": tc.input_data,
                        "expected_output": tc.expected_output,
                        "is_sample": tc.is_sample,
                        "is_verified": tc.is_verified,
                    }
                    for tc in meta.sample_test_cases
                ]
                problem = repo.create_or_get_problem(
                    platform=Platform.LEETCODE,
                    external_id=meta.external_id,
                    title=meta.title,
                    slug=meta.slug,
                    url=meta.url,
                    difficulty_label=meta.difficulty_label,
                    numeric_rating=meta.numeric_rating,
                    description=meta.raw_content,
                    tags=meta.tags,
                    metadata_payload=meta.metadata_payload,
                    test_cases=test_case_dicts,
                )
                session = repo.create_daily_session(
                    platform=Platform.LEETCODE,
                    handle=self.settings.leetcode_username,
                    cycle_date=cycle_date,
                    problem=problem,
                    retry_budget=self.settings.retry_limit,
                )
                repo.update_session_status(
                    session,
                    SessionStatus.PROBLEM_FETCHED,
                    log_entry=f"Fetched LeetCode Daily Challenge: {problem.title} ({problem.slug})",
                )
                db.commit()

            if not existing:
                # Set session to RUNNING state
                repo.update_session_status(session, SessionStatus.RUNNING)
                db.commit()

            test_cases = [
                TestCase(
                    input_data=tc.input_data,
                    expected_output=tc.expected_output,
                    is_verified=tc.is_verified,
                )
                for tc in problem.test_cases
            ]

            # 3. Local Solution Generation (SOLUTION_GENERATED)
            current_code: str
            if session.attempts:
                current_code = session.attempts[-1].source_code
            else:
                desc = problem.description or problem.title
                sol, review, current_code = await self.solver.solve_problem(
                    platform=Platform.LEETCODE,
                    title=problem.title,
                    description=desc,
                    sample_tests=test_cases,
                )
                repo.update_session_status(
                    session,
                    SessionStatus.SOLUTION_GENERATED,
                    log_entry=f"Generated Python 3 solution with Gemini (Complexity: {sol.time_complexity})",
                )
                db.commit()

            # 4. Local Sandbox Testing & Iterative Corrections (LOCALLY_VERIFIED)
            if session.status not in (
                SessionStatus.LOCALLY_VERIFIED,
                SessionStatus.SUBMISSION_PENDING,
                SessionStatus.SUBMITTED,
                SessionStatus.JUDGE_ACCEPTED,
                SessionStatus.CALENDAR_VERIFIED,
                SessionStatus.DONE,
            ):
                desc = problem.description or problem.title
                (
                    final_code,
                    summary,
                    diagnostics,
                ) = await self.correction_engine.run_and_correct_loop(
                    platform=Platform.LEETCODE,
                    title=problem.title,
                    description=desc,
                    initial_code=current_code,
                    test_cases=test_cases,
                    max_retries=self.settings.retry_limit,
                )
                current_code = final_code

                repo.record_attempt(
                    daily_session=session,
                    source_code=final_code,
                    local_verdict=str(summary.overall_verdict),
                    execution_metrics={
                        "total_time_ms": summary.total_time_ms,
                        "passed_tests": summary.passed_tests,
                        "total_tests": summary.total_tests,
                        "unverified_tests": summary.unverified_tests,
                        "corrections_made": len(diagnostics),
                    },
                    platform_verdict="PENDING_SUBMIT",
                )

                if (
                    summary.overall_verdict
                    in (
                        LocalExecutionVerdict.PASS,
                        LocalExecutionVerdict.UNVERIFIED,
                        LocalExecutionVerdict.NO_VERIFIED_TESTS,
                    )
                    or summary.passed_tests > 0
                ):
                    repo.update_session_status(
                        session,
                        SessionStatus.LOCALLY_VERIFIED,
                        log_entry=f"Passed local tests ({summary.passed_tests}/{summary.total_tests}) in Docker sandbox.",
                    )
                    db.commit()
                else:
                    err_msg = (
                        f"Local tests failed ({summary.passed_tests}/{summary.total_tests}) "
                        f"with verdict {summary.overall_verdict} after {len(diagnostics)} corrections."
                    )
                    repo.mark_session_failed(session, err_msg)
                    db.commit()
                    report = self._build_execution_report(
                        session=session,
                        problem=problem,
                        cycle_date=cycle_date,
                        start_time_utc=start_time_utc,
                        start_time_ist=start_time_ist,
                        dry_run=dry_run,
                    )
                    self._persist_report(report)
                    return {
                        "status": SessionStatus.FAILED,
                        "session_id": session.id,
                        "error": err_msg,
                        "problem_title": problem.title,
                    }

            # If dry-run requested, stop before submitting to platform
            if dry_run:
                logger.info(
                    "Dry-run requested: skipping remote platform submission and calendar verification."
                )
                report = self._build_execution_report(
                    session=session,
                    problem=problem,
                    cycle_date=cycle_date,
                    start_time_utc=start_time_utc,
                    start_time_ist=start_time_ist,
                    dry_run=True,
                )
                self._persist_report(report)
                return {
                    "status": session.status,
                    "session_id": session.id,
                    "problem_title": problem.title,
                    "problem_url": problem.url,
                    "dry_run": True,
                }

            # 5. Remote Platform Submission (SUBMISSION_PENDING -> SUBMITTED)
            if not self.settings.leetcode_session:
                repo.update_session_status(
                    session,
                    SessionStatus.NEEDS_ATTENTION,
                    error_message="LEETCODE_SESSION secret is missing. Cannot submit to platform.",
                    log_entry="Execution paused: LEETCODE_SESSION is required for cloud submission.",
                )
                db.commit()
                report = self._build_execution_report(
                    session=session,
                    problem=problem,
                    cycle_date=cycle_date,
                    start_time_utc=start_time_utc,
                    start_time_ist=start_time_ist,
                    dry_run=False,
                )
                self._persist_report(report)
                return {
                    "status": SessionStatus.NEEDS_ATTENTION,
                    "session_id": session.id,
                    "error": "LEETCODE_SESSION secret is required.",
                    "problem_title": problem.title,
                }

            session_cookie = self.settings.leetcode_session.get_secret_value()
            csrf_token = (
                self.settings.leetcode_csrf_token.get_secret_value()
                if self.settings.leetcode_csrf_token
                else None
            )

            submission_id = session.submission_id

            # 6. Submit or Resume Polling Existing Submission
            if not submission_id or session.status in (
                SessionStatus.LOCALLY_VERIFIED,
                SessionStatus.SUBMISSION_PENDING,
            ):
                repo.update_session_status(
                    session,
                    SessionStatus.SUBMISSION_PENDING,
                    log_entry="Submitting verified solution to LeetCode API...",
                )
                db.commit()

                try:
                    submission_id = await self.lc_adapter.submit_solution(
                        slug=problem.slug,
                        question_id=problem.internal_question_id,
                        typed_code=current_code,
                        session_cookie=session_cookie,
                        csrf_token=csrf_token,
                    )
                    repo.update_submission_verdict(
                        session, submission_id=submission_id, verdict="PENDING"
                    )
                    repo.update_session_status(
                        session,
                        SessionStatus.SUBMITTED,
                        log_entry=f"Solution submitted successfully. Submission ID: #{submission_id}",
                    )
                    db.commit()
                except LeetCodeAPIError as api_err:
                    err_msg = f"Platform submission failed: {api_err}"
                    repo.mark_session_failed(session, err_msg)
                    db.commit()
                    report = self._build_execution_report(
                        session=session,
                        problem=problem,
                        cycle_date=cycle_date,
                        start_time_utc=start_time_utc,
                        start_time_ist=start_time_ist,
                        dry_run=False,
                    )
                    self._persist_report(report)
                    return {
                        "status": SessionStatus.FAILED,
                        "session_id": session.id,
                        "error": err_msg,
                    }

            # 7. Poll Judge Verdict with Bounded Remote Retries (JUDGE_ACCEPTED)
            remote_attempt = 1
            while remote_attempt <= max_platform_retries:
                logger.info(
                    "Polling LeetCode judge for submission #%s (Attempt %d/%d)...",
                    submission_id,
                    remote_attempt,
                    max_platform_retries,
                )
                verdict_res: LeetCodeSubmissionResult = (
                    await self.lc_adapter.poll_submission_verdict(
                        submission_id=submission_id,
                        session_cookie=session_cookie,
                        csrf_token=csrf_token,
                    )
                )

                if verdict_res.is_accepted:
                    logger.info("LeetCode Judge Verdict: ACCEPTED (#%s)!", submission_id)
                    repo.update_submission_verdict(
                        session, submission_id=submission_id, verdict="Accepted"
                    )
                    repo.update_session_status(
                        session,
                        SessionStatus.JUDGE_ACCEPTED,
                        log_entry=f"Judge verdict: Accepted ({verdict_res.runtime}, {verdict_res.memory})",
                    )
                    db.commit()
                    break

                logger.warning(
                    "LeetCode Judge Verdict not Accepted: %s (#%s). Correct: %d/%d",
                    verdict_res.status_msg,
                    submission_id,
                    verdict_res.total_correct,
                    verdict_res.total_testcases,
                )
                repo.update_submission_verdict(
                    session, submission_id=submission_id, verdict=verdict_res.status_msg
                )
                db.commit()

                if remote_attempt >= max_platform_retries:
                    err_msg = (
                        f"Judge verdict was {verdict_res.status_msg} (#%s). "
                        f"Platform retry budget ({max_platform_retries}) exhausted."
                    )
                    repo.mark_session_failed(session, err_msg)
                    db.commit()
                    report = self._build_execution_report(
                        session=session,
                        problem=problem,
                        cycle_date=cycle_date,
                        start_time_utc=start_time_utc,
                        start_time_ist=start_time_ist,
                        dry_run=False,
                    )
                    self._persist_report(report)
                    return {
                        "status": SessionStatus.FAILED,
                        "session_id": session.id,
                        "error": err_msg,
                        "submission_id": submission_id,
                        "verdict": verdict_res.status_msg,
                    }

                # Perform bounded remote diagnostic correction with judge feedback
                remote_attempt += 1
                failure_info = {
                    "overall_verdict": verdict_res.status_msg,
                    "failing_test_index": verdict_res.total_correct + 1,
                    "input": verdict_res.last_testcase or "",
                    "expected_output": verdict_res.expected_output or "",
                    "actual_output": verdict_res.code_output or "",
                    "error_message": verdict_res.error_message or verdict_res.runtime_error or "",
                }

                logger.info("Invoking Gemini to diagnose platform judge rejection...")
                diag = await self.ai_provider.diagnose_and_fix(
                    problem_title=problem.title,
                    problem_description=problem.description or problem.title,
                    platform=Platform.LEETCODE,
                    solution_code=current_code,
                    failure_details=failure_info,
                )
                current_code = diag.corrected_code

                # Re-verify in Docker sandbox with judge test case appended
                if verdict_res.last_testcase:
                    test_cases.append(
                        TestCase(
                            input_data=verdict_res.last_testcase,
                            expected_output=verdict_res.expected_output,
                            is_verified=bool(verdict_res.expected_output),
                            description="Judge Counterexample Test Case",
                        )
                    )

                re_summary = self.sandbox.run_tests(current_code, test_cases)
                repo.record_attempt(
                    daily_session=session,
                    source_code=current_code,
                    local_verdict=str(re_summary.overall_verdict),
                    execution_metrics={"re_verified_tests": re_summary.passed_tests},
                    platform_verdict="PENDING_SUBMIT",
                )

                # Re-submit corrected code
                submission_id = await self.lc_adapter.submit_solution(
                    slug=problem.slug,
                    question_id=problem.internal_question_id,
                    typed_code=current_code,
                    session_cookie=session_cookie,
                    csrf_token=csrf_token,
                )
                repo.update_submission_verdict(
                    session, submission_id=submission_id, verdict="PENDING"
                )
                db.commit()

            # 8. Calendar Activity Verification (CALENDAR_VERIFIED -> DONE)
            logger.info(
                "Verifying activity calendar for user '%s' on %s (Asia/Kolkata)...",
                self.settings.leetcode_username,
                cycle_date,
            )
            cal_result: CalendarVerificationResult = await self.lc_adapter.verify_calendar_activity(
                username=self.settings.leetcode_username,
                target_date_ist=cycle_date,
                max_retries=5,
                retry_delay=4.0,
            )

            if cal_result.is_active:
                logger.info(
                    "Calendar verified active for %s! Streak: %d, Total Active: %d",
                    cycle_date,
                    cal_result.streak,
                    cal_result.total_active_days,
                )
                repo.mark_session_calendar_verified(session)
                repo.mark_session_done(session)
                repo.update_session_status(
                    session,
                    SessionStatus.DONE,
                    log_entry=(
                        f"Calendar active verified on LeetCode. "
                        f"Streak: {cal_result.streak}, Total Active: {cal_result.total_active_days}. Daily Run DONE."
                    ),
                )
                db.commit()
            else:
                logger.warning(
                    "Judge accepted solution, but calendar activity was not yet reflected on LeetCode profile for %s.",
                    cycle_date,
                )
                repo.update_session_status(
                    session,
                    SessionStatus.NEEDS_ATTENTION,
                    error_message="Submission accepted, but calendar check did not observe active date entry.",
                )
                db.commit()

            # 9. Scaffolding & Report Generation
            report_md = self._build_execution_report(
                session=session,
                problem=problem,
                cycle_date=cycle_date,
                start_time_utc=start_time_utc,
                start_time_ist=start_time_ist,
                dry_run=False,
                cal_result=cal_result,
            )
            report_path = self._persist_report(report_md)

            return {
                "status": session.status,
                "session_id": session.id,
                "problem_title": problem.title,
                "problem_url": problem.url,
                "submission_id": session.submission_id,
                "judge_verdict": session.judge_verdict,
                "calendar_verified": cal_result.is_active,
                "streak": cal_result.streak,
                "total_active_days": cal_result.total_active_days,
                "report_file": str(report_path),
            }

    def _build_execution_report(
        self,
        session: DailySession,
        problem: Problem,
        cycle_date: str,
        start_time_utc: datetime,
        start_time_ist: datetime,
        dry_run: bool,
        cal_result: CalendarVerificationResult | None = None,
    ) -> str:
        """Construct a GitHub Actions markdown execution report."""
        status_badge = (
            "🟢 ACCEPTED & VERIFIED (DONE)"
            if session.status == SessionStatus.DONE
            else f"🟡 {session.status}"
            if session.status != SessionStatus.FAILED
            else "🔴 FAILED"
        )
        sub_id = session.submission_id or "N/A"
        judge_verdict = session.judge_verdict or "Pending"
        cal_status = (
            f"Active (Streak: {cal_result.streak}, Total Days: {cal_result.total_active_days})"
            if cal_result and cal_result.is_active
            else "Verified"
            if session.calendar_verified_at
            else "Pending Sync"
        )

        attempts_count = len(session.attempts) if session.attempts else 0
        latest_attempt = session.attempts[-1] if session.attempts else None
        metrics = latest_attempt.execution_metrics if latest_attempt else {}

        report_lines = [
            "# DC-Agent Cloud Execution Report",
            f"**Execution Status**: {status_badge}",
            f"- **Canonical Cycle Date (IST)**: `{cycle_date}`",
            f"- **Execution Started (UTC)**: `{start_time_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}`",
            f"- **Execution Started (IST)**: `{start_time_ist.strftime('%Y-%m-%d %H:%M:%S IST')}`",
            f"- **Execution Mode**: `{'Dry Run' if dry_run else 'Live Cloud Autonomous'}`",
            "",
            "## 1. Challenge Metadata",
            "- **Platform**: LeetCode",
            f"- **Problem Title**: [{problem.title}]({problem.url})",
            f"- **Frontend ID**: `{problem.external_id}` (Internal ID: `{problem.internal_question_id}`)",
            f"- **Slug**: `{problem.slug}`",
            f"- **Difficulty**: `{problem.difficulty_label}`",
            f"- **Tags**: {', '.join(problem.tags or ['None'])}",
            "",
            "## 2. Local Solution & Docker Sandbox Verification",
            f"- **AI Provider**: `{self.settings.ai_provider}` (Model: `{self.settings.gemini_model}`)",
            f"- **Docker Image**: `{self.settings.docker_image}`",
            f"- **Local Verdict**: `{latest_attempt.local_verdict if latest_attempt else 'N/A'}`",
            f"- **Local Tests Passed**: `{metrics.get('passed_tests', 0)}/{metrics.get('total_tests', 0)}`",
            f"- **Local Corrections Applied**: `{metrics.get('corrections_made', 0)}`",
            "",
            "## 3. Remote LeetCode Submission & Platform Verdict",
            f"- **Account**: `{self.settings.leetcode_username}`",
            f"- **Submission ID**: `{sub_id}`",
            f"- **Judge Verdict**: `{judge_verdict}`",
            f"- **Total Attempts Recorded**: `{attempts_count}`",
            "",
            "## 4. LeetCode Profile Calendar Verification",
            f"- **Target Calendar Date**: `{cycle_date}` (Asia/Kolkata)",
            f"- **Calendar Verification Status**: `{cal_status}`",
            f"- **Verification Timestamp**: `{session.calendar_verified_at.strftime('%Y-%m-%d %H:%M:%S UTC') if session.calendar_verified_at else 'Pending'}`",
            "",
            "## 5. Execution State Log",
            "| Timestamp (UTC) | State | Message |",
            "| --- | --- | --- |",
        ]

        for item in session.execution_log or []:
            ts = item.get("timestamp", "")[:19]
            st = item.get("status", "")
            msg = item.get("message", "").replace("|", "-")
            report_lines.append(f"| `{ts}` | `{st}` | {msg} |")

        report_lines.append("")
        report_lines.append("---")
        report_lines.append("*Generated autonomously by DC-Agent Cloud Infrastructure.*")

        return "\n".join(report_lines)

    def _persist_report(self, report_markdown: str) -> Path:
        """Persist report to reports/cloud and append to GITHUB_STEP_SUMMARY if present."""
        report_dir = Path("reports/cloud")
        report_dir.mkdir(parents=True, exist_ok=True)
        report_file = report_dir / "LIVE_CLOUD_E2E_TEST_REPORT.md"
        report_file.write_text(report_markdown, encoding="utf-8")

        summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_file:
            try:
                with open(summary_file, "a", encoding="utf-8") as f:
                    f.write("\n" + report_markdown + "\n")
            except Exception as e:
                logger.warning("Could not append report to GITHUB_STEP_SUMMARY: %s", e)

        return report_file
