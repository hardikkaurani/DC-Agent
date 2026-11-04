"""Tests for PostgreSQL connection string normalization and driver configuration."""

from unittest.mock import MagicMock, patch

from dc_agent.config import Settings
from dc_agent.db.session import get_engine


def test_normalize_database_url_postgres_prefix() -> None:
    """Ensure postgres:// prefix is normalized to postgresql+psycopg://."""
    s = Settings(DATABASE_URL="postgres://user:pass@localhost:5432/dc_db")
    assert s.database_url == "postgresql+psycopg://user:pass@localhost:5432/dc_db"


def test_normalize_database_url_postgresql_prefix() -> None:
    """Ensure bare postgresql:// prefix is normalized to postgresql+psycopg://."""
    s = Settings(DATABASE_URL="postgresql://user:pass@localhost:5432/dc_db")
    assert s.database_url == "postgresql+psycopg://user:pass@localhost:5432/dc_db"


def test_normalize_database_url_preserves_explicit_driver() -> None:
    """Ensure explicit postgresql+driver:// prefix is preserved."""
    s = Settings(DATABASE_URL="postgresql+asyncpg://user:pass@localhost:5432/dc_db")
    assert s.database_url == "postgresql+asyncpg://user:pass@localhost:5432/dc_db"


def test_normalize_database_url_sqlite_untouched() -> None:
    """Ensure sqlite URL is unmodified."""
    s = Settings(DATABASE_URL="sqlite:///test.db")
    assert s.database_url == "sqlite:///test.db"


def test_get_engine_postgresql_configuration() -> None:
    """Verify get_engine applies pool_pre_ping and pool_recycle for PostgreSQL."""
    with (
        patch("dc_agent.db.session.create_engine") as mock_create_engine,
        patch("dc_agent.db.session.Base.metadata.create_all"),
    ):
        mock_engine = MagicMock()
        mock_create_engine.return_value = mock_engine

        engine = get_engine("postgresql://app:secret@db.internal:5432/prod")

        mock_create_engine.assert_called_once_with(
            "postgresql+psycopg://app:secret@db.internal:5432/prod",
            pool_pre_ping=True,
            pool_recycle=300,
        )
        assert engine == mock_engine
