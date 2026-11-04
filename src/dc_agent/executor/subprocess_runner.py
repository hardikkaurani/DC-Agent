"""Subprocess execution runner with Windows Job Objects and POSIX resource containment."""

import contextlib
import logging
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from dc_agent.adapters.base import TestCase
from dc_agent.executor.ast_sanitizer import SecurityViolationError, validate_code_safety
from dc_agent.executor.base import (
    MAX_OUTPUT_BYTES,
    ExecutionSummary,
    IExecutionSandbox,
    LocalExecutionVerdict,
    TestCaseResult,
)

logger = logging.getLogger(__name__)

# Platform specific imports for resource limits
HAS_WIN32JOB = False
if sys.platform == "win32":
    try:
        import win32api
        import win32job

        HAS_WIN32JOB = True
    except ImportError:
        HAS_WIN32JOB = False

try:
    import resource

    HAS_RESOURCE = True
except ImportError:
    resource = None  # type: ignore[assignment]
    HAS_RESOURCE = False


def _create_windows_job_object(memory_limit_mb: int) -> Any:
    """Create a Windows Job Object with memory and active process constraints."""
    if not HAS_WIN32JOB:
        return None
    try:
        job = win32job.CreateJobObject(None, "")
        info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        limit_bytes = memory_limit_mb * 1024 * 1024

        # Set job memory limit and process memory limit
        info["ProcessMemoryLimit"] = limit_bytes
        info["JobMemoryLimit"] = limit_bytes
        info["BasicLimitInformation"]["LimitFlags"] = (
            win32job.JOB_OBJECT_LIMIT_PROCESS_MEMORY
            | win32job.JOB_OBJECT_LIMIT_JOB_MEMORY
            | win32job.JOB_OBJECT_LIMIT_ACTIVE_PROCESS
        )
        info["BasicLimitInformation"]["ActiveProcessLimit"] = 4
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
        return job
    except Exception as e:
        logger.warning("Failed to configure Windows Job Object: %s", e)
        return None


class SubprocessExecutionSandbox(IExecutionSandbox):
    """Executes Python solutions in dedicated isolated subprocesses with timeouts and memory caps."""

    def __init__(self) -> None:
        self.isolation_warning = (
            "Weaker Host Isolation Warning: Subprocess execution is active. "
            "Resource limits (Job Objects/rlimit) and AST inspection are applied, "
            "but host filesystem isolation is not as strict as containerized Docker."
        )

    def _normalize_output(self, text: str) -> str:
        """Strip trailing whitespace and normalize CRLF to LF for reliable comparison."""
        lines = [line.rstrip() for line in text.strip().splitlines()]
        return "\n".join(lines)

    def _build_sanitized_env(self) -> dict[str, str]:
        """Construct a minimal environment omitting API keys, user secrets, and credentials."""
        clean_env: dict[str, str] = {
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1",
        }
        # Windows requires SYSTEMROOT and ComSpec to spawn Python processes properly
        if sys.platform == "win32":
            if "SYSTEMROOT" in os.environ:
                clean_env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
            if "COMSPEC" in os.environ:
                clean_env["COMSPEC"] = os.environ["COMSPEC"]
            clean_env["PATH"] = str(Path(sys.executable).parent)
        else:
            clean_env["PATH"] = "/usr/bin:/bin"

        # Explicitly purge any sensitive substring keys from environment
        forbidden_substrings = ("KEY", "SECRET", "TOKEN", "AUTH", "PASS", "GEMINI", "OPENAI", "API")
        for k in list(clean_env.keys()):
            if any(sub in k.upper() for sub in forbidden_substrings):
                del clean_env[k]

        return clean_env

    def run_tests(
        self,
        solution_code: str,
        test_cases: list[TestCase],
        timeout_seconds: float = 2.0,
        memory_limit_mb: int = 256,
    ) -> ExecutionSummary:
        """Execute test cases against the solution code in isolated subprocesses."""
        # Layer 1: Static AST Security Inspection
        try:
            validate_code_safety(solution_code)
        except SecurityViolationError as sec_err:
            return ExecutionSummary(
                overall_verdict=LocalExecutionVerdict.SAFETY_VIOLATION,
                total_tests=len(test_cases),
                passed_tests=0,
                failed_tests=len(test_cases),
                results=[
                    TestCaseResult(
                        test_index=i + 1,
                        verdict=LocalExecutionVerdict.SAFETY_VIOLATION,
                        input_data=tc.input_data,
                        expected_output=tc.expected_output,
                        error_message=str(sec_err),
                    )
                    for i, tc in enumerate(test_cases)
                ],
                backend_used="subprocess",
                isolation_warning=self.isolation_warning,
            )

        test_results: list[TestCaseResult] = []
        total_time_ms = 0.0

        with tempfile.TemporaryDirectory(prefix="dc_sandbox_") as tmpdir:
            solution_file = Path(tmpdir) / "solution.py"
            solution_file.write_text(solution_code, encoding="utf-8")

            clean_env = self._build_sanitized_env()

            for idx, tc in enumerate(test_cases, start=1):
                start_time = time.perf_counter()
                verdict = LocalExecutionVerdict.FAIL
                actual_output: str | None = None
                error_msg: str | None = None

                job_obj = None
                if HAS_WIN32JOB:
                    job_obj = _create_windows_job_object(memory_limit_mb)

                try:
                    # Configure execution command using base Python executable (bypasses launcher quota traps)
                    base_python = getattr(sys, "_base_executable", sys.executable)
                    cmd = [base_python, str(solution_file)]

                    def preexec() -> None:
                        if HAS_RESOURCE and resource is not None:
                            limit_bytes = memory_limit_mb * 1024 * 1024
                            setrlimit = getattr(resource, "setrlimit", None)
                            r_as = getattr(resource, "RLIMIT_AS", None)
                            r_nproc = getattr(resource, "RLIMIT_NPROC", None)
                            if setrlimit and r_as is not None and r_nproc is not None:
                                setrlimit(r_as, (limit_bytes, limit_bytes))
                                setrlimit(r_nproc, (30, 30))

                    proc = subprocess.Popen(
                        cmd,
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        cwd=tmpdir,
                        env=clean_env,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        preexec_fn=preexec if HAS_RESOURCE else None,
                    )

                    if job_obj and HAS_WIN32JOB:
                        try:
                            h_proc = getattr(proc, "_handle", None)
                            if h_proc is not None:
                                win32job.AssignProcessToJobObject(job_obj, int(h_proc))
                        except Exception as e:
                            logger.debug("Could not assign process to job object: %s", e)

                    stdout, stderr = proc.communicate(
                        input=tc.input_data,
                        timeout=timeout_seconds,
                    )

                    elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                    total_time_ms += elapsed_ms

                    # Truncate output to avoid memory bombs
                    stdout_truncated = stdout[:MAX_OUTPUT_BYTES]
                    stderr_truncated = stderr[:MAX_OUTPUT_BYTES]

                    if proc.returncode != 0:
                        verdict = LocalExecutionVerdict.RUNTIME_ERROR
                        error_msg = f"Process exited with code {proc.returncode}. Stderr: {stderr_truncated.strip()}"
                    else:
                        actual_output = stdout_truncated
                        if (
                            tc.expected_output is None
                            or tc.expected_output == ""
                            or not getattr(tc, "is_verified", True)
                        ):
                            verdict = LocalExecutionVerdict.UNVERIFIED
                            error_msg = (
                                "Test executed successfully without errors, "
                                "but expected output was missing or unverified."
                            )
                        else:
                            norm_actual = self._normalize_output(actual_output)
                            norm_expected = self._normalize_output(tc.expected_output)
                            if norm_actual == norm_expected:
                                verdict = LocalExecutionVerdict.PASS
                            else:
                                verdict = LocalExecutionVerdict.FAIL
                                error_msg = f"Output mismatch. Expected:\n{tc.expected_output}\nGot:\n{actual_output}"

                except subprocess.TimeoutExpired:
                    elapsed_ms = timeout_seconds * 1000.0
                    total_time_ms += elapsed_ms
                    if job_obj and HAS_WIN32JOB:
                        with contextlib.suppress(Exception):
                            win32job.TerminateJobObject(job_obj, 1)
                    proc.kill()
                    proc.communicate()
                    verdict = LocalExecutionVerdict.TIMEOUT
                    error_msg = f"Execution exceeded wall-clock timeout of {timeout_seconds:.1f}s."
                except Exception as ex:
                    elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                    total_time_ms += elapsed_ms
                    verdict = LocalExecutionVerdict.RUNTIME_ERROR
                    error_msg = f"Unexpected execution exception: {ex}"
                finally:
                    if job_obj and HAS_WIN32JOB:
                        with contextlib.suppress(Exception):
                            win32api.CloseHandle(job_obj)

                test_results.append(
                    TestCaseResult(
                        test_index=idx,
                        verdict=verdict,
                        input_data=tc.input_data,
                        expected_output=tc.expected_output,
                        actual_output=actual_output,
                        execution_time_ms=elapsed_ms,
                        error_message=error_msg,
                        is_verified=bool(getattr(tc, "is_verified", True) and tc.expected_output),
                    )
                )

        passed_count = sum(1 for r in test_results if r.verdict == LocalExecutionVerdict.PASS)
        unverified_count = sum(
            1 for r in test_results if r.verdict == LocalExecutionVerdict.UNVERIFIED
        )
        failed_count = sum(
            1
            for r in test_results
            if r.verdict
            in (
                LocalExecutionVerdict.FAIL,
                LocalExecutionVerdict.RUNTIME_ERROR,
                LocalExecutionVerdict.TIMEOUT,
                LocalExecutionVerdict.MEMORY_LIMIT_EXCEEDED,
                LocalExecutionVerdict.SAFETY_VIOLATION,
            )
        )

        if not test_cases:
            overall = LocalExecutionVerdict.NO_VERIFIED_TESTS
        elif any(r.verdict == LocalExecutionVerdict.SAFETY_VIOLATION for r in test_results):
            overall = LocalExecutionVerdict.SAFETY_VIOLATION
        elif any(r.verdict == LocalExecutionVerdict.TIMEOUT for r in test_results):
            overall = LocalExecutionVerdict.TIMEOUT
        elif any(r.verdict == LocalExecutionVerdict.MEMORY_LIMIT_EXCEEDED for r in test_results):
            overall = LocalExecutionVerdict.MEMORY_LIMIT_EXCEEDED
        elif any(r.verdict == LocalExecutionVerdict.RUNTIME_ERROR for r in test_results):
            overall = LocalExecutionVerdict.RUNTIME_ERROR
        elif failed_count > 0:
            overall = LocalExecutionVerdict.FAIL
        elif passed_count > 0:
            overall = LocalExecutionVerdict.PASS
        elif unverified_count > 0:
            overall = LocalExecutionVerdict.UNVERIFIED
        else:
            overall = LocalExecutionVerdict.NO_VERIFIED_TESTS

        return ExecutionSummary(
            overall_verdict=overall,
            total_tests=len(test_cases),
            passed_tests=passed_count,
            failed_tests=failed_count,
            unverified_tests=unverified_count,
            results=test_results,
            total_time_ms=total_time_ms,
            backend_used="subprocess",
            isolation_warning=self.isolation_warning,
        )
