"""Core adapter protocols and data transfer objects for DC-Agent."""

from datetime import date
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, Field


class Platform(StrEnum):
    """Supported competitive programming platforms."""

    LEETCODE = "leetcode"
    CODEFORCES = "codeforces"


class ProblemDifficulty(StrEnum):
    """Normalized problem difficulty labels."""

    EASY = "Easy"
    MEDIUM = "Medium"
    HARD = "Hard"


class PlatformVerdict(StrEnum):
    """Platform submission verdicts."""

    ACCEPTED = "Accepted"
    WRONG_ANSWER = "Wrong Answer"
    TIME_LIMIT_EXCEEDED = "Time Limit Exceeded"
    MEMORY_LIMIT_EXCEEDED = "Memory Limit Exceeded"
    RUNTIME_ERROR = "Runtime Error"
    COMPILATION_ERROR = "Compilation Error"
    PENDING = "Pending"
    UNKNOWN = "Unknown"


class TestCase(BaseModel):
    """A test case for local solution execution."""

    __test__ = False

    input_data: str
    expected_output: str | None = None
    is_sample: bool = True
    is_verified: bool = True
    description: str | None = None


class ProblemMetadata(BaseModel):
    """Metadata representing a programming challenge."""

    platform: Platform
    external_id: str
    title: str
    slug: str
    url: str
    difficulty_label: str
    numeric_rating: int | None = None
    tags: list[str] = Field(default_factory=list)
    raw_content: str | None = None
    sample_test_cases: list[TestCase] = Field(default_factory=list)
    metadata_payload: dict[str, Any] = Field(default_factory=dict)


class PlatformSubmission(BaseModel):
    """Verified submission record retrieved from platform APIs."""

    submission_id: str
    platform: Platform
    problem_identifier: str
    verdict: PlatformVerdict
    timestamp_unix: int
    language: str
    time_consumed_ms: int | None = None
    memory_consumed_bytes: int | None = None


class LeetCodeSubmissionResult(BaseModel):
    """Result of an authenticated submission to LeetCode."""

    submission_id: str
    state: str  # PENDING, STARTED, SUCCESS, TIMEOUT
    status_msg: str  # Accepted, Wrong Answer, Time Limit Exceeded, etc.
    is_accepted: bool = False
    status_code: int | None = None
    total_correct: int = 0
    total_testcases: int = 0
    runtime: str | None = None
    memory: str | None = None
    last_testcase: str | None = None
    expected_output: str | None = None
    code_output: str | None = None
    runtime_error: str | None = None
    compile_error: str | None = None
    error_message: str | None = None


class CalendarVerificationResult(BaseModel):
    """Result of querying userProfileCalendar on LeetCode."""

    is_active: bool
    submission_count: int = 0
    streak: int = 0
    total_active_days: int = 0
    cycle_date: str


class ILeetCodeAdapter(Protocol):
    """Protocol defining the interface for LeetCode integrations."""

    async def fetch_daily_challenge(self, target_date: date | None = None) -> ProblemMetadata:
        """Fetch the official Daily Coding Challenge for the specified date."""
        ...

    async def check_recent_submission(
        self,
        username: str,
        problem_slug: str,
        min_timestamp_unix: int = 0,
    ) -> PlatformSubmission | None:
        """Query public user profile submissions for an accepted verdict."""
        ...

    async def submit_solution(
        self,
        slug: str,
        question_id: str,
        typed_code: str,
        session_cookie: str,
        csrf_token: str | None = None,
    ) -> str:
        """Submit code to LeetCode authenticated endpoint and return submission ID."""
        ...

    async def poll_submission_verdict(
        self,
        submission_id: str,
        session_cookie: str,
        csrf_token: str | None = None,
        max_timeout: float = 30.0,
        poll_interval: float = 2.0,
    ) -> LeetCodeSubmissionResult:
        """Poll LeetCode check endpoint until a terminal judge verdict is received."""
        ...

    async def verify_calendar_activity(
        self,
        username: str,
        target_date_ist: str,
    ) -> CalendarVerificationResult:
        """Verify whether target date in Asia/Kolkata is marked active on LeetCode calendar."""
        ...


class ICodeforcesAdapter(Protocol):
    """Protocol defining the interface for Codeforces integrations."""

    async def fetch_problemset(self, tags: list[str] | None = None) -> list[ProblemMetadata]:
        """Fetch problem metadata list from the official problemset.problems endpoint."""
        ...

    async def fetch_user_submissions(
        self,
        handle: str,
        from_index: int = 1,
        count: int = 1000,
    ) -> list[PlatformSubmission]:
        """Fetch user submission history from user.status."""
        ...

    async def check_problem_verdict(
        self,
        handle: str,
        contest_id: int,
        problem_index: str,
        min_timestamp_unix: int,
    ) -> PlatformSubmission | None:
        """Check if the user has an OK submission for the specific problem."""
        ...
