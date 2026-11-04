"""Codeforces official REST API adapter with conservative rate limiting."""

import asyncio
import logging
import time
from typing import Any, cast

import httpx

from dc_agent.adapters.base import (
    ICodeforcesAdapter,
    Platform,
    PlatformSubmission,
    PlatformVerdict,
    ProblemMetadata,
)

logger = logging.getLogger(__name__)

CODEFORCES_API_BASE = "https://codeforces.com/api"


class CodeforcesAPIError(Exception):
    """Exception raised when Codeforces API returns an error or unexpected status."""

    pass


class CodeforcesAdapter(ICodeforcesAdapter):
    """Official Codeforces REST API client.

    Enforces rate-limiting: maximum 1 request per 2 seconds.
    """

    def __init__(self, base_url: str = CODEFORCES_API_BASE) -> None:
        self.base_url = base_url
        self._last_request_time: float = 0.0
        self._rate_limit_lock = asyncio.Lock()
        self._min_interval: float = 2.0  # At least 2 seconds between requests

    async def _throttle(self) -> None:
        """Ensure rate limits are respected across concurrent requests."""
        async with self._rate_limit_lock:
            now = time.time()
            elapsed = now - self._last_request_time
            if elapsed < self._min_interval:
                sleep_needed = self._min_interval - elapsed
                await asyncio.sleep(sleep_needed)
            self._last_request_time = time.time()

    async def _get(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        """Issue an HTTP GET request to Codeforces API with rate limiting and retries."""
        await self._throttle()
        url = f"{self.base_url}/{endpoint}"

        for attempt in range(1, 4):
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.get(url, params=params)
                    if resp.status_code == 429:
                        await asyncio.sleep(3.0 * attempt)
                        continue
                    resp.raise_for_status()

                    data = resp.json()
                    status = data.get("status")
                    if status != "OK":
                        comment = data.get("comment", "Unknown Codeforces API error")
                        raise CodeforcesAPIError(f"Codeforces API error: {comment}")

                    return data.get("result")

            except httpx.HTTPStatusError as e:
                if attempt == 3:
                    raise CodeforcesAPIError(
                        f"HTTP error {e.response.status_code} from Codeforces: {e}"
                    ) from e
                await asyncio.sleep(2.0 * attempt)
            except (httpx.ConnectError, httpx.TimeoutException) as conn_err:
                if attempt == 3:
                    raise CodeforcesAPIError(
                        f"Network error communicating with Codeforces: {conn_err}"
                    ) from conn_err
                await asyncio.sleep(2.0 * attempt)

        raise CodeforcesAPIError("Exceeded retries querying Codeforces API.")

    async def fetch_user_info(self, handle: str) -> dict[str, Any]:
        """Fetch user profile details (rating, maxRating, rank)."""
        result = await self._get("user.info", {"handles": handle})
        if not result or not isinstance(result, list):
            raise CodeforcesAPIError(f"No user found for handle '{handle}'")
        return cast(dict[str, Any], result[0])

    async def fetch_user_submissions(
        self,
        handle: str,
        from_index: int = 1,
        count: int = 1000,
    ) -> list[PlatformSubmission]:
        """Fetch submission history from user.status."""
        params = {"handle": handle, "from": from_index, "count": count}
        try:
            result = await self._get("user.status", params)
        except CodeforcesAPIError as e:
            if "handle: User not found" in str(e):
                logger.warning("Codeforces handle '%s' not found.", handle)
                return []
            raise

        submissions: list[PlatformSubmission] = []
        for item in result or []:
            prob = item.get("problem", {})
            contest_id = prob.get("contestId")
            index = prob.get("index")
            if not contest_id or not index:
                continue

            prob_id = f"{contest_id}_{index}"
            raw_verdict = item.get("verdict", "")

            # Normalize verdict
            if raw_verdict == "OK":
                verdict = PlatformVerdict.ACCEPTED
            elif raw_verdict == "WRONG_ANSWER":
                verdict = PlatformVerdict.WRONG_ANSWER
            elif raw_verdict == "TIME_LIMIT_EXCEEDED":
                verdict = PlatformVerdict.TIME_LIMIT_EXCEEDED
            elif raw_verdict == "MEMORY_LIMIT_EXCEEDED":
                verdict = PlatformVerdict.MEMORY_LIMIT_EXCEEDED
            else:
                verdict = PlatformVerdict.UNKNOWN

            submissions.append(
                PlatformSubmission(
                    submission_id=str(item.get("id")),
                    platform=Platform.CODEFORCES,
                    problem_identifier=prob_id,
                    verdict=verdict,
                    timestamp_unix=int(item.get("creationTimeSeconds", 0)),
                    language=item.get("programmingLanguage", "unknown"),
                    time_consumed_ms=item.get("timeConsumedMillis"),
                    memory_consumed_bytes=item.get("memoryConsumedBytes"),
                )
            )

        return submissions

    async def fetch_problemset(self, tags: list[str] | None = None) -> list[ProblemMetadata]:
        """Fetch problem catalog from problemset.problems."""
        params = {}
        if tags:
            params["tags"] = ";".join(tags)

        result = await self._get("problemset.problems", params)
        problems_raw = result.get("problems", []) if isinstance(result, dict) else []

        problemset: list[ProblemMetadata] = []
        for p in problems_raw:
            contest_id = p.get("contestId")
            index = p.get("index")
            name = p.get("name")
            rating = p.get("rating")

            if not contest_id or not index or not name:
                continue

            external_id = f"{contest_id}_{index}"
            url = f"https://codeforces.com/problemset/problem/{contest_id}/{index}"

            problemset.append(
                ProblemMetadata(
                    platform=Platform.CODEFORCES,
                    external_id=external_id,
                    title=name,
                    slug=f"{contest_id}-{index}".lower(),
                    url=url,
                    difficulty_label=str(rating) if rating is not None else "Unrated",
                    numeric_rating=rating,
                    tags=p.get("tags", []),
                    metadata_payload=p,
                )
            )

        return problemset

    async def check_problem_verdict(
        self,
        handle: str,
        contest_id: int,
        problem_index: str,
        min_timestamp_unix: int = 0,
    ) -> PlatformSubmission | None:
        """Verify if a user has an OK verdict on a specific problem."""
        submissions = await self.fetch_user_submissions(handle=handle, count=50)
        target_id = f"{contest_id}_{problem_index}"

        for sub in submissions:
            if (
                sub.problem_identifier == target_id
                and sub.verdict == PlatformVerdict.ACCEPTED
                and sub.timestamp_unix >= min_timestamp_unix
            ):
                return sub

        return None
