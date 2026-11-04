"""LeetCode adapter supporting public GraphQL queries and user-assisted workflows."""

import asyncio
import json
import logging
import re
from datetime import date, datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx

from dc_agent.adapters.base import (
    CalendarVerificationResult,
    ILeetCodeAdapter,
    LeetCodeSubmissionResult,
    Platform,
    PlatformSubmission,
    PlatformVerdict,
    ProblemMetadata,
    TestCase,
)

logger = logging.getLogger(__name__)

LEETCODE_GRAPHQL_URL = "https://leetcode.com/graphql"
LEETCODE_BASE_URL = "https://leetcode.com"

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

USER_PROFILE_CALENDAR_QUERY = """
query userProfileCalendar($username: String!, $year: Int) {
  matchedUser(username: $username) {
    userCalendar(year: $year) {
      activeYears
      streak
      totalActiveDays
      submissionCalendar
    }
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

    async def get_csrf_token(self, session_cookie: str) -> str:
        """Extract CSRF token using the authenticated session cookie."""
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Cookie": f"LEETCODE_SESSION={session_cookie}",
        }
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                resp = await client.get(LEETCODE_BASE_URL, headers=headers)
                token = client.cookies.get("csrftoken") or resp.cookies.get("csrftoken")
                if not token:
                    # Parse Set-Cookie header if not populated in cookie jar
                    set_cookie = resp.headers.get("set-cookie", "")
                    match = re.search(r"csrftoken=([a-zA-Z0-9_-]+)", set_cookie)
                    if match:
                        token = match.group(1)

                if token:
                    return token
                raise LeetCodeAPIError(
                    "Could not extract csrftoken from LeetCode. Verify LEETCODE_SESSION is valid."
                )
        except Exception as e:
            if isinstance(e, LeetCodeAPIError):
                raise
            raise LeetCodeAPIError(f"Failed to fetch CSRF token from LeetCode: {e}") from e

    async def submit_solution(
        self,
        slug: str,
        question_id: str,
        typed_code: str,
        session_cookie: str,
        csrf_token: str | None = None,
    ) -> str:
        """Submit Python 3 solution directly to LeetCode authenticated endpoint."""
        token = csrf_token
        if not token:
            token = await self.get_csrf_token(session_cookie)

        submit_url = f"{LEETCODE_BASE_URL}/problems/{slug}/submit/"
        headers = {
            "Content-Type": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": f"{LEETCODE_BASE_URL}/problems/{slug}/",
            "Origin": LEETCODE_BASE_URL,
            "x-csrftoken": token,
            "Cookie": f"LEETCODE_SESSION={session_cookie}; csrftoken={token}",
        }
        payload = {
            "question_id": str(question_id),
            "lang": "python3",
            "typed_code": typed_code,
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(submit_url, json=payload, headers=headers)
                if resp.status_code == 403:
                    raise LeetCodeAPIError(
                        "LeetCode rejected submission (HTTP 403 Forbidden). "
                        "Authentication failed or session expired. Please update LEETCODE_SESSION."
                    )
                if resp.status_code == 429:
                    raise LeetCodeAPIError(
                        "LeetCode rate limited submission requests (HTTP 429). "
                        "Please wait before submitting again."
                    )
                resp.raise_for_status()

                data = resp.json()
                sub_id = data.get("submission_id")
                if sub_id:
                    return str(sub_id)
                if "error" in data:
                    raise LeetCodeAPIError(f"LeetCode submission rejected: {data['error']}")
                raise LeetCodeAPIError(
                    f"Unexpected response structure from LeetCode submit: {data}"
                )
        except Exception as e:
            if isinstance(e, LeetCodeAPIError):
                raise
            raise LeetCodeAPIError(f"Failed to submit solution to LeetCode: {e}") from e

    async def poll_submission_verdict(
        self,
        submission_id: str,
        session_cookie: str,
        csrf_token: str | None = None,
        max_timeout: float = 30.0,
        poll_interval: float = 2.0,
    ) -> LeetCodeSubmissionResult:
        """Poll the LeetCode check endpoint until a terminal verdict is reached."""
        token = csrf_token
        if not token:
            token = await self.get_csrf_token(session_cookie)

        check_url = f"{LEETCODE_BASE_URL}/submissions/detail/{submission_id}/check/"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": f"{LEETCODE_BASE_URL}/submissions/detail/{submission_id}/",
            "x-csrftoken": token,
            "Cookie": f"LEETCODE_SESSION={session_cookie}; csrftoken={token}",
        }

        start_time = asyncio.get_event_loop().time()
        async with httpx.AsyncClient(timeout=10.0) as client:
            while (asyncio.get_event_loop().time() - start_time) < max_timeout:
                try:
                    resp = await client.get(check_url, headers=headers)
                    if resp.status_code == 200:
                        data = resp.json()
                        state = data.get("state", "PENDING")
                        if state == "SUCCESS":
                            status_msg = data.get("status_msg", "Unknown")
                            is_accepted = status_msg == "Accepted"
                            return LeetCodeSubmissionResult(
                                submission_id=str(submission_id),
                                state=state,
                                status_msg=status_msg,
                                is_accepted=is_accepted,
                                status_code=data.get("status_code"),
                                total_correct=data.get("total_correct", 0),
                                total_testcases=data.get("total_testcases", 0),
                                runtime=data.get("status_runtime"),
                                memory=data.get("status_memory"),
                                last_testcase=data.get("last_testcase"),
                                expected_output=data.get("expected_output"),
                                code_output=data.get("code_output"),
                                runtime_error=data.get("runtime_error"),
                                compile_error=data.get("compile_error"),
                                error_message=data.get("full_compile_error")
                                or data.get("full_runtime_error"),
                            )
                except httpx.HTTPError as http_err:
                    logger.warning(
                        "Transient error polling submission %s: %s", submission_id, http_err
                    )

                await asyncio.sleep(poll_interval)

        return LeetCodeSubmissionResult(
            submission_id=str(submission_id),
            state="TIMEOUT",
            status_msg="Pending Judge Evaluation",
            is_accepted=False,
            error_message=f"Polling exceeded timeout limit of {max_timeout:.1f}s.",
        )

    async def verify_calendar_activity(
        self,
        username: str,
        target_date_ist: str,
        max_retries: int = 3,
        retry_delay: float = 3.0,
    ) -> CalendarVerificationResult:
        """Verify that the target date in Asia/Kolkata has non-zero submissions in user calendar."""
        headers = {
            "Content-Type": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": f"{LEETCODE_BASE_URL}/u/{username}/",
        }

        try:
            year = int(target_date_ist.split("-")[0])
        except Exception:
            year = datetime.now().year

        tz = ZoneInfo("Asia/Kolkata")

        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(
                        self.base_url,
                        json={
                            "query": USER_PROFILE_CALENDAR_QUERY,
                            "variables": {"username": username, "year": year},
                        },
                        headers=headers,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        matched_user = data.get("data", {}).get("matchedUser") or {}
                        user_cal = matched_user.get("userCalendar") or {}
                        raw_cal_str = user_cal.get("submissionCalendar", "{}")
                        try:
                            cal_dict = (
                                json.loads(raw_cal_str)
                                if isinstance(raw_cal_str, str)
                                else raw_cal_str
                            )
                        except Exception:
                            cal_dict = {}

                        streak = user_cal.get("streak", 0)
                        total_days = user_cal.get("totalActiveDays", 0)

                        for epoch_str, count in cal_dict.items():
                            ts = int(epoch_str)
                            # Convert epoch seconds to date string in Asia/Kolkata
                            d_str = datetime.fromtimestamp(ts, tz=tz).strftime("%Y-%m-%d")
                            if d_str == target_date_ist and count > 0:
                                return CalendarVerificationResult(
                                    is_active=True,
                                    submission_count=count,
                                    streak=streak,
                                    total_active_days=total_days,
                                    cycle_date=target_date_ist,
                                )

                        if attempt < max_retries - 1:
                            await asyncio.sleep(retry_delay)
            except Exception as e:
                logger.warning(
                    "Error verifying LeetCode calendar on attempt %d: %s", attempt + 1, e
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)

        return CalendarVerificationResult(
            is_active=False,
            submission_count=0,
            streak=0,
            total_active_days=0,
            cycle_date=target_date_ist,
        )
