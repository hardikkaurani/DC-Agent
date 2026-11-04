"""Tests for canonical Asia/Kolkata timezone handling, year/leap boundaries, and daily idempotency."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.exc import IntegrityError

from dc_agent.config import Settings, get_canonical_cycle_date
from dc_agent.core.orchestrator import DailyOrchestrator
from dc_agent.db.repository import Repository
from dc_agent.db.session import get_db, init_db


def test_canonical_cycle_date_around_midnight_ist() -> None:
    """CRITICAL: At 1:00 AM IST, UTC is 19:30 on the previous calendar day.

    The cycle date must be the local IST date, NEVER the UTC date.
    """
    settings = Settings(TIMEZONE="Asia/Kolkata")

    # 1:00 AM IST on November 5, 2026 = 19:30 UTC on November 4, 2026
    dt_utc = datetime(2026, 11, 4, 19, 30, 0, tzinfo=UTC)
    cycle_date = get_canonical_cycle_date(settings=settings, now_dt=dt_utc)

    # In IST (+05:30), it is 01:00 AM on 2026-11-05
    assert cycle_date == "2026-11-05"


def test_canonical_cycle_date_year_boundary_rollover() -> None:
    """Test December 31 -> January 1 transition in Asia/Kolkata timezone."""
    settings = Settings(TIMEZONE="Asia/Kolkata")
    tz = ZoneInfo("Asia/Kolkata")

    # Dec 31, 2026 at 23:59:50 IST
    dec_31 = datetime(2026, 12, 31, 23, 59, 50, tzinfo=tz)
    assert get_canonical_cycle_date(settings=settings, now_dt=dec_31) == "2026-12-31"

    # Jan 1, 2027 at 00:00:10 IST
    jan_1 = datetime(2027, 1, 1, 0, 0, 10, tzinfo=tz)
    assert get_canonical_cycle_date(settings=settings, now_dt=jan_1) == "2027-01-01"


def test_canonical_cycle_date_leap_year() -> None:
    """Test February 28 -> February 29 transition in a leap year (2024)."""
    settings = Settings(TIMEZONE="Asia/Kolkata")
    tz = ZoneInfo("Asia/Kolkata")

    feb_28 = datetime(2024, 2, 28, 23, 50, 0, tzinfo=tz)
    feb_29 = datetime(2024, 2, 29, 0, 10, 0, tzinfo=tz)
    mar_1 = datetime(2024, 3, 1, 0, 10, 0, tzinfo=tz)

    assert get_canonical_cycle_date(settings=settings, now_dt=feb_28) == "2024-02-28"
    assert get_canonical_cycle_date(settings=settings, now_dt=feb_29) == "2024-02-29"
    assert get_canonical_cycle_date(settings=settings, now_dt=mar_1) == "2024-03-01"


def test_persistent_alternation_across_year_rollover() -> None:
    """Test that platform alternation persists across year rollover without day-of-year parity bugs."""
    settings = Settings(DATABASE_URL="sqlite:///:memory:")
    init_db(settings.database_url)
    orchestrator = DailyOrchestrator(settings=settings)

    with get_db() as db:
        repo = Repository(db)
        # Dec 31: solved LeetCode
        p_dec = repo.create_or_get_problem("leetcode", "1", "Prob", "prob", "http://lc/1", "Easy")
        repo.create_daily_session("leetcode", "user", "2026-12-31", p_dec)
        db.commit()

        # Jan 1: must alternate to Codeforces (regardless of Day 1 parity!)
        assert (
            orchestrator.determine_today_platform(cycle_date="2027-01-01", repo=repo)
            == "codeforces"
        )


def test_single_session_per_calendar_day_enforced_by_db() -> None:
    """Ensure database rejects multiple platform sessions on the same calendar day."""
    settings = Settings(DATABASE_URL="sqlite:///:memory:")
    init_db(settings.database_url)

    with get_db() as db:
        repo = Repository(db)
        p1 = repo.create_or_get_problem("leetcode", "1", "Prob 1", "p1", "http://lc/1", "Easy")
        repo.create_daily_session("leetcode", "user", "2026-11-04", p1)
        db.commit()

        # Attempt to insert a second session on the same cycle date (even on a different platform)
        p2 = repo.create_or_get_problem("codeforces", "2", "Prob 2", "p2", "http://cf/2", "800")
        with pytest.raises(IntegrityError):
            repo.create_daily_session("codeforces", "user", "2026-11-04", p2)
        db.rollback()
