"""Docker-based isolated execution sandbox."""

import contextlib
import logging
import tempfile
import time
from pathlib import Path

import docker
from docker.errors import ContainerError, DockerException, ImageNotFound

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


class DockerExecutionSandbox(IExecutionSandbox):
    """Executes code inside a strictly constrained Docker container with no network access."""

    def __init__(self, image: str = "python:3.12-slim") -> None:
        self.image = image
        self._client: docker.DockerClient | None = None

    def is_available(self) -> bool:
        """Check if Docker daemon is running and reachable."""
        try:
            if self._client is None:
                self._client = docker.from_env()
            self._client.ping()
            return True
        except Exception:
            return False

    def _normalize_output(self, text: str) -> str:
        lines = [line.rstrip() for line in text.strip().splitlines()]
        return "\n".join(lines)

    def run_tests(
        self,
        solution_code: str,
        test_cases: list[TestCase],
        timeout_seconds: float = 2.0,
        memory_limit_mb: int = 256,
    ) -> ExecutionSummary:
        """Run solution in a hardened Docker container."""
        if not self.is_available():
            raise DockerException("Docker daemon is not running or accessible.")

        assert self._client is not None

        # Layer 1: AST check
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
                backend_used="docker",
            )

        test_results: list[TestCaseResult] = []
        total_time_ms = 0.0

        with tempfile.TemporaryDirectory(prefix="dc_docker_") as host_tmpdir:
            sol_path = Path(host_tmpdir) / "solution.py"
            sol_path.write_text(solution_code, encoding="utf-8")

            # Ensure image is present
            try:
                self._client.images.get(self.image)
            except ImageNotFound:
                logger.info("Pulling Docker sandbox image %s...", self.image)
                self._client.images.pull(self.image)

            for idx, tc in enumerate(test_cases, start=1):
                input_file = Path(host_tmpdir) / f"input_{idx}.txt"
                input_file.write_text(tc.input_data, encoding="utf-8")

                start_time = time.perf_counter()
                verdict = LocalExecutionVerdict.FAIL
                actual_output: str | None = None
                error_msg: str | None = None

                container = None
                try:
                    # Run container with maximum isolation
                    container = self._client.containers.create(
                        image=self.image,
                        command=[
                            "sh",
                            "-c",
                            f"python /workspace/solution.py < /workspace/input_{idx}.txt",
                        ],
                        volumes={
                            str(Path(host_tmpdir).resolve()): {
                                "bind": "/workspace",
                                "mode": "ro",
                            }
                        },
                        network_mode="none",
                        mem_limit=f"{memory_limit_mb}m",
                        nano_cpus=1000000000,
                        pids_limit=30,
                        user="1000:1000",
                        read_only=True,
                        tmpfs={"/tmp": "size=32m,noexec"},
                    )

                    container.start()

                    # Wait with timeout
                    try:
                        res = container.wait(timeout=int(timeout_seconds) + 1)
                        exit_code = res.get("StatusCode", 1)
                        stdout = container.logs(stdout=True, stderr=False).decode(
                            "utf-8", errors="replace"
                        )
                        stderr = container.logs(stdout=False, stderr=True).decode(
                            "utf-8", errors="replace"
                        )

                        stdout_truncated = stdout[:MAX_OUTPUT_BYTES]
                        stderr_truncated = stderr[:MAX_OUTPUT_BYTES]

                        if exit_code != 0:
                            if exit_code == 137:
                                verdict = LocalExecutionVerdict.MEMORY_LIMIT_EXCEEDED
                                error_msg = f"Container terminated with OOM / timeout (exit code {exit_code})."
                            else:
                                verdict = LocalExecutionVerdict.RUNTIME_ERROR
                                error_msg = f"Process exited with code {exit_code}. Stderr: {stderr_truncated.strip()}"
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

                    except Exception as wait_err:
                        # Timeout
                        container.kill()
                        verdict = LocalExecutionVerdict.TIMEOUT
                        error_msg = f"Container execution exceeded timeout: {wait_err}"

                except ContainerError as ce:
                    verdict = LocalExecutionVerdict.RUNTIME_ERROR
                    error_msg = f"Container runtime error: {ce}"
                except Exception as ex:
                    verdict = LocalExecutionVerdict.RUNTIME_ERROR
                    error_msg = f"Docker execution failure: {ex}"
                finally:
                    if container:
                        with contextlib.suppress(Exception):
                            container.remove(force=True)

                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                total_time_ms += elapsed_ms

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
            backend_used="docker",
        )
