"""SQLAlchemy 2.0 declarative database entities for DC-Agent."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base declarative class for all DC-Agent ORM entities."""

    pass


class UserAccount(Base):
    """Stores configured platform accounts and identities."""

    __tablename__ = "user_account"
    __table_args__ = (UniqueConstraint("platform", "handle", name="uq_user_platform_handle"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    handle: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    progress: Mapped["CurriculumProgress | None"] = relationship(
        "CurriculumProgress", back_populates="account", uselist=False, cascade="all, delete-orphan"
    )
    sessions: Mapped[list["DailySession"]] = relationship("DailySession", back_populates="account")


# Backwards compatible alias
PlatformAccount = UserAccount


class CurriculumProgress(Base):
    """Tracks persistent Codeforces difficulty curriculum rating and streaks."""

    __tablename__ = "curriculum_progress"
    __table_args__ = (
        CheckConstraint("current_tier BETWEEN 1 AND 6", name="ck_curriculum_tier_range"),
        CheckConstraint("current_rating_target >= 800", name="ck_curriculum_min_rating"),
        UniqueConstraint("account_id", name="uq_curriculum_account"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user_account.id", ondelete="CASCADE"), nullable=False
    )
    current_tier: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    current_rating_target: Mapped[int] = mapped_column(Integer, default=800, nullable=False)
    consecutive_accepted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    consecutive_failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    account: Mapped["UserAccount"] = relationship("UserAccount", back_populates="progress")


# Backwards compatible alias
RatingProgress = CurriculumProgress


class Problem(Base):
    """Represents a competitive programming challenge catalog entry."""

    __tablename__ = "problem"
    __table_args__ = (
        UniqueConstraint("platform", "external_id", name="uq_problem_platform_external_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    slug: Mapped[str] = mapped_column(String(256), nullable=False)
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    difficulty_label: Mapped[str] = mapped_column(String(32), nullable=False)
    numeric_rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    metadata_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)

    # Relationships
    test_cases: Mapped[list["LocalTestCase"]] = relationship(
        "LocalTestCase", back_populates="problem", cascade="all, delete-orphan"
    )
    sessions: Mapped[list["DailySession"]] = relationship("DailySession", back_populates="problem")


class LocalTestCase(Base):
    """Stores input/expected output pairs used for local verification."""

    __tablename__ = "local_test_case"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    problem_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("problem.id", ondelete="CASCADE"), nullable=False
    )
    test_index: Mapped[int] = mapped_column(Integer, nullable=False)
    input_data: Mapped[str] = mapped_column(Text, nullable=False)
    expected_output: Mapped[str] = mapped_column(Text, nullable=False)
    is_sample: Mapped[bool] = mapped_column(default=True, nullable=False)
    source: Mapped[str] = mapped_column(String(32), default="PLATFORM_API", nullable=False)

    # Relationships
    problem: Mapped["Problem"] = relationship("Problem", back_populates="test_cases")


# Backwards compatible alias
TestExecution = LocalTestCase


class DailySession(Base):
    """Represents a daily problem-solving challenge session.

    Enforces strict daily idempotency via UNIQUE(platform, cycle_date).
    """

    __tablename__ = "daily_session"
    __table_args__ = (UniqueConstraint("platform", "cycle_date", name="uq_session_platform_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    account_id: Mapped[int] = mapped_column(Integer, ForeignKey("user_account.id"), nullable=False)
    cycle_date: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD (UTC)
    problem_id: Mapped[int] = mapped_column(Integer, ForeignKey("problem.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="PENDING", nullable=False)
    retry_budget: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    selected_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships
    account: Mapped["UserAccount"] = relationship("UserAccount", back_populates="sessions")
    problem: Mapped["Problem"] = relationship("Problem", back_populates="sessions")
    attempts: Mapped[list["SolutionAttempt"]] = relationship(
        "SolutionAttempt", back_populates="session", cascade="all, delete-orphan"
    )


class SolutionAttempt(Base):
    """Records an attempt to solve a problem locally or on platform."""

    __tablename__ = "solution_attempt"
    __table_args__ = (
        UniqueConstraint("session_id", "attempt_number", name="uq_attempt_session_number"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("daily_session.id", ondelete="CASCADE"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_code: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(String(32), default="python3", nullable=False)
    local_verdict: Mapped[str] = mapped_column(String(32), nullable=False)
    execution_metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    platform_verdict: Mapped[str] = mapped_column(
        String(32), default="PENDING_MANUAL", nullable=False
    )
    platform_submission_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)

    # Relationships
    session: Mapped["DailySession"] = relationship("DailySession", back_populates="attempts")


# Backwards compatible alias
ProblemAttempt = SolutionAttempt
Solution = SolutionAttempt


class SchedulerState(Base):
    """Tracks background scheduler execution state and distributed locking."""

    __tablename__ = "scheduler_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="IDLE", nullable=False)
    is_locked: Mapped[bool] = mapped_column(default=False, nullable=False)
    lock_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )


class ApplicationSettings(Base):
    """Key-value persistence for dynamic runtime configuration."""

    __tablename__ = "application_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )


class ProviderUsage(Base):
    """Tracks token consumption and API call latencies for AI providers."""

    __tablename__ = "provider_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider_name: Mapped[str] = mapped_column(String(32), nullable=False)
    model_name: Mapped[str] = mapped_column(String(64), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)  # solve, review, fix
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    success: Mapped[bool] = mapped_column(default=True, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)
