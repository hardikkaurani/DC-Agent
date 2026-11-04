"""Core daily challenge orchestrator and session coordinator."""

import hashlib
import logging
from pathlib import Path
from typing import Any

from dc_agent.adapters.base import ProblemMetadata, TestCase
from dc_agent.adapters.codeforces import CodeforcesAdapter
from dc_agent.adapters.leetcode import LeetCodeAdapter
from dc_agent.config import (
    DailyPolicyType,
    Settings,
    get_canonical_cycle_date,
    get_settings,
)
from dc_agent.core.correction import CorrectionEngine
from dc_agent.core.solver import SolverService
from dc_agent.curriculum.engine import CurriculumEngine
from dc_agent.db.models import Problem
from dc_agent.db.repository import Repository
from dc_agent.db.session import get_db, init_db
from dc_agent.executor.base import LocalExecutionVerdict
from dc_agent.executor.sandbox import Sandbox
from dc_agent.providers.factory import get_ai_provider

logger = logging.getLogger(__name__)


class DailyOrchestrator:
    """Orchestrates problem selection, solving, review, testing, and verdict tracking."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.ai_provider = get_ai_provider(settings=self.settings)
        self.cf_adapter = CodeforcesAdapter()
        self.lc_adapter = LeetCodeAdapter()
        self.sandbox = Sandbox(settings=self.settings)
        self.solver = SolverService(provider=self.ai_provider)
        self.correction_engine = CorrectionEngine(provider=self.ai_provider, sandbox=self.sandbox)

    def determine_today_platform(
        self, cycle_date: str | None = None, repo: Repository | None = None
    ) -> str:
        """Determine target platform based on configured policy, existing sessions, and history.

        Replaces flawed day-of-year parity with persistent history alternation.
        """
        policy = self.settings.daily_policy

        if policy == DailyPolicyType.LEETCODE_ONLY:
            return "leetcode"
        if policy == DailyPolicyType.CODEFORCES_ONLY:
            return "codeforces"

        c_date = cycle_date or get_canonical_cycle_date(self.settings)

        if repo is not None:
            # 1. If today already has an initialized or recorded session, lock to that platform
            today_session = repo.get_daily_session_for_date(c_date)
            if today_session:
                return today_session.platform

            # 2. Otherwise alternate based on the most recent completed or recorded session
            last_session = repo.get_latest_daily_session()
            if last_session:
                return "codeforces" if last_session.platform == "leetcode" else "leetcode"
        else:
            with get_db() as db:
                r = Repository(db)
                today_session = r.get_daily_session_for_date(c_date)
                if today_session:
                    return today_session.platform
                last_session = r.get_latest_daily_session()
                if last_session:
                    return "codeforces" if last_session.platform == "leetcode" else "leetcode"

        # Default initial platform for a fresh installation
        return "leetcode"

    def scaffold_workspace(
        self,
        platform: str,
        cycle_date: str,
        problem: Problem,
        code: str,
        test_cases: list[TestCase],
    ) -> Path:
        """Scaffold local problem directory with code, README, and test cases."""
        safe_title = problem.slug.replace("/", "_")
        target_dir = self.settings.workspace_dir / f"{cycle_date}_{platform}_{safe_title}"
        target_dir.mkdir(parents=True, exist_ok=True)

        # Write solution file
        sol_file = target_dir / "solution.py"
        sol_file.write_text(code, encoding="utf-8")

        # Write README
        readme_file = target_dir / "README.md"
        readme_content = f"""# {problem.title} ({platform.upper()})
- **Official URL**: {problem.url}
- **Difficulty**: {problem.difficulty_label}
- **Tags**: {", ".join(problem.tags or [])}
- **Date**: {cycle_date}

## Instructions
1. Review generated solution in `solution.py`.
2. Submit code manually at {problem.url}.
3. Record verdict with `dc-agent record-verdict --session-id <id>` or verify with `dc-agent verify-verdict`.
"""
        readme_file.write_text(readme_content, encoding="utf-8")

        # Write test cases
        if test_cases:
            tests_dir = target_dir / "tests"
            tests_dir.mkdir(exist_ok=True)
            for idx, tc in enumerate(test_cases, start=1):
                (tests_dir / f"input_{idx}.txt").write_text(tc.input_data, encoding="utf-8")
                exp_text = tc.expected_output if tc.expected_output is not None else ""
                (tests_dir / f"expected_{idx}.txt").write_text(exp_text, encoding="utf-8")

        return target_dir

    async def run_daily(self, force_platform: str | None = None) -> dict[str, Any]:
        """Execute or resume the daily challenge workflow."""
        init_db(self.settings.database_url)
        cycle_date = get_canonical_cycle_date(self.settings)

        with get_db() as db:
            repo = Repository(db)

            # Check for existing session on this calendar day (Idempotency & Single Daily Policy)
            existing_session = repo.get_daily_session_for_date(cycle_date)
            problem: Problem

            if existing_session:
                logger.info(
                    "Resuming existing daily session (ID: %d, Platform: %s, Status: %s) for date %s.",
                    existing_session.id,
                    existing_session.platform,
                    existing_session.status,
                    cycle_date,
                )
                session = existing_session
                platform = existing_session.platform
                problem = existing_session.problem

                # Check concurrency: prevent overlapping CLI executions
                if session.status == "RUNNING":
                    raise RuntimeError(
                        f"Daily session for {cycle_date} is currently running in another process. "
                        "Concurrent runs are prevented to avoid duplicate attempts."
                    )

                if session.status in ("ACCEPTED", "EXHAUSTED"):
                    return {
                        "status": session.status,
                        "session_id": session.id,
                        "platform": platform,
                        "date": cycle_date,
                        "problem_title": problem.title,
                        "problem_url": problem.url,
                        "resumed": True,
                        "already_completed": True,
                    }

                # If session already passed local tests, do not regenerate unnecessarily
                if session.status == "LOCAL_PASSED" and session.attempts:
                    latest_att = session.attempts[-1]
                    ws_dir = self.scaffold_workspace(
                        platform=platform,
                        cycle_date=cycle_date,
                        problem=problem,
                        code=latest_att.source_code,
                        test_cases=[
                            TestCase(
                                input_data=t.input_data,
                                expected_output=t.expected_output,
                                is_verified=t.is_verified,
                            )
                            for t in problem.test_cases
                        ],
                    )
                    return {
                        "status": "LOCAL_PASSED",
                        "session_id": session.id,
                        "platform": platform,
                        "date": cycle_date,
                        "problem_title": problem.title,
                        "problem_url": problem.url,
                        "solution_file": str(ws_dir / "solution.py"),
                        "workspace_dir": str(ws_dir),
                        "resumed": True,
                        "already_passed": True,
                        "tests_passed": latest_att.execution_metrics.get("passed_tests", 0),
                        "total_tests": latest_att.execution_metrics.get("total_tests", 0),
                    }

            else:
                # No existing session for today: determine platform and select target problem
                platform = (
                    force_platform
                    or self.determine_today_platform(cycle_date=cycle_date, repo=repo)
                ).lower()
                problem_meta: ProblemMetadata

                if platform == "codeforces":
                    problem_meta = await self._select_codeforces_problem(repo, cycle_date)
                else:
                    problem_meta = await self._select_leetcode_problem()

                test_case_dicts = [
                    {
                        "input_data": tc.input_data,
                        "expected_output": tc.expected_output,
                        "is_sample": tc.is_sample,
                        "is_verified": tc.is_verified,
                    }
                    for tc in problem_meta.sample_test_cases
                ]

                problem = repo.create_or_get_problem(
                    platform=platform,
                    external_id=problem_meta.external_id,
                    title=problem_meta.title,
                    slug=problem_meta.slug,
                    url=problem_meta.url,
                    difficulty_label=problem_meta.difficulty_label,
                    numeric_rating=problem_meta.numeric_rating,
                    description=problem_meta.raw_content,
                    tags=problem_meta.tags,
                    metadata_payload=problem_meta.metadata_payload,
                    test_cases=test_case_dicts,
                )

                handle = (
                    self.settings.codeforces_username
                    if platform == "codeforces"
                    else self.settings.leetcode_username
                )
                session = repo.create_daily_session(
                    platform=platform,
                    handle=handle,
                    cycle_date=cycle_date,
                    problem=problem,
                    retry_budget=self.settings.retry_limit,
                )

            # Check if problem statement is available before calling AI
            desc = (problem.description or "").strip()
            placeholder_names = (
                problem.title.strip(),
                f"{problem.title} on {platform.upper()}",
                f"{problem.title} on {platform.lower()}",
            )
            if not desc or desc in placeholder_names:
                session.status = "STATEMENT_REQUIRED"
                db.commit()
                ws_dir = self.scaffold_workspace(
                    platform=platform,
                    cycle_date=cycle_date,
                    problem=problem,
                    code="# Problem statement required before AI solution generation.\n# Provide statement with: dc-agent codeforces set-statement\n",
                    test_cases=[],
                )
                return {
                    "status": "STATEMENT_REQUIRED",
                    "session_id": session.id,
                    "platform": platform,
                    "date": cycle_date,
                    "problem_title": problem.title,
                    "problem_url": problem.url,
                    "workspace_dir": str(ws_dir),
                    "message": (
                        f"Official {platform.upper()} API provides metadata only. "
                        f"Please provide the problem statement for '{problem.title}' before generating solutions:\n"
                        f"Run: dc-agent codeforces set-statement --statement '<text>' (or --file <path>)"
                    ),
                }

            # Set concurrency lock
            session.status = "RUNNING"
            db.commit()

            test_cases = [
                TestCase(
                    input_data=tc.input_data,
                    expected_output=tc.expected_output,
                    is_verified=tc.is_verified,
                )
                for tc in problem.test_cases
            ]

            initial_code: str
            review_info: dict[str, Any] = {}

            # Restore existing attempt code if available; otherwise generate
            if session.attempts:
                initial_code = session.attempts[-1].source_code
            else:
                sol, review, initial_code = await self.solver.solve_problem(
                    platform=platform,
                    title=problem.title,
                    description=desc,
                    sample_tests=test_cases,
                )
                review_info = review.model_dump()

            # Run sandbox tests with iterative correction loop
            final_code, summary, diagnostics = await self.correction_engine.run_and_correct_loop(
                platform=platform,
                title=problem.title,
                description=desc,
                initial_code=initial_code,
                test_cases=test_cases,
                max_retries=self.settings.retry_limit,
            )

            # Record attempt in database
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
                platform_verdict="PENDING_MANUAL",
            )

            if summary.overall_verdict == LocalExecutionVerdict.PASS and summary.passed_tests > 0:
                session.status = "LOCAL_PASSED"
            elif summary.overall_verdict == LocalExecutionVerdict.UNVERIFIED:
                session.status = "UNVERIFIED"
            else:
                session.status = "LOCAL_FAILED"

            db.commit()

            # Scaffold local workspace
            ws_dir = self.scaffold_workspace(
                platform=platform,
                cycle_date=cycle_date,
                problem=problem,
                code=final_code,
                test_cases=test_cases,
            )

            return {
                "status": session.status,
                "session_id": session.id,
                "platform": platform,
                "date": cycle_date,
                "problem_title": problem.title,
                "problem_url": problem.url,
                "solution_file": str(ws_dir / "solution.py"),
                "workspace_dir": str(ws_dir),
                "review": review_info,
                "tests_passed": summary.passed_tests,
                "total_tests": summary.total_tests,
                "unverified_tests": summary.unverified_tests,
                "corrections": len(diagnostics),
            }

    async def _select_codeforces_problem(
        self, repo: Repository, cycle_date: str
    ) -> ProblemMetadata:
        """Select a Codeforces problem matching current curriculum tier excluding solved problems."""
        handle = self.settings.codeforces_username
        progress = repo.get_curriculum_progress(handle)

        # 1. Fetch solved problems from Codeforces API
        submissions = await self.cf_adapter.fetch_user_submissions(handle=handle, count=1000)
        solved_ids = {s.problem_identifier for s in submissions if s.verdict == "Accepted"}

        # 2. Fetch problemset
        problemset = await self.cf_adapter.fetch_problemset()

        # 3. Filter candidates
        candidates = CurriculumEngine.filter_candidates(
            problemset=problemset,
            solved_ids=solved_ids,
            target_rating=progress.current_rating_target,
            target_tier=progress.current_tier,  # type: ignore[arg-type]
        )

        if not candidates:
            raise RuntimeError(
                f"No eligible Codeforces candidates found for rating {progress.current_rating_target}."
            )

        # Deterministic stable daily selection via hash of canonical cycle_date and target rating
        seed = int(
            hashlib.md5(f"{cycle_date}_{progress.current_rating_target}".encode()).hexdigest(), 16
        )
        chosen = candidates[seed % len(candidates)]
        return chosen

    async def _select_leetcode_problem(self) -> ProblemMetadata:
        """Fetch the official LeetCode Daily Challenge or raise error for manual entry."""
        try:
            return await self.lc_adapter.fetch_daily_challenge()
        except Exception as e:
            logger.warning("Could not automatically retrieve LeetCode daily challenge: %s", e)
            raise RuntimeError(
                "LeetCode Daily Challenge could not be retrieved automatically (Cloudflare WAF / network). "
                "Please set it with: dc-agent leetcode set-daily --url '<url>' or --slug '<slug>'"
            ) from e
