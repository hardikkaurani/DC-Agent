"""Domain models for the Codeforces Curriculum and Rating Progression."""

from enum import IntEnum

from pydantic import BaseModel


class CurriculumTier(IntEnum):
    """Curriculum tiers corresponding to Codeforces rating bands."""

    TIER_1_FUNDAMENTALS = 1  # 800 - 1000
    TIER_2_IMPLEMENTATION = 2  # 1100 - 1300
    TIER_3_BINARY_SEARCH = 3  # 1400 - 1600
    TIER_4_DP_GRAPHS = 4  # 1700 - 1900
    TIER_5_ADVANCED = 5  # 2000 - 2200
    TIER_6_EXPERT = 6  # 2300+


TIER_BOUNDS: dict[CurriculumTier, tuple[int, int]] = {
    CurriculumTier.TIER_1_FUNDAMENTALS: (800, 1000),
    CurriculumTier.TIER_2_IMPLEMENTATION: (1100, 1300),
    CurriculumTier.TIER_3_BINARY_SEARCH: (1400, 1600),
    CurriculumTier.TIER_4_DP_GRAPHS: (1700, 1900),
    CurriculumTier.TIER_5_ADVANCED: (2000, 2200),
    CurriculumTier.TIER_6_EXPERT: (2300, 3500),
}

TIER_TAGS: dict[CurriculumTier, list[str]] = {
    CurriculumTier.TIER_1_FUNDAMENTALS: ["implementation", "math", "brute force"],
    CurriculumTier.TIER_2_IMPLEMENTATION: ["implementation", "greedy", "sortings"],
    CurriculumTier.TIER_3_BINARY_SEARCH: ["binary search", "two pointers", "number theory"],
    CurriculumTier.TIER_4_DP_GRAPHS: ["dp", "graphs", "constructive algorithms", "dfs and similar"],
    CurriculumTier.TIER_5_ADVANCED: ["data structures", "trees", "flows", "dsu"],
    CurriculumTier.TIER_6_EXPERT: ["combinatorics", "geometry", "string suffix structures"],
}


class ProgressionState(BaseModel):
    """Represents the current curriculum and rating state of a user."""

    handle: str
    current_tier: CurriculumTier = CurriculumTier.TIER_1_FUNDAMENTALS
    current_rating_target: int = 800
    consecutive_accepted: int = 0
    consecutive_failed: int = 0
    retry_budget_per_problem: int = 3


class ProgressionEvent(BaseModel):
    """The result of an evaluation step on the user's progress."""

    promoted: bool = False
    demoted: bool = False
    previous_rating: int
    new_rating: int
    previous_tier: CurriculumTier
    new_tier: CurriculumTier
    reason: str
