"""Tests for DockerExecutionSandbox."""

from unittest.mock import MagicMock, patch

import pytest
from docker.errors import DockerException

from dc_agent.adapters.base import TestCase
from dc_agent.executor.base import LocalExecutionVerdict
from dc_agent.executor.docker_runner import DockerExecutionSandbox


def test_docker_is_available() -> None:
    sandbox = DockerExecutionSandbox()
    with patch("docker.from_env") as mock_from_env:
        mock_client = MagicMock()
        mock_from_env.return_value = mock_client

        # Ping succeeds
        mock_client.ping.return_value = True
        assert sandbox.is_available() is True

        # Ping throws
        mock_client.ping.side_effect = Exception("Docker not running")
        assert sandbox.is_available() is False


def test_docker_run_tests_not_available() -> None:
    sandbox = DockerExecutionSandbox()
    with (
        patch.object(sandbox, "is_available", return_value=False),
        pytest.raises(DockerException, match="Docker daemon is not running"),
    ):
        sandbox.run_tests(
            "print('hello')",
            [TestCase(input_data="1", expected_output="1")],
        )


def test_docker_ast_safety_violation() -> None:
    sandbox = DockerExecutionSandbox()
    with patch.object(sandbox, "is_available", return_value=True):
        sandbox._client = MagicMock()
        summary = sandbox.run_tests(
            "import os\nos.system('dir')",
            [TestCase(input_data="1", expected_output="1")],
        )
        assert summary.overall_verdict == LocalExecutionVerdict.SAFETY_VIOLATION
        assert summary.failed_tests == 1


def test_docker_run_tests_success_and_fail() -> None:
    sandbox = DockerExecutionSandbox()
    with patch.object(sandbox, "is_available", return_value=True):
        mock_client = MagicMock()
        sandbox._client = mock_client

        # Mock image get
        mock_client.images.get.return_value = MagicMock()

        # Mock container
        mock_container = MagicMock()
        mock_client.containers.create.return_value = mock_container
        mock_container.wait.return_value = {"StatusCode": 0}
        mock_container.logs.side_effect = [
            b"42\n",  # stdout
            b"",  # stderr
        ]

        summary = sandbox.run_tests(
            "print(42)",
            [TestCase(input_data="", expected_output="42")],
        )
        assert summary.overall_verdict == LocalExecutionVerdict.PASS
        assert summary.passed_tests == 1
        mock_container.remove.assert_called()


def test_docker_run_tests_mismatch() -> None:
    sandbox = DockerExecutionSandbox()
    with patch.object(sandbox, "is_available", return_value=True):
        mock_client = MagicMock()
        sandbox._client = mock_client

        mock_container = MagicMock()
        mock_client.containers.create.return_value = mock_container
        mock_container.wait.return_value = {"StatusCode": 0}
        mock_container.logs.side_effect = [
            b"100\n",
            b"",
        ]

        summary = sandbox.run_tests(
            "print(100)",
            [TestCase(input_data="", expected_output="42")],
        )
        assert summary.overall_verdict == LocalExecutionVerdict.FAIL
        assert summary.failed_tests == 1


def test_docker_run_tests_oom_exit_137() -> None:
    sandbox = DockerExecutionSandbox()
    with patch.object(sandbox, "is_available", return_value=True):
        mock_client = MagicMock()
        sandbox._client = mock_client

        mock_container = MagicMock()
        mock_client.containers.create.return_value = mock_container
        mock_container.wait.return_value = {"StatusCode": 137}
        mock_container.logs.side_effect = [
            b"",
            b"Killed\n",
        ]

        summary = sandbox.run_tests(
            "print('oom')",
            [TestCase(input_data="", expected_output="42")],
        )
        assert summary.overall_verdict == LocalExecutionVerdict.MEMORY_LIMIT_EXCEEDED


def test_docker_run_tests_timeout() -> None:
    sandbox = DockerExecutionSandbox()
    with patch.object(sandbox, "is_available", return_value=True):
        mock_client = MagicMock()
        sandbox._client = mock_client

        mock_container = MagicMock()
        mock_client.containers.create.return_value = mock_container
        mock_container.wait.side_effect = Exception("Read timed out")

        summary = sandbox.run_tests(
            "while True: pass",
            [TestCase(input_data="", expected_output="42")],
        )
        assert summary.overall_verdict == LocalExecutionVerdict.TIMEOUT
        mock_container.kill.assert_called()
