"""AI providers package for DC-Agent."""

from dc_agent.providers.base import (
    AIProviderError,
    IAIProvider,
    InvalidResponseError,
    ModelUnavailableError,
    RateLimitError,
)
from dc_agent.providers.factory import get_ai_provider
from dc_agent.providers.gemini import GeminiProvider
from dc_agent.providers.ollama import OllamaProvider
from dc_agent.providers.schemas import (
    DiagnosticReport,
    GeneratedSolution,
    SolutionReview,
)

__all__ = [
    "AIProviderError",
    "DiagnosticReport",
    "GeminiProvider",
    "GeneratedSolution",
    "IAIProvider",
    "InvalidResponseError",
    "ModelUnavailableError",
    "OllamaProvider",
    "RateLimitError",
    "SolutionReview",
    "get_ai_provider",
]
