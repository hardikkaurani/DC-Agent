"""Persistent daily scheduler using APScheduler with Asia/Kolkata timezone support."""

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from dc_agent.config import Settings, get_settings
from dc_agent.core.orchestrator import DailyOrchestrator
from dc_agent.db.models import SchedulerState
from dc_agent.db.session import get_db

logger = logging.getLogger(__name__)


def scheduled_daily_job() -> None:
    """Entrypoint invoked by the scheduler when the daily trigger fires."""
    logger.info("Scheduler triggered scheduled daily job...")
    settings = get_settings()

    # Prevent concurrent execution via database state lock
    with get_db() as db:
        stmt = select(SchedulerState).where(SchedulerState.job_name == "daily_challenge")
        state = db.scalar(stmt)
        if not state:
            state = SchedulerState(job_name="daily_challenge", is_locked=False, status="IDLE")
            db.add(state)
            db.flush()

        if state.is_locked:
            logger.warning("Scheduled job is already running or locked. Skipping duplicate run.")
            return

        state.is_locked = True
        state.status = "RUNNING"
        state.last_run_at = datetime.now()
        db.commit()

    try:
        orchestrator = DailyOrchestrator(settings=settings)
        # Run async workflow in event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(orchestrator.run_daily())
        loop.close()
        logger.info("Scheduled daily job completed: %s", result.get("status"))
    except Exception as e:
        logger.error("Error executing scheduled daily job: %s", e)
    finally:
        with get_db() as db:
            stmt = select(SchedulerState).where(SchedulerState.job_name == "daily_challenge")
            state = db.scalar(stmt)
            if state:
                state.is_locked = False
                state.status = "IDLE"
                db.commit()


class SchedulerService:
    """Manages background scheduling of daily competitive programming tasks."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.tz = ZoneInfo(self.settings.timezone)
        self.scheduler = BackgroundScheduler(timezone=self.tz)

    def configure_job(self) -> None:
        """Parse time string (e.g. '17:15') and register the cron job with APScheduler."""
        time_parts = self.settings.daily_run_time.split(":")
        hour = int(time_parts[0])
        minute = int(time_parts[1]) if len(time_parts) > 1 else 0

        trigger = CronTrigger(
            hour=hour,
            minute=minute,
            timezone=self.tz,
        )

        self.scheduler.add_job(
            scheduled_daily_job,
            trigger=trigger,
            id="daily_challenge",
            name="DC-Agent Daily Challenge",
            replace_existing=True,
            misfire_grace_time=3600,  # 1 hour grace period for missed jobs
            coalesce=True,
        )
        logger.info(
            "Registered daily challenge job at %02d:%02d (%s)",
            hour,
            minute,
            self.settings.timezone,
        )

    def start(self) -> None:
        """Start the background scheduler."""
        if not self.scheduler.running:
            self.configure_job()
            self.scheduler.start()
            logger.info("Scheduler started successfully.")

    def shutdown(self) -> None:
        """Gracefully shut down the scheduler."""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped.")

    def get_status(self) -> dict[str, str | None]:
        """Return operational status and next scheduled execution time."""
        job = self.scheduler.get_job("daily_challenge")
        next_run_val = getattr(job, "next_run_time", None) if job else None
        next_run = next_run_val.isoformat() if next_run_val is not None else None

        return {
            "running": "ACTIVE" if self.scheduler.running else "STOPPED",
            "timezone": self.settings.timezone,
            "scheduled_time": self.settings.daily_run_time,
            "next_run_at": next_run,
        }
