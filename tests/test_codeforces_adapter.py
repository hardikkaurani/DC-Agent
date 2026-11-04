"""Unit tests for the Codeforces REST API adapter."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dc_agent.adapters.base import Platform, PlatformVerdict
from dc_agent.adapters.codeforces import CodeforcesAdapter, CodeforcesAPIError


@pytest.mark.asyncio
async def test_codeforces_fetch_user_submissions_normalizes_verdicts() -> None:
    """Ensure submission history is retrieved and verdicts are mapped accurately."""
    adapter = CodeforcesAdapter()

    mock_result = [
        {
            "id": 1001,
            "contestId": 1890,
            "creationTimeSeconds": 1700000000,
            "programmingLanguage": "Python 3",
            "verdict": "OK",
            "problem": {"contestId": 1890, "index": "A", "name": "Doremy's Paint 3"},
        },
        {
            "id": 1002,
            "contestId": 1890,
            "creationTimeSeconds": 1700000100,
            "programmingLanguage": "Python 3",
            "verdict": "WRONG_ANSWER",
            "problem": {"contestId": 1890, "index": "B", "name": "Qingshan Loves Strings"},
        },
    ]

    with patch.object(adapter, "_get", AsyncMock(return_value=mock_result)):
        submissions = await adapter.fetch_user_submissions("hkvariant", count=10)

        assert len(submissions) == 2
        assert submissions[0].verdict == PlatformVerdict.ACCEPTED
        assert submissions[0].problem_identifier == "1890_A"
        assert submissions[1].verdict == PlatformVerdict.WRONG_ANSWER
        assert submissions[1].problem_identifier == "1890_B"


@pytest.mark.asyncio
async def test_codeforces_fetch_problemset_parses_metadata() -> None:
    """Ensure problemset.problems parses into ProblemMetadata models."""
    adapter = CodeforcesAdapter()

    mock_result = {
        "problems": [
            {
                "contestId": 1900,
                "index": "A",
                "name": "Cover in Water",
                "type": "PROGRAMMING",
                "rating": 800,
                "tags": ["implementation", "greedy"],
            },
            {
                # Missing rating (unrated)
                "contestId": 1900,
                "index": "B",
                "name": "Laura and Operations",
                "type": "PROGRAMMING",
                "tags": ["math"],
            },
        ]
    }

    with patch.object(adapter, "_get", AsyncMock(return_value=mock_result)):
        problemset = await adapter.fetch_problemset()

        assert len(problemset) == 2
        p1 = problemset[0]
        assert p1.platform == Platform.CODEFORCES
        assert p1.external_id == "1900_A"
        assert p1.title == "Cover in Water"
        assert p1.numeric_rating == 800
        assert p1.difficulty_label == "800"
        assert p1.url == "https://codeforces.com/problemset/problem/1900/A"

        p2 = problemset[1]
        assert p2.numeric_rating is None
        assert p2.difficulty_label == "Unrated"


@pytest.mark.asyncio
async def test_codeforces_check_problem_verdict() -> None:
    """Ensure check_problem_verdict identifies Accepted verdict for specific problem."""
    adapter = CodeforcesAdapter()

    with patch.object(
        adapter,
        "fetch_user_submissions",
        AsyncMock(
            return_value=[
                AsyncMock(
                    problem_identifier="1890_A",
                    verdict=PlatformVerdict.ACCEPTED,
                    timestamp_unix=1700000000,
                    submission_id="1001",
                )
            ]
        ),
    ):
        sub = await adapter.check_problem_verdict(
            handle="hkvariant", contest_id=1890, problem_index="A", min_timestamp_unix=1690000000
        )
        assert sub is not None
        assert sub.submission_id == "1001"

        # Timestamp prior to submission
        sub_future = await adapter.check_problem_verdict(
            handle="hkvariant", contest_id=1890, problem_index="A", min_timestamp_unix=1800000000
        )
        assert sub_future is None


@pytest.mark.asyncio
async def test_codeforces_api_error_raised_on_failure_status() -> None:
    """Ensure Codeforces API error response raises CodeforcesAPIError."""
    adapter = CodeforcesAdapter()

    # When Codeforces returns status FAILED with comment
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"status": "FAILED", "comment": "handle: User not found"}

    with patch("httpx.AsyncClient.get", AsyncMock(return_value=mock_resp)):
        with pytest.raises(CodeforcesAPIError) as exc_info:
            await adapter._get("user.info", {"handles": "non_existent"})
        assert "User not found" in str(exc_info.value)
