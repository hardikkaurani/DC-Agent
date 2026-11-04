"""Unit tests for the Curriculum Progression Engine."""

from dc_agent.adapters.base import Platform, ProblemMetadata
from dc_agent.curriculum.engine import CurriculumEngine, get_tier_for_rating
from dc_agent.curriculum.models import (
    CurriculumTier,
    ProgressionState,
)


def test_tier_mapping() -> None:
    """Verify rating-to-tier classification."""
    assert get_tier_for_rating(800) == CurriculumTier.TIER_1_FUNDAMENTALS
    assert get_tier_for_rating(1000) == CurriculumTier.TIER_1_FUNDAMENTALS
    assert get_tier_for_rating(1100) == CurriculumTier.TIER_2_IMPLEMENTATION
    assert get_tier_for_rating(1500) == CurriculumTier.TIER_3_BINARY_SEARCH
    assert get_tier_for_rating(1800) == CurriculumTier.TIER_4_DP_GRAPHS
    assert get_tier_for_rating(2100) == CurriculumTier.TIER_5_ADVANCED
    assert get_tier_for_rating(2400) == CurriculumTier.TIER_6_EXPERT


def test_promotion_rule_on_three_consecutive_accepted() -> None:
    """Ensure 3 consecutive Accepted verdicts increment target rating by 100."""
    state = ProgressionState(
        handle="hkvariant",
        current_tier=CurriculumTier.TIER_1_FUNDAMENTALS,
        current_rating_target=1000,
    )

    # Attempt 1
    event_1 = CurriculumEngine.record_accepted(state)
    assert not event_1.promoted
    assert state.consecutive_accepted == 1
    assert state.current_rating_target == 1000

    # Attempt 2
    event_2 = CurriculumEngine.record_accepted(state)
    assert not event_2.promoted
    assert state.consecutive_accepted == 2
    assert state.current_rating_target == 1000

    # Attempt 3 -> Triggers promotion
    event_3 = CurriculumEngine.record_accepted(state)
    assert event_3.promoted
    assert event_3.previous_rating == 1000
    assert event_3.new_rating == 1100
    assert event_3.new_tier == CurriculumTier.TIER_2_IMPLEMENTATION
    assert state.consecutive_accepted == 0
    assert state.current_rating_target == 1100


def test_demotion_rule_on_two_consecutive_exhausted() -> None:
    """Ensure 2 consecutive exhausted attempts decrement target rating by 100."""
    state = ProgressionState(
        handle="hkvariant",
        current_tier=CurriculumTier.TIER_2_IMPLEMENTATION,
        current_rating_target=1100,
    )

    # Failure 1
    event_1 = CurriculumEngine.record_exhausted(state)
    assert not event_1.demoted
    assert state.consecutive_failed == 1
    assert state.current_rating_target == 1100

    # Failure 2 -> Triggers demotion
    event_2 = CurriculumEngine.record_exhausted(state)
    assert event_2.demoted
    assert event_2.previous_rating == 1100
    assert event_2.new_rating == 1000
    assert event_2.new_tier == CurriculumTier.TIER_1_FUNDAMENTALS
    assert state.consecutive_failed == 0
    assert state.current_rating_target == 1000


def test_demotion_floor_at_800() -> None:
    """Ensure rating target never falls below 800."""
    state = ProgressionState(
        handle="hkvariant",
        current_tier=CurriculumTier.TIER_1_FUNDAMENTALS,
        current_rating_target=800,
    )

    CurriculumEngine.record_exhausted(state)
    event = CurriculumEngine.record_exhausted(state)
    assert event.demoted
    assert event.new_rating == 800  # Cannot drop below 800


def test_candidate_filtering_excludes_solved_problems() -> None:
    """Ensure candidate selection filters out all problems in user's solved set."""
    p1 = ProblemMetadata(
        platform=Platform.CODEFORCES,
        external_id="1000_A",
        title="Problem A",
        slug="problem-a",
        url="https://codeforces.com/problemset/problem/1000/A",
        difficulty_label="800",
        numeric_rating=800,
    )
    p2 = ProblemMetadata(
        platform=Platform.CODEFORCES,
        external_id="1000_B",
        title="Problem B",
        slug="problem-b",
        url="https://codeforces.com/problemset/problem/1000/B",
        difficulty_label="800",
        numeric_rating=800,
    )
    problemset = [p1, p2]
    solved_set = {"1000_A"}

    candidates = CurriculumEngine.filter_candidates(
        problemset=problemset,
        solved_ids=solved_set,
        target_rating=800,
        target_tier=CurriculumTier.TIER_1_FUNDAMENTALS,
    )

    assert len(candidates) == 1
    assert candidates[0].external_id == "1000_B"
