"""Unified execution sandbox factory and facade."""

import logging
from typing import Any

from dc_agent.adapters.base import TestCase
from dc_agent.config import SandboxBackendType, Settings, get_settings
from dc_agent.executor.base import ExecutionSummary, IExecutionSandbox
from dc_agent.executor.docker_runner import DockerExecutionSandbox
from dc_agent.executor.subprocess_runner import SubprocessExecutionSandbox

logger = logging.getLogger(__name__)


class InsecureSandboxFallbackError(RuntimeError):
    """Raised when Docker backend is required or unavailable and insecure fallback is disabled."""

    pass


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

    def get_security_capabilities(self) -> dict[str, Any]:
        """Return detailed report on active execution capabilities and security guarantees."""
        docker_available = self._docker_runner.is_available()
        active = self.get_active_backend_name()

        if active == "docker":
            isolation_summary = (
                "High Isolation: Hardened Docker container (non-root UID, --network none, "
                "--read-only root, 256MB memory cap, 32MB tmpfs noexec, wall-clock timeout)."
            )
        else:
            isolation_summary = (
                "Limited Host Isolation: Process isolation via Windows Job Objects / POSIX rlimit. "
                "Sanitized environment (zero secrets), stdout truncation (64KB), and 2.0s timeout. "
                "AST inspection applied as defense-in-depth filter, NOT an OS security boundary."
            )

        return {
            "active_backend": active,
            "configured_backend": str(self.settings.sandbox_backend),
            "docker_available": docker_available,
            "allow_insecure_fallback": self.settings.allow_insecure_fallback,
            "memory_limit_mb": self.settings.memory_limit_mb,
            "timeout_seconds": self.settings.execution_timeout,
            "isolation_summary": isolation_summary,
        }

    def run_tests(
        self,
        solution_code: str,
        test_cases: list[TestCase],
        timeout_seconds: float | None = None,
        memory_limit_mb: int | None = None,
    ) -> ExecutionSummary:
        """Run tests using the best available sandbox backend adhering to security policies."""
        timeout = timeout_seconds or self.settings.execution_timeout
        memory_mb = memory_limit_mb or self.settings.memory_limit_mb

        if self.settings.sandbox_backend == SandboxBackendType.DOCKER:
            if self._docker_runner.is_available():
                logger.info("Executing tests inside Docker container sandbox...")
                return self._docker_runner.run_tests(
                    solution_code, test_cases, timeout_seconds=timeout, memory_limit_mb=memory_mb
                )
            else:
                if not self.settings.allow_insecure_fallback:
                    raise InsecureSandboxFallbackError(
                        "Docker execution backend was requested or configured as default, but the Docker daemon is unreachable. "
                        "Executing untrusted generated code safely requires hardened container isolation (--network none, read-only root). "
                        "To explicitly allow local subprocess execution with limited host isolation, set "
                        "ALLOW_INSECURE_FALLBACK=true in your .env or pass --allow-insecure-fallback."
                    )
                logger.warning(
                    "SECURITY WARNING: Docker is unavailable. Falling back to local Subprocess runner with limited isolation "
                    "(Windows Job Objects / POSIX rlimit). AST inspection is defense-in-depth, NOT an OS security boundary."
                )

        # Subprocess runner execution
        logger.info(
            "Executing tests in local Subprocess runner (timeout=%.1fs, mem=%dMB)...",
            timeout,
            memory_mb,
        )
        return self._subprocess_runner.run_tests(
            solution_code, test_cases, timeout_seconds=timeout, memory_limit_mb=memory_mb
        )
