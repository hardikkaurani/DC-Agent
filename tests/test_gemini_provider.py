"""Unit tests for the Gemini AI Provider."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from google.genai.errors import APIError
from pydantic import SecretStr

from dc_agent.config import Settings
from dc_agent.providers.base import (
    AIProviderError,
    InvalidResponseError,
    ModelUnavailableError,
    RateLimitError,
)
from dc_agent.providers.gemini import GeminiProvider
from dc_agent.providers.schemas import GeneratedSolution


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        GEMINI_API_KEY=SecretStr("test_secret_key_12345678"),
        GEMINI_MODEL="gemini-2.5-flash",
    )


@pytest.mark.asyncio
async def test_gemini_provider_missing_key_raises_error() -> None:
    """Ensure missing API key raises actionable AIProviderError."""
    settings = Settings(GEMINI_API_KEY=SecretStr(""))
    with pytest.raises(AIProviderError) as exc_info:
        GeminiProvider(settings=settings)
    assert "GEMINI_API_KEY is not configured" in str(exc_info.value)


@pytest.mark.asyncio
async def test_gemini_provider_successful_solution_generation(test_settings: Settings) -> None:
    """Ensure structured JSON response from Gemini is correctly parsed into GeneratedSolution."""
    provider = GeminiProvider(settings=test_settings)

    mock_response = MagicMock()
    mock_response.text = """{
        "problem_understanding": "Find two numbers that add up to target.",
        "algorithm": "Hash Map",
        "intuition": "Use a hash table for O(1) lookups of the complement.",
        "python_code": "class Solution:\\n    def twoSum(self, nums, target):\\n        return [0, 1]",
        "time_complexity": "O(N)",
        "space_complexity": "O(N)",
        "edge_cases": ["Negative numbers", "Exact target sum"],
        "assumptions": ["Exactly one solution exists"]
    }"""

    provider._client = MagicMock()
    provider._client.aio.models.generate_content = AsyncMock(return_value=mock_response)

    solution = await provider.generate_solution(
        problem_title="Two Sum",
        problem_description="Given an array of integers nums and an integer target...",
        platform="leetcode",
    )

    assert isinstance(solution, GeneratedSolution)
    assert solution.algorithm == "Hash Map"
    assert "class Solution" in solution.python_code
    assert solution.time_complexity == "O(N)"


@pytest.mark.asyncio
async def test_gemini_provider_empty_response_raises_invalid_response(
    test_settings: Settings,
) -> None:
    """Ensure empty response from Gemini raises InvalidResponseError."""
    provider = GeminiProvider(settings=test_settings)
    mock_response = MagicMock()
    mock_response.text = ""

    provider._client = MagicMock()
    provider._client.aio.models.generate_content = AsyncMock(return_value=mock_response)

    with pytest.raises(InvalidResponseError):
        await provider.generate_solution(
            problem_title="Two Sum",
            problem_description="Description",
            platform="leetcode",
        )


@pytest.mark.asyncio
async def test_gemini_provider_rate_limit_retry_and_exhaustion(test_settings: Settings) -> None:
    """Ensure 429 rate limit triggers retries and eventual RateLimitError."""
    provider = GeminiProvider(settings=test_settings)

    # Mock 429 APIError
    err = APIError(429, {"error": {"message": "RESOURCE_EXHAUSTED: Rate limit exceeded"}})

    provider._client = MagicMock()
    provider._client.aio.models.generate_content = AsyncMock(side_effect=err)

    with pytest.raises(RateLimitError) as exc_info:
        await provider._execute_with_retry(
            "test prompt", GeneratedSolution, max_retries=2, base_delay=0.01
        )

    assert "rate limit" in str(exc_info.value).lower()
    # Ensure raw secret key is never in exception string
    assert "test_secret_key_12345678" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_gemini_provider_model_unavailable_error(test_settings: Settings) -> None:
    """Ensure 404 model not found raises ModelUnavailableError."""
    provider = GeminiProvider(settings=test_settings)
    err = APIError(404, {"error": {"message": "Model not found"}})

    provider._client = MagicMock()
    provider._client.aio.models.generate_content = AsyncMock(side_effect=err)

    with pytest.raises(ModelUnavailableError):
        await provider._execute_with_retry("test prompt", GeneratedSolution, max_retries=1)


@pytest.mark.asyncio
async def test_gemini_provider_health_check(test_settings: Settings) -> None:
    """Verify health check logic."""
    provider = GeminiProvider(settings=test_settings)
    mock_response = MagicMock()
    mock_response.text = "1"

    provider._client = MagicMock()
    provider._client.aio.models.generate_content = AsyncMock(return_value=mock_response)

    assert await provider.health_check() is True

    # When exception occurs
    provider._client.aio.models.generate_content = AsyncMock(side_effect=Exception("Network error"))
    assert await provider.health_check() is False
