"""Unit tests for the Scheduler Service and duplicate run locking."""

from dc_agent.config import Settings
from dc_agent.db.models import SchedulerState
from dc_agent.db.session import get_db, init_db
from dc_agent.scheduler.service import SchedulerService, scheduled_daily_job


def test_scheduler_service_configuration() -> None:
    """Ensure SchedulerService registers cron job with correct time and timezone."""
    settings = Settings(TIMEZONE="Asia/Kolkata", DAILY_RUN_TIME="17:15")
    service = SchedulerService(settings=settings)
    service.configure_job()

    status = service.get_status()
    assert status["timezone"] == "Asia/Kolkata"
    assert status["scheduled_time"] == "17:15"


def test_scheduler_duplicate_run_lock() -> None:
    """Ensure concurrent scheduler executions are prevented via database state lock."""
    init_db()

    # Manually lock the job
    with get_db() as db:
        state = SchedulerState(job_name="daily_challenge", is_locked=True, status="RUNNING")
        db.add(state)
        db.commit()

        # Re-running scheduled_daily_job should immediately return without executing
        scheduled_daily_job()

        # State should still be locked
        assert state.is_locked is True
