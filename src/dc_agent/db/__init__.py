"""Database package for DC-Agent."""

from dc_agent.db.models import (
    ApplicationSettings,
    Base,
    CurriculumProgress,
    DailySession,
    LocalTestCase,
    PlatformAccount,
    Problem,
    ProblemAttempt,
    ProviderUsage,
    RatingProgress,
    SchedulerState,
    Solution,
    SolutionAttempt,
    TestExecution,
    UserAccount,
)
from dc_agent.db.repository import Repository
from dc_agent.db.session import get_db, get_engine, init_db

__all__ = [
    "ApplicationSettings",
    "Base",
    "CurriculumProgress",
    "DailySession",
    "LocalTestCase",
    "PlatformAccount",
    "Problem",
    "ProblemAttempt",
    "ProviderUsage",
    "RatingProgress",
    "Repository",
    "SchedulerState",
    "Solution",
    "SolutionAttempt",
    "TestExecution",
    "UserAccount",
    "get_db",
    "get_engine",
    "init_db",
]
