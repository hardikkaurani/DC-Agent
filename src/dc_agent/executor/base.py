"""Base execution protocols, verdicts, and data models."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import ClassVar, Protocol

from dc_agent.adapters.base import TestCase

MAX_OUTPUT_BYTES = 64 * 1024  # 64 KB maximum stdout/stderr capture limit


class LocalExecutionVerdict(StrEnum):
    """Verdicts for local sandbox execution."""

    PASS = "PASS"
    FAIL = "FAIL"
    TIMEOUT = "TIMEOUT"
    MEMORY_LIMIT_EXCEEDED = "MEMORY_LIMIT_EXCEEDED"
    RUNTIME_ERROR = "RUNTIME_ERROR"
    SAFETY_VIOLATION = "SAFETY_VIOLATION"
    UNVERIFIED = "UNVERIFIED"  # Ran without error, but expected output was missing
    NO_VERIFIED_TESTS = "NO_VERIFIED_TESTS"  # No verified test cases available to check


@dataclass
class TestCaseResult:
    """Result of running a single test case."""

    __test__: ClassVar[bool] = False

    test_index: int
    verdict: LocalExecutionVerdict
    input_data: str
    expected_output: str | None = None
    actual_output: str | None = None
    execution_time_ms: float = 0.0
    error_message: str | None = None
    is_verified: bool = True


@dataclass
class ExecutionSummary:
    """Aggregated summary of running a test suite."""

    overall_verdict: LocalExecutionVerdict
    total_tests: int
    passed_tests: int
    failed_tests: int
    unverified_tests: int = 0
    results: list[TestCaseResult] = field(default_factory=list)
    total_time_ms: float = 0.0
    backend_used: str = "unknown"
    isolation_warning: str | None = None


class IExecutionSandbox(Protocol):
    """Protocol for local code execution runners."""

    def run_tests(
        self,
        solution_code: str,
        test_cases: list[TestCase],
        timeout_seconds: float = 2.0,
        memory_limit_mb: int = 256,
    ) -> ExecutionSummary:
        """Execute test cases against the solution code."""
        ...
