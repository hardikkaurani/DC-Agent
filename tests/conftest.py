"""Pytest fixtures for DC-Agent test suite."""

from pathlib import Path

import pytest

from dc_agent.config import get_settings
from dc_agent.db.session import init_db


@pytest.fixture(autouse=True)
def test_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate database and configuration for every test."""
    test_db = tmp_path / "test_dc_agent.db"
    db_url = f"sqlite:///{test_db.resolve()}"

    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("GEMINI_API_KEY", "test_key_12345678")
    monkeypatch.setenv("WORKSPACE_DIR", str((tmp_path / "workspace").resolve()))
    monkeypatch.setenv("SANDBOX_BACKEND", "subprocess")
    monkeypatch.setenv("ALLOW_INSECURE_FALLBACK", "true")

    # Reload settings singleton to pick up test environment
    get_settings(reload=True)

    # Initialize all database tables
    init_db(db_url)
