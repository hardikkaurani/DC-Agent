"""Unit tests for the LeetCode adapter and URL normalizer."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dc_agent.adapters.base import PlatformVerdict
from dc_agent.adapters.leetcode import LeetCodeAdapter, LeetCodeAPIError


def test_slug_normalization_from_various_inputs() -> None:
    """Ensure problem slug is cleanly normalized from URLs and messy inputs."""
    adapter = LeetCodeAdapter()

    assert adapter.normalize_slug("https://leetcode.com/problems/two-sum/") == "two-sum"
    assert adapter.normalize_slug("https://leetcode.com/problems/two-sum/description/") == "two-sum"
    assert (
        adapter.normalize_slug("https://leetcode.cn/problems/reverse-linked-list/")
        == "reverse-linked-list"
    )
    assert adapter.normalize_slug("Two Sum") == "two-sum"
    assert (
        adapter.normalize_slug("  Median of Two Sorted Arrays  ") == "median-of-two-sorted-arrays"
    )
    assert adapter.normalize_slug("valid-parentheses") == "valid-parentheses"


def test_url_validation() -> None:
    """Ensure URL validation distinguishes official LeetCode domains from invalid domains."""
    adapter = LeetCodeAdapter()

    assert adapter.validate_url("https://leetcode.com/problems/two-sum/") is True
    assert adapter.validate_url("https://www.leetcode.com/problems/two-sum/") is True
    assert adapter.validate_url("https://leetcode.cn/problems/two-sum/") is True
    assert adapter.validate_url("https://hackerrank.com/challenges/sample") is False
    assert adapter.validate_url("https://phishing-leetcode.com/problem") is False


@pytest.mark.asyncio
async def test_leetcode_fetch_daily_challenge_parses_graphql() -> None:
    """Ensure GraphQL response is parsed into ProblemMetadata."""
    adapter = LeetCodeAdapter()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "data": {
            "activeDailyCodingChallengeQuestion": {
                "date": "2026-11-04",
                "link": "/problems/count-subarrays-where-max-element-appears-at-least-k-times/",
                "question": {
                    "questionId": "3225",
                    "questionFrontendId": "2962",
                    "title": "Count Subarrays Where Max Element Appears at Least K Times",
                    "titleSlug": "count-subarrays-where-max-element-appears-at-least-k-times",
                    "difficulty": "Medium",
                    "isPaidOnly": False,
                    "topicTags": [{"name": "Array"}, {"name": "Sliding Window"}],
                    "content": "<p>Problem HTML</p>",
                    "exampleTestcaseList": ["[1,3,2,3,3]\n2", "[1,4,2,1]\n3"],
                },
            }
        }
    }

    with patch("httpx.AsyncClient.post", AsyncMock(return_value=mock_resp)):
        prob = await adapter.fetch_daily_challenge()

        assert prob.slug == "count-subarrays-where-max-element-appears-at-least-k-times"
        assert prob.difficulty_label == "Medium"
        assert "Array" in prob.tags
        assert "Sliding Window" in prob.tags
        assert len(prob.sample_test_cases) == 2


@pytest.mark.asyncio
async def test_leetcode_fetch_daily_challenge_403_raises_actionable_error() -> None:
    """Ensure Cloudflare 403 raises LeetCodeAPIError with instructions for manual entry."""
    adapter = LeetCodeAdapter()

    mock_resp = AsyncMock()
    mock_resp.status_code = 403

    with patch("httpx.AsyncClient.post", AsyncMock(return_value=mock_resp)):
        with pytest.raises(LeetCodeAPIError) as exc_info:
            await adapter.fetch_daily_challenge()
        assert "Cloudflare WAF blocked automated GraphQL request" in str(exc_info.value)


@pytest.mark.asyncio
async def test_leetcode_check_recent_submissions_matches_accepted() -> None:
    """Ensure user's public recent submissions are checked for platform Accepted."""
    adapter = LeetCodeAdapter()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "data": {
            "recentSubmissionList": [
                {
                    "title": "Two Sum",
                    "titleSlug": "two-sum",
                    "timestamp": "1700000000",
                    "statusDisplay": "Accepted",
                    "lang": "python3",
                },
                {
                    "title": "Add Two Numbers",
                    "titleSlug": "add-two-numbers",
                    "timestamp": "1700000050",
                    "statusDisplay": "Wrong Answer",
                    "lang": "python3",
                },
            ]
        }
    }

    with patch("httpx.AsyncClient.post", AsyncMock(return_value=mock_resp)):
        sub = await adapter.check_recent_submission(
            username="hardikkaurani_", problem_slug="two-sum", min_timestamp_unix=1690000000
        )
        assert sub is not None
        assert sub.verdict == PlatformVerdict.ACCEPTED
        assert sub.problem_identifier == "two-sum"
