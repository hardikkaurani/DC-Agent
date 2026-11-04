"""Gemini AI Provider implementation using official Google Gen AI Python SDK."""

import asyncio
import json
import logging
import random
from typing import Any, cast

from google import genai
from google.genai import types
from google.genai.errors import APIError
from pydantic import ValidationError

from dc_agent.config import Settings, get_settings
from dc_agent.providers.base import (
    AIProviderError,
    IAIProvider,
    InvalidResponseError,
    ModelUnavailableError,
    RateLimitError,
)
from dc_agent.providers.schemas import DiagnosticReport, GeneratedSolution, SolutionReview

logger = logging.getLogger(__name__)


class GeminiProvider(IAIProvider):
    """Primary AI provider powered by Google Gemini."""

    provider_name: str = "gemini"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        if not self.settings.gemini_api_key:
            raise AIProviderError(
                "GEMINI_API_KEY is not configured. Please add it to your .env file."
            )

        self._api_key = self.settings.gemini_api_key.get_secret_value()
        self.model_name = self.settings.gemini_model
        self._client = genai.Client(api_key=self._api_key)

    async def _execute_with_retry(
        self,
        prompt: str,
        response_schema: type[Any],
        max_retries: int = 3,
        base_delay: float = 1.0,
    ) -> Any:
        """Execute a generate_content call with bounded retries and exponential backoff."""
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=response_schema,
            temperature=0.2,
        )

        last_error: Exception | None = None

        for attempt in range(1, max_retries + 1):
            try:
                response = await self._client.aio.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config=config,
                )

                if not response.text:
                    raise InvalidResponseError("Gemini returned an empty response.")

                data = json.loads(response.text)
                return response_schema.model_validate(data)

            except ValidationError as ve:
                logger.error("Failed to parse Gemini output into target schema: %s", ve)
                raise InvalidResponseError(
                    f"Gemini output did not match expected schema: {ve}", original_error=ve
                ) from ve

            except APIError as api_err:
                last_error = api_err
                code = getattr(api_err, "code", None)
                msg = str(api_err)

                # Mask any stray occurrences of the API key in the error string
                masked_msg = msg.replace(self._api_key, "***")

                if (
                    code == 429
                    or "RESOURCE_EXHAUSTED" in masked_msg
                    or "rate limit" in masked_msg.lower()
                ):
                    if attempt == max_retries:
                        raise RateLimitError(
                            f"Gemini quota exhausted or rate limit hit: {masked_msg}",
                            original_error=api_err,
                        ) from api_err
                    # Exponential backoff with jitter
                    sleep_time = (base_delay * (2 ** (attempt - 1))) + random.uniform(0.1, 0.5)
                    logger.warning(
                        "Rate limit hit, retrying in %.2fs (attempt %d/%d)",
                        sleep_time,
                        attempt,
                        max_retries,
                    )
                    await asyncio.sleep(sleep_time)
                    continue

                if code == 404 or "not found" in masked_msg.lower():
                    raise ModelUnavailableError(
                        f"Configured model '{self.model_name}' is not accessible or does not exist: {masked_msg}",
                        original_error=api_err,
                    ) from api_err

                # Retry on transient server errors (500, 503)
                if code in (500, 503) and attempt < max_retries:
                    sleep_time = (base_delay * (2 ** (attempt - 1))) + random.uniform(0.1, 0.5)
                    logger.warning("Transient server error %s, retrying in %.2fs", code, sleep_time)
                    await asyncio.sleep(sleep_time)
                    continue

                raise AIProviderError(
                    f"Gemini API error ({code}): {masked_msg}", original_error=api_err
                ) from api_err

            except (InvalidResponseError, RateLimitError, ModelUnavailableError):
                raise

            except Exception as e:
                last_error = e
                masked_msg = str(e).replace(self._api_key, "***")
                if attempt < max_retries:
                    sleep_time = (base_delay * (2 ** (attempt - 1))) + random.uniform(0.1, 0.5)
                    await asyncio.sleep(sleep_time)
                    continue
                raise AIProviderError(
                    f"Unexpected error communicating with Gemini: {masked_msg}", original_error=e
                ) from e

        raise AIProviderError(
            "Exceeded maximum retries for Gemini call.", original_error=last_error
        )

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
        sample_tests_str = json.dumps(sample_tests or [], indent=2)

        prompt = f"""You are an elite competitive programmer and algorithms specialist.
Generate a verified, optimal Python 3 solution for the following {platform.upper()} problem.

Problem Title: {problem_title}
Platform: {platform.upper()}

Description:
{problem_description}

Constraints:
{constraints or "Standard platform constraints."}

Sample Tests:
{sample_tests_str}

Platform Requirements:
"""
        if platform.lower() == "leetcode":
            prompt += f"""- Output a solution adhering to LeetCode's class and method structure.
Expected Signature: {method_signature or "class Solution: def solve(self, ...):"}
- Include complete typing annotations.
- Do NOT read from stdin or write to stdout.
"""
        else:
            prompt += """- Output a complete standalone Python 3 script.
- Read input from sys.stdin (use sys.stdin.readline or sys.stdin.read for fast I/O).
- Print output to sys.stdout.
- Handle multiple test cases (e.g. t = int(sys.stdin.readline())) if specified by the problem.
"""

        prompt += """
Guidelines:
1. Target optimal Big-O time and auxiliary space complexity.
2. Consider zero, negative, maximum boundary values, and performance under worst-case inputs.
3. Provide cleanly indented, commented, readable Python 3 code in python_code.
"""
        result = await self._execute_with_retry(prompt, GeneratedSolution)
        return cast(GeneratedSolution, result)

    async def review_solution(
        self,
        problem_title: str,
        problem_description: str,
        platform: str,
        solution_code: str,
    ) -> SolutionReview:
        """Perform an adversarial, independent review of the solution."""
        prompt = f"""You are a skeptical, adversarial senior competitive programming judge and code reviewer.
Critique the following Python 3 solution for correctness, potential TLE, MLE, and hidden corner case failures.

Problem Title: {problem_title}
Platform: {platform.upper()}

Description:
{problem_description}

Proposed Python 3 Solution:
```python
{solution_code}
```

Instructions:
1. Test the code mentally against extreme corner cases (min/max constraints, duplicates, empty/null, large arrays).
2. Check if Python's recursion limit or object allocation will cause Memory Limit Exceeded (MLE) or Runtime Error (RTE).
3. Check if worst-case time complexity exceeds 1.0–2.0 seconds on standard online judges.
4. Set is_correct to false if ANY logical bug, TLE risk, or edge case failure exists.
"""
        result = await self._execute_with_retry(prompt, SolutionReview)
        return cast(SolutionReview, result)

    async def diagnose_and_fix(
        self,
        problem_title: str,
        problem_description: str,
        platform: str,
        solution_code: str,
        failure_details: dict[str, Any],
    ) -> DiagnosticReport:
        """Diagnose a local test failure or judge rejection and produce a corrected solution."""
        failure_json = json.dumps(failure_details, indent=2)

        prompt = f"""You are fixing a failed competitive programming solution.

Problem: {problem_title} ({platform.upper()})
Description:
{problem_description}

Current Solution:
```python
{solution_code}
```

Failure Details (Test input, expected vs actual output, error message, exit code):
{failure_json}

Instructions:
1. Identify the exact root cause of the failure.
2. Formulate a precise, minimal correction that addresses the bug without introducing regressions.
3. Output the complete corrected Python 3 source code in corrected_code.
"""
        result = await self._execute_with_retry(prompt, DiagnosticReport)
        return cast(DiagnosticReport, result)

    async def health_check(self) -> bool:
        """Verify provider authentication and model availability."""
        try:
            # Issue a minimal test prompt
            test_prompt = "Verify connection. Return 1."
            config = types.GenerateContentConfig(max_output_tokens=10)
            response = await self._client.aio.models.generate_content(
                model=self.model_name,
                contents=test_prompt,
                config=config,
            )
            return bool(response.text)
        except Exception as e:
            logger.warning("Gemini health check failed: %s", str(e).replace(self._api_key, "***"))
            return False
