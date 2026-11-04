"""Curriculum progression engine and problem selection algorithms."""

from dc_agent.adapters.base import ProblemMetadata
from dc_agent.curriculum.models import (
    TIER_BOUNDS,
    CurriculumTier,
    ProgressionEvent,
    ProgressionState,
)


def get_tier_for_rating(rating: int) -> CurriculumTier:
    """Determine the curriculum tier for a given rating."""
    for tier, (low, high) in TIER_BOUNDS.items():
        if low <= rating <= high:
            return tier
    if rating < 800:
        return CurriculumTier.TIER_1_FUNDAMENTALS
    return CurriculumTier.TIER_6_EXPERT


class CurriculumEngine:
    """Manages rating progression, tier promotion/demotion, and candidate filtering."""

    PROMOTION_THRESHOLD: int = 3
    DEMOTION_THRESHOLD: int = 2
    RATING_STEP: int = 100
    MIN_RATING: int = 800
    MAX_RATING: int = 3500

    @classmethod
    def record_accepted(cls, state: ProgressionState) -> ProgressionEvent:
        """Process an ACCEPTED verdict. Promotes after threshold consecutive successes."""
        state.consecutive_accepted += 1
        state.consecutive_failed = 0

        prev_rating = state.current_rating_target
        prev_tier = state.current_tier

        if state.consecutive_accepted >= cls.PROMOTION_THRESHOLD:
            new_rating = min(cls.MAX_RATING, prev_rating + cls.RATING_STEP)
            new_tier = get_tier_for_rating(new_rating)
            state.current_rating_target = new_rating
            state.current_tier = new_tier
            state.consecutive_accepted = 0

            return ProgressionEvent(
                promoted=True,
                demoted=False,
                previous_rating=prev_rating,
                new_rating=new_rating,
                previous_tier=prev_tier,
                new_tier=new_tier,
                reason=f"Achieved {cls.PROMOTION_THRESHOLD} consecutive Accepted solutions.",
            )

        return ProgressionEvent(
            promoted=False,
            demoted=False,
            previous_rating=prev_rating,
            new_rating=prev_rating,
            previous_tier=prev_tier,
            new_tier=prev_tier,
            reason=(
                f"Accepted recorded ({state.consecutive_accepted}/"
                f"{cls.PROMOTION_THRESHOLD} toward promotion)."
            ),
        )

    @classmethod
    def record_exhausted(cls, state: ProgressionState) -> ProgressionEvent:
        """Process an EXHAUSTED session (retry budget exhausted). Demotes after threshold."""
        state.consecutive_failed += 1
        state.consecutive_accepted = 0

        prev_rating = state.current_rating_target
        prev_tier = state.current_tier

        if state.consecutive_failed >= cls.DEMOTION_THRESHOLD:
            new_rating = max(cls.MIN_RATING, prev_rating - cls.RATING_STEP)
            new_tier = get_tier_for_rating(new_rating)
            state.current_rating_target = new_rating
            state.current_tier = new_tier
            state.consecutive_failed = 0

            return ProgressionEvent(
                promoted=False,
                demoted=True,
                previous_rating=prev_rating,
                new_rating=new_rating,
                previous_tier=prev_tier,
                new_tier=new_tier,
                reason=f"Exhausted retry budget on {cls.DEMOTION_THRESHOLD} consecutive problems.",
            )

        return ProgressionEvent(
            promoted=False,
            demoted=False,
            previous_rating=prev_rating,
            new_rating=prev_rating,
            previous_tier=prev_tier,
            new_tier=prev_tier,
            reason=(
                f"Session exhausted ({state.consecutive_failed}/"
                f"{cls.DEMOTION_THRESHOLD} toward demotion)."
            ),
        )

    @classmethod
    def filter_candidates(
        cls,
        problemset: list[ProblemMetadata],
        solved_ids: set[str],
        target_rating: int,
        target_tier: CurriculumTier,
        cooldown_ids: set[str] | None = None,
    ) -> list[ProblemMetadata]:
        """Filter problem catalog according to curriculum constraints and user history."""
        cooldown = cooldown_ids or set()
        low_bound, high_bound = TIER_BOUNDS[target_tier]

        # Primary filter: Exact target rating, unsolved, not in cooldown
        primary: list[ProblemMetadata] = []
        # Secondary fallback: Within tier rating bounds
        tier_fallback: list[ProblemMetadata] = []

        for p in problemset:
            if p.external_id in solved_ids or p.external_id in cooldown:
                continue
            if p.numeric_rating == target_rating:
                primary.append(p)
            elif p.numeric_rating is not None and low_bound <= p.numeric_rating <= high_bound:
                tier_fallback.append(p)

        return primary if primary else tier_fallback
