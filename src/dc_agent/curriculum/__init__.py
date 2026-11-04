"""Curriculum engine package."""

from dc_agent.curriculum.engine import CurriculumEngine, get_tier_for_rating
from dc_agent.curriculum.models import (
    TIER_BOUNDS,
    TIER_TAGS,
    CurriculumTier,
    ProgressionEvent,
    ProgressionState,
)

__all__ = [
    "TIER_BOUNDS",
    "TIER_TAGS",
    "CurriculumEngine",
    "CurriculumTier",
    "ProgressionEvent",
    "ProgressionState",
    "get_tier_for_rating",
]
