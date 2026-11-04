"""Unit tests for the FastAPI Local Dashboard."""

from fastapi.testclient import TestClient

from dc_agent.db.repository import Repository
from dc_agent.db.session import get_db, init_db
from dc_agent.web.app import app

client = TestClient(app)


def test_dashboard_homepage() -> None:
    """Ensure GET / returns valid HTML dashboard."""
    init_db()
    response = client.get("/")
    assert response.status_code == 200
    assert "DC-Agent" in response.text
    assert "Today's Challenge" in response.text


def test_api_status_endpoint() -> None:
    """Ensure GET /api/status returns operational status and masks API keys."""
    response = client.get("/api/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ONLINE"
    assert "ai_provider" in data
    assert "timezone" in data
    # Ensure no raw secrets are present anywhere in the payload
    assert "api_key" not in data
    assert "GEMINI_API_KEY" not in data


def test_api_today_endpoint() -> None:
    """Ensure GET /api/today returns valid structure."""
    init_db()
    response = client.get("/api/today")
    assert response.status_code == 200
    data = response.json()
    assert "active" in data
    assert "date" in data


def test_api_curriculum_endpoint() -> None:
    """Ensure GET /api/curriculum returns Codeforces progress metrics."""
    init_db()
    with get_db() as db:
        repo = Repository(db)
        repo.get_or_create_user("codeforces", "hkvariant")

    response = client.get("/api/curriculum")
    assert response.status_code == 200
    data = response.json()
    assert data["handle"] == "hkvariant"
    assert "current_tier" in data
    assert "target_rating" in data
