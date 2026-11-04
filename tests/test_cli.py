"""Unit tests for Typer CLI."""

from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from dc_agent.cli.main import app

runner = CliRunner()


def test_cli_help() -> None:
    """Ensure root CLI help displays without error."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Daily Competitive Programming Assistant" in result.stdout


def test_cli_config() -> None:
    """Ensure config command displays configured account handles."""
    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0
    assert "hardikkaurani_" in result.stdout
    assert "hkvariant" in result.stdout


def test_cli_daily_unsupported_platform() -> None:
    """Ensure daily command rejects unsupported platform names."""
    result = runner.invoke(app, ["daily", "--platform", "unsupported"])
    assert result.exit_code == 1
    assert "Unsupported platform" in result.stdout


def test_cli_daily_supported_platforms() -> None:
    """Ensure daily command executes with supported platforms when orchestrator runs."""
    mock_run = AsyncMock(
        return_value={
            "status": "LOCAL_PASSED",
            "platform": "leetcode",
            "problem_title": "Two Sum",
            "problem_url": "https://leetcode.com/problems/two-sum/",
            "workspace_dir": "problem_workspace/sample",
            "tests_passed": 1,
            "total_tests": 1,
            "corrections": 0,
        }
    )

    with patch("dc_agent.cli.main.DailyOrchestrator.run_daily", mock_run):
        res_lc = runner.invoke(app, ["daily", "--platform", "leetcode"])
        assert res_lc.exit_code == 0
        assert "Two Sum" in res_lc.stdout

        mock_run.return_value["platform"] = "codeforces"
        mock_run.return_value["problem_title"] = "Watermelon"
        res_cf = runner.invoke(app, ["daily", "--platform", "codeforces"])
        assert res_cf.exit_code == 0
        assert "Watermelon" in res_cf.stdout
