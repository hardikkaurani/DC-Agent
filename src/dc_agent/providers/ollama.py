"""Ollama secondary AI provider implementation."""

import json
import logging
from typing import Any, cast

import httpx
from pydantic import ValidationError

from dc_agent.config import Settings, get_settings
from dc_agent.providers.base import (
    AIProviderError,
    IAIProvider,
    InvalidResponseError,
    ModelUnavailableError,
)
from dc_agent.providers.schemas import DiagnosticReport, GeneratedSolution, SolutionReview

logger = logging.getLogger(__name__)


class OllamaProvider(IAIProvider):
    """Secondary local AI provider powered by Ollama."""

    provider_name: str = "ollama"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.base_url = self.settings.ollama_base_url.rstrip("/")
        self.model_name = self.settings.ollama_model

    async def _generate_json(self, prompt: str, schema: type[Any]) -> Any:
        """Issue prompt to Ollama with JSON format constraint and parse into target schema."""
        endpoint = f"{self.base_url}/api/generate"
        schema_json = schema.model_json_schema()

        payload = {
            "model": self.model_name,
            "prompt": f"{prompt}\n\nYou must respond in JSON adhering to this schema:\n{json.dumps(schema_json)}",
            "format": "json",
            "stream": False,
            "options": {"temperature": 0.2},
        }

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                res = await client.post(endpoint, json=payload)
                if res.status_code == 404:
                    raise ModelUnavailableError(
                        f"Ollama model '{self.model_name}' not found locally. Run: ollama pull {self.model_name}"
                    )
                res.raise_for_status()

                data = res.json()
                raw_response = data.get("response", "")
                if not raw_response:
                    raise InvalidResponseError("Ollama returned an empty response.")

                parsed_json = json.loads(raw_response)
                return schema.model_validate(parsed_json)

        except (httpx.ConnectError, httpx.TimeoutException) as conn_err:
            raise AIProviderError(
                f"Could not connect to Ollama server at {self.base_url}. Ensure Ollama is running.",
                original_error=conn_err,
            ) from conn_err
        except (ModelUnavailableError, InvalidResponseError):
            raise
        except ValidationError as ve:
            raise InvalidResponseError(
                f"Ollama response did not match expected schema: {ve}", original_error=ve
            ) from ve
        except Exception as e:
            raise AIProviderError(f"Ollama generation failed: {e}", original_error=e) from e

    async def generate_solution(
        self,
        problem_title: str,
        problem_description: str,
        platform: str,
        constraints: str = "",
        sample_tests: list[dict[str, str]] | None = None,
        method_signature: str | None = None,
    ) -> GeneratedSolution:
        prompt = f"Solve {platform.upper()} problem: {problem_title}\n{problem_description}\nConstraints: {constraints}"
        result = await self._generate_json(prompt, GeneratedSolution)
        return cast(GeneratedSolution, result)

    async def review_solution(
        self,
        problem_title: str,
        problem_description: str,
        platform: str,
        solution_code: str,
    ) -> SolutionReview:
        prompt = f"Review Python solution for {problem_title}:\n{solution_code}"
        result = await self._generate_json(prompt, SolutionReview)
        return cast(SolutionReview, result)

    async def diagnose_and_fix(
        self,
        problem_title: str,
        problem_description: str,
        platform: str,
        solution_code: str,
        failure_details: dict[str, Any],
    ) -> DiagnosticReport:
        prompt = f"Fix Python solution for {problem_title}. Failures: {failure_details}\nCode:\n{solution_code}"
        result = await self._generate_json(prompt, DiagnosticReport)
        return cast(DiagnosticReport, result)

    async def health_check(self) -> bool:
        """Verify Ollama is reachable and responsive."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                res = await client.get(f"{self.base_url}/api/tags")
                return res.status_code == 200
        except Exception:
            return False
