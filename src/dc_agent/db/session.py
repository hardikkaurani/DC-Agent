"""Database engine and session management with SQLite WAL mode."""

import logging
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from dc_agent.config import get_settings
from dc_agent.db.models import Base

logger = logging.getLogger(__name__)

_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def get_engine(database_url: str | None = None) -> Engine:
    """Create or return the global SQLAlchemy engine with WAL mode configured."""
    global _engine, _SessionFactory
    if _engine is None or database_url is not None:
        url = database_url or get_settings().database_url

        # Normalize postgres driver to psycopg
        if url.startswith("postgres://"):
            url = "postgresql+psycopg://" + url[len("postgres://") :]
        elif url.startswith("postgresql://") and not url.startswith("postgresql+"):
            url = "postgresql+psycopg://" + url[len("postgresql://") :]

        engine_kwargs: dict[str, Any] = {}
        if url.startswith("sqlite"):
            engine_kwargs["connect_args"] = {"check_same_thread": False}
        elif url.startswith("postgresql"):
            engine_kwargs["pool_pre_ping"] = True
            engine_kwargs["pool_recycle"] = 300

        _engine = create_engine(url, **engine_kwargs)

        # Enforce WAL mode and foreign key constraints on SQLite connections
        if url.startswith("sqlite"):

            @event.listens_for(_engine, "connect")
            def set_sqlite_pragma(dbapi_connection: Any, connection_record: Any) -> None:
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA busy_timeout=5000")
                cursor.close()

        _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False)
        Base.metadata.create_all(bind=_engine)
        _upgrade_sqlite_schema(_engine)

    return _engine


def _upgrade_sqlite_schema(engine: Engine) -> None:
    """Safely add newly introduced columns to existing SQLite tables if missing."""
    if not str(engine.url).startswith("sqlite"):
        return
    try:
        with engine.connect() as conn:
            cols = [
                r[1] for r in conn.exec_driver_sql("PRAGMA table_info(daily_session)").fetchall()
            ]
            if not cols:
                return
            new_cols = {
                "submission_id": "VARCHAR(128)",
                "judge_verdict": "VARCHAR(64)",
                "calendar_verified_at": "DATETIME",
                "error_message": "TEXT",
                "execution_log": "TEXT DEFAULT '[]'",
            }
            for col_name, col_type in new_cols.items():
                if col_name not in cols:
                    try:
                        conn.exec_driver_sql(
                            f"ALTER TABLE daily_session ADD COLUMN {col_name} {col_type}"
                        )
                        conn.commit()
                    except Exception as e:
                        logger.debug(
                            "Column %s could not be added or already exists: %s", col_name, e
                        )
    except Exception as e:
        logger.debug("SQLite schema upgrade check skipped: %s", e)


def init_db(database_url: str | None = None) -> None:
    """Idempotently initialize all database tables."""
    engine = get_engine(database_url)
    Base.metadata.create_all(bind=engine)
    _upgrade_sqlite_schema(engine)
    logger.info("Database initialized successfully at %s", engine.url)


@contextmanager
def get_db(database_url: str | None = None) -> Generator[Session, None, None]:
    """Provide a transactional database session context."""
    get_engine(database_url)
    assert _SessionFactory is not None
    session: Session = _SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
