"""LeetCode adapter supporting public GraphQL queries and user-assisted workflows."""

import logging
import re
from datetime import date
from urllib.parse import urlparse

import httpx

from dc_agent.adapters.base import (
    ILeetCodeAdapter,
    Platform,
    PlatformSubmission,
    PlatformVerdict,
    ProblemMetadata,
    TestCase,
)

logger = logging.getLogger(__name__)

LEETCODE_GRAPHQL_URL = "https://leetcode.com/graphql"

DAILY_CHALLENGE_QUERY = """
query questionOfToday {
  activeDailyCodingChallengeQuestion {
    date
    link
    question {
      questionId
      questionFrontendId
      title
      titleSlug
      difficulty
      isPaidOnly
      topicTags {
        name
        slug
      }
      content
      exampleTestcaseList
    }
  }
}
"""

USER_RECENT_SUBMISSIONS_QUERY = """
query recentSubmissionList($username: String!, $limit: Int) {
  recentSubmissionList(username: $username, limit: $limit) {
    title
    titleSlug
    timestamp
    statusDisplay
    lang
  }
}
"""


class LeetCodeAPIError(Exception):
    """Raised when LeetCode GraphQL query fails or is blocked."""

    pass


class LeetCodeAdapter(ILeetCodeAdapter):
    """Adapter for LeetCode public endpoints and user-assisted workflows."""

    def __init__(self, base_url: str = LEETCODE_GRAPHQL_URL) -> None:
        self.base_url = base_url

    @staticmethod
    def normalize_slug(slug_or_url: str) -> str:
        """Extract and normalize the problem slug from a full URL or raw slug string."""
        raw = slug_or_url.strip()
        if raw.startswith("http://") or raw.startswith("https://"):
            parsed = urlparse(raw)
            # Match /problems/<slug>/...
            match = re.search(r"/problems/([^/]+)", parsed.path)
            if match:
                return match.group(1).lower()
        # Clean slug string
        return re.sub(r"[^a-z0-9\-]", "", raw.lower().replace(" ", "-"))

    @staticmethod
    def validate_url(url: str) -> bool:
        """Validate whether a URL belongs to official LeetCode domains and points to a problem."""
        try:
            parsed = urlparse(url.strip())
            if parsed.scheme not in ("http", "https"):
                return False
            if parsed.netloc.lower() not in ("leetcode.com", "www.leetcode.com", "leetcode.cn"):
                return False
            return bool(re.search(r"^/problems/[a-z0-9-]+", parsed.path.lower()))
        except Exception:
            return False

    @staticmethod
    def extract_expected_outputs_from_content(content_html: str) -> list[str]:
        """Attempt to reliably extract example expected outputs from LeetCode problem HTML.

        LeetCode problem statements usually format examples with:
        <strong>Input:</strong> ...
        <strong>Output:</strong> &nbsp;...
        """
        if not content_html:
            return []

        pattern = re.compile(
            r"<strong>Output:</strong>\s*(?:<(?:code|span)[^>]*>)?\s*([^<\n\r]+)",
            re.IGNORECASE,
        )
        matches = pattern.findall(content_html)
        cleaned = [
            re.sub(r"<[^>]+>", "", m).replace("&nbsp;", " ").strip() for m in matches if m.strip()
        ]
        return cleaned

    @staticmethod
    def get_profile_url(username: str) -> str:
        """Return official user profile URL."""
        return f"https://leetcode.com/u/{username}/"

    @staticmethod
    def get_problem_url(slug: str) -> str:
        """Return official problem URL."""
        return f"https://leetcode.com/problems/{slug}/"

    async def fetch_daily_challenge(self, target_date: date | None = None) -> ProblemMetadata:
        """Fetch the official Daily Challenge from LeetCode's public GraphQL endpoint.

        If Cloudflare or network blocks the request, raises LeetCodeAPIError so that
        the user can provide the problem slug manually.
        """
        headers = {
            "Content-Type": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": "https://leetcode.com/",
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    self.base_url,
                    json={"query": DAILY_CHALLENGE_QUERY},
                    headers=headers,
                )
                if resp.status_code == 403:
                    raise LeetCodeAPIError(
                        "Cloudflare WAF blocked automated GraphQL request (HTTP 403). "
                        "Please provide the daily challenge slug or URL manually."
                    )
                resp.raise_for_status()

                data = resp.json()
                daily = data.get("data", {}).get("activeDailyCodingChallengeQuestion", {})
                if not daily:
                    raise LeetCodeAPIError("No active daily coding challenge returned by LeetCode.")

                question = daily.get("question", {})
                slug = question.get("titleSlug")
                title = question.get("title", slug)
                difficulty = question.get("difficulty", "Medium")
                frontend_id = question.get("questionFrontendId", "0")
                tags = [t.get("name") for t in question.get("topicTags", []) if t.get("name")]
                content = question.get("content", "")
                examples_raw = question.get("exampleTestcaseList", [])

                extracted_outputs = self.extract_expected_outputs_from_content(content)

                sample_tests: list[TestCase] = []
                for idx, ex in enumerate(examples_raw):
                    if idx < len(extracted_outputs) and extracted_outputs[idx]:
                        sample_tests.append(
                            TestCase(
                                input_data=str(ex),
                                expected_output=extracted_outputs[idx],
                                is_sample=True,
                                is_verified=True,
                            )
                        )
                    else:
                        sample_tests.append(
                            TestCase(
                                input_data=str(ex),
                                expected_output=None,
                                is_sample=True,
                                is_verified=False,
                                description="Input-only sample without verified expected output",
                            )
                        )

                return ProblemMetadata(
                    platform=Platform.LEETCODE,
                    external_id=str(frontend_id),
                    title=title,
                    slug=slug,
                    url=f"https://leetcode.com/problems/{slug}/",
                    difficulty_label=difficulty,
                    tags=tags,
                    raw_content=content,
                    sample_test_cases=sample_tests,
                    metadata_payload=daily,
                )

        except (httpx.ConnectError, httpx.TimeoutException) as conn_err:
            raise LeetCodeAPIError(
                f"Could not connect to LeetCode GraphQL endpoint: {conn_err}"
            ) from conn_err
        except Exception as e:
            if isinstance(e, LeetCodeAPIError):
                raise
            raise LeetCodeAPIError(f"Failed to fetch LeetCode daily challenge: {e}") from e

    async def check_recent_submission(
        self,
        username: str,
        problem_slug: str,
        min_timestamp_unix: int = 0,
    ) -> PlatformSubmission | None:
        """Query public user profile recent submissions for an accepted verdict."""
        headers = {
            "Content-Type": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": f"https://leetcode.com/u/{username}/",
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    self.base_url,
                    json={
                        "query": USER_RECENT_SUBMISSIONS_QUERY,
                        "variables": {"username": username, "limit": 20},
                    },
                    headers=headers,
                )
                if resp.status_code != 200:
                    return None

                data = resp.json()
                submissions = data.get("data", {}).get("recentSubmissionList", [])

                normalized_target = self.normalize_slug(problem_slug)
                for sub in submissions or []:
                    slug = self.normalize_slug(sub.get("titleSlug", ""))
                    status = sub.get("statusDisplay", "")
                    ts = int(sub.get("timestamp", 0))

                    if (
                        slug == normalized_target
                        and status == "Accepted"
                        and ts >= min_timestamp_unix
                    ):
                        return PlatformSubmission(
                            submission_id=f"lc_{ts}_{slug}",
                            platform=Platform.LEETCODE,
                            problem_identifier=slug,
                            verdict=PlatformVerdict.ACCEPTED,
                            timestamp_unix=ts,
                            language=sub.get("lang", "python3"),
                        )

                return None

        except Exception as e:
            logger.warning("Error checking LeetCode recent submissions: %s", e)
            return None
