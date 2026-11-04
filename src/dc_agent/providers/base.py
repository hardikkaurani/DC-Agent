"""Base protocol and custom exceptions for AI providers."""

from typing import Any, Protocol

from dc_agent.providers.schemas import DiagnosticReport, GeneratedSolution, SolutionReview


class AIProviderError(Exception):
    """Base exception for AI provider failures."""

    def __init__(self, message: str, original_error: Exception | None = None) -> None:
        # Sanitize message to ensure no secrets or sensitive data leak
        clean_msg = message.replace("\r", " ").replace("\n", " ")
        super().__init__(clean_msg)
        self.original_error = original_error


class RateLimitError(AIProviderError):
    """Raised when the AI provider throttles requests or exhausts quota."""

    pass


class ModelUnavailableError(AIProviderError):
    """Raised when the requested model is not found or unsupported."""

    pass


class InvalidResponseError(AIProviderError):
    """Raised when AI response is empty, malformed, or fails schema validation."""

    pass


class IAIProvider(Protocol):
    """Protocol defining the interface for AI reasoning and code generation providers."""

    provider_name: str
    model_name: str

    async def generate_solution(
        self,
        problem_title: str,
        problem_description: str,
        platform: str,
        constraints: str = "",
        sample_tests: list[dict[str, str]] | None = None,
        method_signature: str | None = None,
    ) -> GeneratedSolution:
        """Generate a structured algorithmic solution in Python 3."""
        ...

    async def review_solution(
        self,
        problem_title: str,
        problem_description: str,
        platform: str,
        solution_code: str,
    ) -> SolutionReview:
        """Perform an adversarial, independent code review of the solution."""
        ...

    async def diagnose_and_fix(
        self,
        problem_title: str,
        problem_description: str,
        platform: str,
        solution_code: str,
        failure_details: dict[str, Any],
    ) -> DiagnosticReport:
        """Diagnose a local test failure or judge rejection and produce a corrected solution."""
        ...

    async def health_check(self) -> bool:
        """Verify provider authentication and model availability."""
        ...
