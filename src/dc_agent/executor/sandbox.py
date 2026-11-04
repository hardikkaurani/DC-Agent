"""Unified execution sandbox factory and facade."""

import logging

from dc_agent.adapters.base import TestCase
from dc_agent.config import SandboxBackendType, Settings, get_settings
from dc_agent.executor.base import ExecutionSummary, IExecutionSandbox
from dc_agent.executor.docker_runner import DockerExecutionSandbox
from dc_agent.executor.subprocess_runner import SubprocessExecutionSandbox

logger = logging.getLogger(__name__)


class Sandbox(IExecutionSandbox):
    """Facade for sandboxed code execution, delegating to Docker or Subprocess backend."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._docker_runner = DockerExecutionSandbox(image=self.settings.docker_image)
        self._subprocess_runner = SubprocessExecutionSandbox()

    def get_active_backend_name(self) -> str:
        """Return the name of the execution backend that will be used."""
        if (
            self.settings.sandbox_backend == SandboxBackendType.DOCKER
            and self._docker_runner.is_available()
        ):
            return "docker"
        return "subprocess"

    def run_tests(
        self,
        solution_code: str,
        test_cases: list[TestCase],
        timeout_seconds: float | None = None,
        memory_limit_mb: int | None = None,
    ) -> ExecutionSummary:
        """Run tests using the best available sandbox backend."""
        timeout = timeout_seconds or self.settings.execution_timeout
        memory_mb = memory_limit_mb or self.settings.memory_limit_mb

        if self.settings.sandbox_backend == SandboxBackendType.DOCKER:
            if self._docker_runner.is_available():
                logger.info("Executing tests inside Docker container sandbox...")
                return self._docker_runner.run_tests(
                    solution_code, test_cases, timeout_seconds=timeout, memory_limit_mb=memory_mb
                )
            else:
                logger.warning(
                    "Docker backend was requested but Docker daemon is unavailable. "
                    "Falling back to Subprocess execution runner with Windows Job Objects/rlimit."
                )

        # Fallback to subprocess runner
        logger.info(
            "Executing tests in local Subprocess sandbox (timeout=%.1fs, mem=%dMB)...",
            timeout,
            memory_mb,
        )
        return self._subprocess_runner.run_tests(
            solution_code, test_cases, timeout_seconds=timeout, memory_limit_mb=memory_mb
        )
