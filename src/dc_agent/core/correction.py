"""Iterative diagnostic and correction engine."""

import logging

from dc_agent.adapters.base import TestCase
from dc_agent.core.harness import wrap_leetcode_code
from dc_agent.executor.base import ExecutionSummary, IExecutionSandbox, LocalExecutionVerdict
from dc_agent.providers.base import IAIProvider
from dc_agent.providers.schemas import DiagnosticReport

logger = logging.getLogger(__name__)


class CorrectionEngine:
    """Coordinates local testing, failure diagnostics, and bounded retries."""

    def __init__(self, provider: IAIProvider, sandbox: IExecutionSandbox) -> None:
        self.provider = provider
        self.sandbox = sandbox

    async def run_and_correct_loop(
        self,
        platform: str,
        title: str,
        description: str,
        initial_code: str,
        test_cases: list[TestCase],
        max_retries: int = 5,
    ) -> tuple[str, ExecutionSummary, list[DiagnosticReport]]:
        """Run tests, diagnose any failures with Gemini, and retry up to max_retries."""
        current_code = initial_code
        diagnostics: list[DiagnosticReport] = []

        # Execute tests on initial code
        exec_code = (
            wrap_leetcode_code(current_code) if platform.lower() == "leetcode" else current_code
        )
        summary = self.sandbox.run_tests(exec_code, test_cases)

        if (
            summary.overall_verdict
            in (
                LocalExecutionVerdict.PASS,
                LocalExecutionVerdict.UNVERIFIED,
                LocalExecutionVerdict.NO_VERIFIED_TESTS,
            )
            or not test_cases
        ):
            logger.info("Local test execution completed with verdict: %s.", summary.overall_verdict)
            return current_code, summary, diagnostics

        if summary.overall_verdict == LocalExecutionVerdict.SAFETY_VIOLATION:
            logger.error("Execution aborted due to static AST safety violation.")
            return current_code, summary, diagnostics

        # Iterative correction loop
        for attempt in range(1, max_retries + 1):
            logger.warning(
                "Local tests failed (Verdict: %s). Requesting AI diagnosis and correction (iteration %d/%d)...",
                summary.overall_verdict,
                attempt,
                max_retries,
            )

            # Find the first failing test case
            failing_test = next(
                (
                    r
                    for r in summary.results
                    if r.verdict
                    in (
                        LocalExecutionVerdict.FAIL,
                        LocalExecutionVerdict.RUNTIME_ERROR,
                        LocalExecutionVerdict.TIMEOUT,
                        LocalExecutionVerdict.MEMORY_LIMIT_EXCEEDED,
                    )
                ),
                None,
            )

            failure_details = {
                "overall_verdict": summary.overall_verdict,
                "failing_test_index": failing_test.test_index if failing_test else 1,
                "input": failing_test.input_data if failing_test else "",
                "expected_output": failing_test.expected_output if failing_test else "",
                "actual_output": failing_test.actual_output if failing_test else "",
                "error_message": failing_test.error_message if failing_test else "",
            }

            try:
                diag_report = await self.provider.diagnose_and_fix(
                    problem_title=title,
                    problem_description=description,
                    platform=platform,
                    solution_code=current_code,
                    failure_details=failure_details,
                )
                diagnostics.append(diag_report)
                current_code = diag_report.corrected_code

                # Re-run tests with corrected code
                exec_code = (
                    wrap_leetcode_code(current_code)
                    if platform.lower() == "leetcode"
                    else current_code
                )
                summary = self.sandbox.run_tests(exec_code, test_cases)

                if summary.overall_verdict in (
                    LocalExecutionVerdict.PASS,
                    LocalExecutionVerdict.UNVERIFIED,
                    LocalExecutionVerdict.NO_VERIFIED_TESTS,
                ):
                    logger.info("Tests succeeded after %d correction iteration(s)!", attempt)
                    return current_code, summary, diagnostics

                if summary.overall_verdict == LocalExecutionVerdict.SAFETY_VIOLATION:
                    logger.error("Corrected code triggered safety violation. Aborting retry loop.")
                    return current_code, summary, diagnostics

            except Exception as e:
                logger.error("Diagnostic correction call failed: %s", e)
                break

        logger.warning("Retry budget exhausted (%d attempts).", max_retries)
        return current_code, summary, diagnostics
