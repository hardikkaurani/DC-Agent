"""Unit tests for SQLAlchemy 2.0 database entities and constraints."""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from dc_agent.db.models import (
    Base,
    CurriculumProgress,
    DailySession,
    Problem,
    UserAccount,
)


def test_database_schema_creation_and_relationships() -> None:
    """Ensure all declarative models instantiate and relationships persist cleanly in SQLite."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        # Create User Account
        user = UserAccount(platform="leetcode", handle="hardikkaurani_")
        session.add(user)
        session.flush()

        # Create Progress
        progress = CurriculumProgress(
            account_id=user.id,
            current_tier=1,
            current_rating_target=800,
            consecutive_accepted=0,
            consecutive_failed=0,
        )
        session.add(progress)

        # Create Problem
        problem = Problem(
            platform="leetcode",
            external_id="1",
            title="Two Sum",
            slug="two-sum",
            url="https://leetcode.com/problems/two-sum/",
            difficulty_label="Easy",
            numeric_rating=None,
            tags=["array", "hash-table"],
        )
        session.add(problem)
        session.flush()

        # Create Daily Session
        daily_session = DailySession(
            platform="leetcode",
            account_id=user.id,
            cycle_date="2026-11-04",
            problem_id=problem.id,
            status="IN_PROGRESS",
            retry_budget=3,
            retry_count=0,
        )
        session.add(daily_session)
        session.commit()

        # Verify query and relationship resolution
        queried_session = (
            session.query(DailySession)
            .filter_by(platform="leetcode", cycle_date="2026-11-04")
            .one()
        )
        assert queried_session.problem.slug == "two-sum"
        assert queried_session.account.handle == "hardikkaurani_"
        assert queried_session.status == "IN_PROGRESS"
