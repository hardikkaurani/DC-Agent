"""Unit tests for LeetCode authenticated submission and calendar verification."""

import json
from datetime import datetime
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import httpx
import pytest

from dc_agent.adapters.leetcode import LeetCodeAdapter, LeetCodeAPIError


@pytest.mark.asyncio
async def test_get_csrf_token_from_cookies() -> None:
    """Verify extracting CSRF token from client response cookies."""
    adapter = LeetCodeAdapter()

    mock_resp = httpx.Response(
        status_code=200,
        headers={"set-cookie": "csrftoken=mock_token_abc123; path=/"},
        request=httpx.Request("GET", "https://leetcode.com"),
    )

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        token = await adapter.get_csrf_token("test_session_cookie")
        assert token == "mock_token_abc123"


@pytest.mark.asyncio
async def test_get_csrf_token_missing_raises_error() -> None:
    """Verify error raised when CSRF token cannot be extracted."""
    adapter = LeetCodeAdapter()

    mock_resp = httpx.Response(
        status_code=200,
        headers={},
        request=httpx.Request("GET", "https://leetcode.com"),
    )

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        with pytest.raises(LeetCodeAPIError, match="Could not extract csrftoken"):
            await adapter.get_csrf_token("test_session_cookie")


@pytest.mark.asyncio
async def test_submit_solution_success() -> None:
    """Verify successful submission returns submission ID."""
    adapter = LeetCodeAdapter()

    mock_resp = httpx.Response(
        status_code=200,
        json={"submission_id": 987654321},
        request=httpx.Request("POST", "https://leetcode.com/problems/two-sum/submit/"),
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        sub_id = await adapter.submit_solution(
            slug="two-sum",
            question_id="1",
            typed_code="class Solution:\n    pass",
            session_cookie="dummy_sess",
            csrf_token="dummy_csrf",
        )
        assert sub_id == "987654321"


@pytest.mark.asyncio
async def test_submit_solution_403_forbidden() -> None:
    """Verify HTTP 403 Forbidden raises descriptive LeetCodeAPIError."""
    adapter = LeetCodeAdapter()

    mock_resp = httpx.Response(
        status_code=403,
        request=httpx.Request("POST", "https://leetcode.com/problems/two-sum/submit/"),
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        with pytest.raises(LeetCodeAPIError, match="HTTP 403 Forbidden"):
            await adapter.submit_solution(
                slug="two-sum",
                question_id="1",
                typed_code="code",
                session_cookie="sess",
                csrf_token="csrf",
            )


@pytest.mark.asyncio
async def test_submit_solution_429_rate_limit() -> None:
    """Verify HTTP 429 raises rate limit error."""
    adapter = LeetCodeAdapter()

    mock_resp = httpx.Response(
        status_code=429,
        request=httpx.Request("POST", "https://leetcode.com/problems/two-sum/submit/"),
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        with pytest.raises(LeetCodeAPIError, match="rate limited"):
            await adapter.submit_solution(
                slug="two-sum",
                question_id="1",
                typed_code="code",
                session_cookie="sess",
                csrf_token="csrf",
            )


@pytest.mark.asyncio
async def test_poll_submission_verdict_accepted() -> None:
    """Verify polling receives terminal Accepted verdict."""
    adapter = LeetCodeAdapter()

    mock_resp = httpx.Response(
        status_code=200,
        json={
            "state": "SUCCESS",
            "status_msg": "Accepted",
            "status_code": 10,
            "total_correct": 55,
            "total_testcases": 55,
            "status_runtime": "32 ms",
            "status_memory": "17.4 MB",
        },
        request=httpx.Request("GET", "https://leetcode.com/submissions/detail/123/check/"),
    )

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        res = await adapter.poll_submission_verdict(
            submission_id="123",
            session_cookie="sess",
            csrf_token="csrf",
        )
        assert res.is_accepted is True
        assert res.status_msg == "Accepted"
        assert res.total_correct == 55
        assert res.runtime == "32 ms"


@pytest.mark.asyncio
async def test_poll_submission_verdict_wrong_answer() -> None:
    """Verify polling receives terminal Wrong Answer verdict with judge diagnostics."""
    adapter = LeetCodeAdapter()

    mock_resp = httpx.Response(
        status_code=200,
        json={
            "state": "SUCCESS",
            "status_msg": "Wrong Answer",
            "status_code": 11,
            "total_correct": 30,
            "total_testcases": 50,
            "last_testcase": "[1,2,3]\n4",
            "expected_output": "true",
            "code_output": "false",
        },
        request=httpx.Request("GET", "https://leetcode.com/submissions/detail/123/check/"),
    )

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        res = await adapter.poll_submission_verdict(
            submission_id="123",
            session_cookie="sess",
            csrf_token="csrf",
        )
        assert res.is_accepted is False
        assert res.status_msg == "Wrong Answer"
        assert res.last_testcase == "[1,2,3]\n4"
        assert res.expected_output == "true"
        assert res.code_output == "false"


@pytest.mark.asyncio
async def test_poll_submission_verdict_timeout() -> None:
    """Verify polling times out gracefully when judge remains in pending state."""
    adapter = LeetCodeAdapter()

    mock_resp = httpx.Response(
        status_code=200,
        json={"state": "PENDING"},
        request=httpx.Request("GET", "https://leetcode.com/submissions/detail/123/check/"),
    )

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        res = await adapter.poll_submission_verdict(
            submission_id="123",
            session_cookie="sess",
            csrf_token="csrf",
            max_timeout=0.1,
            poll_interval=0.05,
        )
        assert res.state == "TIMEOUT"
        assert res.is_accepted is False


@pytest.mark.asyncio
async def test_verify_calendar_activity_active_match() -> None:
    """Verify calendar matching active day in Asia/Kolkata timezone."""
    adapter = LeetCodeAdapter()

    # Generate an epoch timestamp corresponding to target IST date
    tz_ist = ZoneInfo("Asia/Kolkata")
    dt = datetime(2026, 11, 4, 12, 0, 0, tzinfo=tz_ist)
    epoch_ts = int(dt.timestamp())

    cal_payload = {
        str(epoch_ts): 2,  # 2 submissions on this day
    }

    mock_resp = httpx.Response(
        status_code=200,
        json={
            "data": {
                "matchedUser": {
                    "userCalendar": {
                        "streak": 14,
                        "totalActiveDays": 85,
                        "submissionCalendar": json.dumps(cal_payload),
                    }
                }
            }
        },
        request=httpx.Request("POST", "https://leetcode.com/graphql"),
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        res = await adapter.verify_calendar_activity("hardikkaurani_", "2026-11-04")
        assert res.is_active is True
        assert res.submission_count == 2
        assert res.streak == 14
        assert res.total_active_days == 85
        assert res.cycle_date == "2026-11-04"


@pytest.mark.asyncio
async def test_verify_calendar_activity_not_active() -> None:
    """Verify calendar reports inactive when target day has 0 submissions."""
    adapter = LeetCodeAdapter()

    cal_payload = {
        "1600000000": 1,  # different past epoch
    }

    mock_resp = httpx.Response(
        status_code=200,
        json={
            "data": {
                "matchedUser": {
                    "userCalendar": {
                        "streak": 0,
                        "totalActiveDays": 10,
                        "submissionCalendar": json.dumps(cal_payload),
                    }
                }
            }
        },
        request=httpx.Request("POST", "https://leetcode.com/graphql"),
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        res = await adapter.verify_calendar_activity(
            "hardikkaurani_", "2026-11-04", max_retries=1, retry_delay=0.01
        )
        assert res.is_active is False
        assert res.submission_count == 0
