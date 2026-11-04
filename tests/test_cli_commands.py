"""Comprehensive CLI commands test suite."""

from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from dc_agent.cli.main import app
from dc_agent.db.repository import Repository
from dc_agent.db.session import get_db

runner = CliRunner()


def test_cli_init() -> None:
    """Ensure init command completes and initializes tables."""
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert "DC-Agent Initialized Successfully" in result.stdout


def test_cli_doctor() -> None:
    """Ensure doctor command checks database, provider, and workspace."""
    with (
        patch(
            "dc_agent.providers.gemini.GeminiProvider.health_check", AsyncMock(return_value=True)
        ),
        patch(
            "dc_agent.adapters.codeforces.CodeforcesAdapter.fetch_user_info",
            AsyncMock(return_value={"rating": 1200, "rank": "pupil"}),
        ),
    ):
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0
        assert "Database Connection" in result.stdout
        assert "checks passed" in result.stdout.lower()


def test_cli_status_and_today() -> None:
    """Ensure status and today commands display session info."""
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "Status" in result.stdout

    res_today = runner.invoke(app, ["today"])
    assert res_today.exit_code == 0


def test_cli_leetcode_set_daily() -> None:
    """Ensure leetcode set-daily registers challenge."""
    result = runner.invoke(app, ["leetcode", "set-daily", "--slug", "two-sum"])
    assert result.exit_code == 0
    assert "LeetCode Daily Challenge successfully set" in result.stdout


def test_cli_codeforces_commands() -> None:
    """Ensure codeforces profile and progress commands run."""
    with patch(
        "dc_agent.adapters.codeforces.CodeforcesAdapter.fetch_user_info",
        AsyncMock(return_value={"handle": "hkvariant", "rating": 1400, "rank": "specialist"}),
    ):
        res_prof = runner.invoke(app, ["codeforces", "profile"])
        assert res_prof.exit_code == 0
        assert "hkvariant" in res_prof.stdout

    res_prog = runner.invoke(app, ["codeforces", "progress"])
    assert res_prog.exit_code == 0
    assert "Curriculum Progress" in res_prog.stdout


def test_cli_history_and_stats() -> None:
    """Ensure history and stats commands display valid tables."""
    res_hist = runner.invoke(app, ["history"])
    assert res_hist.exit_code == 0

    res_stats = runner.invoke(app, ["stats"])
    assert res_stats.exit_code == 0
    assert "Total Daily Challenges" in res_stats.stdout


def test_cli_reset_curriculum() -> None:
    """Ensure curriculum reset works."""
    result = runner.invoke(app, ["reset", "--curriculum"])
    assert result.exit_code == 0
    assert "curriculum reset to Tier 1" in result.stdout


def test_cli_record_verdict_and_verify() -> None:
    """Ensure verdict recording and verification workflow functions."""
    with get_db() as db:
        repo = Repository(db)
        prob = repo.create_or_get_problem(
            platform="codeforces",
            external_id="1890_A",
            title="Doremy's Paint 3",
            slug="1890-a",
            url="https://codeforces.com/problemset/problem/1890/A",
            difficulty_label="800",
        )
        session = repo.create_daily_session("codeforces", "hkvariant", "2026-11-04", prob)
        session_id = session.id
        db.commit()

    # Record Accepted
    res_rec = runner.invoke(
        app, ["record-verdict", "--verdict", "ACCEPTED", "--session-id", str(session_id)]
    )
    assert res_rec.exit_code == 0
    assert "ACCEPTED" in res_rec.stdout
