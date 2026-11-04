"""Platform adapters package."""

from dc_agent.adapters.base import (
    ICodeforcesAdapter,
    ILeetCodeAdapter,
    Platform,
    PlatformSubmission,
    PlatformVerdict,
    ProblemDifficulty,
    ProblemMetadata,
    TestCase,
)
from dc_agent.adapters.codeforces import CodeforcesAdapter, CodeforcesAPIError
from dc_agent.adapters.leetcode import LeetCodeAdapter, LeetCodeAPIError

__all__ = [
    "CodeforcesAPIError",
    "CodeforcesAdapter",
    "ICodeforcesAdapter",
    "ILeetCodeAdapter",
    "LeetCodeAPIError",
    "LeetCodeAdapter",
    "Platform",
    "PlatformSubmission",
    "PlatformVerdict",
    "ProblemDifficulty",
    "ProblemMetadata",
    "TestCase",
]
