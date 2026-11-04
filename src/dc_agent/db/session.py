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

        connect_args = {}
        if url.startswith("sqlite"):
            connect_args = {"check_same_thread": False}

        _engine = create_engine(url, connect_args=connect_args)

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

    return _engine


def init_db(database_url: str | None = None) -> None:
    """Idempotently initialize all database tables."""
    engine = get_engine(database_url)
    Base.metadata.create_all(bind=engine)
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
