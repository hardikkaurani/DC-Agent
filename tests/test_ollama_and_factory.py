"""Tests for Ollama AI Provider and Provider Factory."""

import json
from unittest.mock import MagicMock, patch

import pytest
from pydantic import SecretStr

from dc_agent.config import AIProviderType, Settings
from dc_agent.providers.base import AIProviderError, InvalidResponseError, ModelUnavailableError
from dc_agent.providers.factory import get_ai_provider
from dc_agent.providers.gemini import GeminiProvider
from dc_agent.providers.ollama import OllamaProvider


def test_factory_gemini_provider() -> None:
    settings = Settings(
        AI_PROVIDER=AIProviderType.GEMINI,
        GEMINI_API_KEY=SecretStr("AQ.TestKey12345678901234567890"),
        GEMINI_MODEL="gemini-2.5-flash",
    )
    provider = get_ai_provider(settings=settings)
    assert isinstance(provider, GeminiProvider)
    assert provider.provider_name == "gemini"


def test_factory_ollama_provider() -> None:
    settings = Settings(
        AI_PROVIDER=AIProviderType.OLLAMA,
        OLLAMA_BASE_URL="http://localhost:11434",
        OLLAMA_MODEL="qwen2.5-coder:7b",
    )
    provider = get_ai_provider(settings=settings)
    assert isinstance(provider, OllamaProvider)
    assert provider.provider_name == "ollama"


def test_factory_unsupported_provider() -> None:
    settings = Settings(AI_PROVIDER=AIProviderType.GEMINI)
    with pytest.raises(AIProviderError):
        get_ai_provider("non_existent_provider", settings=settings)


@pytest.mark.asyncio
async def test_ollama_generate_solution_success() -> None:
    settings = Settings(
        AI_PROVIDER=AIProviderType.OLLAMA,
        OLLAMA_BASE_URL="http://localhost:11434",
        OLLAMA_MODEL="deepseek-coder:6.7b",
    )
    provider = OllamaProvider(settings=settings)

    mock_response_data = {
        "problem_understanding": "Understanding text",
        "algorithm": "greedy",
        "intuition": "intuition text",
        "python_code": "def solve(): pass",
        "time_complexity": "O(N)",
        "space_complexity": "O(1)",
        "edge_cases": ["N=1"],
        "assumptions": ["non-negative integers"],
    }

    mock_res = MagicMock()
    mock_res.status_code = 200
    mock_res.json.return_value = {"response": json.dumps(mock_response_data)}
    mock_res.raise_for_status.return_value = None

    with patch("httpx.AsyncClient.post", return_value=mock_res):
        sol = await provider.generate_solution(
            problem_title="Problem X",
            problem_description="Solve problem X",
            platform="codeforces",
        )
        assert sol.algorithm == "greedy"
        assert sol.python_code == "def solve(): pass"


@pytest.mark.asyncio
async def test_ollama_model_unavailable_404() -> None:
    settings = Settings(
        AI_PROVIDER=AIProviderType.OLLAMA,
        OLLAMA_BASE_URL="http://localhost:11434",
        OLLAMA_MODEL="missing-model",
    )
    provider = OllamaProvider(settings=settings)

    mock_res = MagicMock()
    mock_res.status_code = 404

    with (
        patch("httpx.AsyncClient.post", return_value=mock_res),
        pytest.raises(ModelUnavailableError, match="not found locally"),
    ):
        await provider.generate_solution(
            problem_title="Problem X",
            problem_description="Solve problem X",
            platform="codeforces",
        )


@pytest.mark.asyncio
async def test_ollama_empty_response() -> None:
    settings = Settings(
        AI_PROVIDER=AIProviderType.OLLAMA,
        OLLAMA_BASE_URL="http://localhost:11434",
        OLLAMA_MODEL="deepseek-coder",
    )
    provider = OllamaProvider(settings=settings)

    mock_res = MagicMock()
    mock_res.status_code = 200
    mock_res.json.return_value = {"response": ""}
    mock_res.raise_for_status.return_value = None

    with (
        patch("httpx.AsyncClient.post", return_value=mock_res),
        pytest.raises(InvalidResponseError, match="empty response"),
    ):
        await provider.generate_solution(
            problem_title="Problem X",
            problem_description="Solve problem X",
            platform="codeforces",
        )
