"""Tests for sandbox security hardening, environment sanitization, and fallback policies."""

from unittest.mock import patch

import pytest

from dc_agent.adapters.base import TestCase
from dc_agent.config import SandboxBackendType, Settings
from dc_agent.executor.base import LocalExecutionVerdict
from dc_agent.executor.sandbox import InsecureSandboxFallbackError, Sandbox
from dc_agent.executor.subprocess_runner import SubprocessExecutionSandbox


def test_docker_unavailable_without_fallback_raises_error() -> None:
    """CRITICAL: If Docker is configured and unavailable, execution must fail unless insecure fallback is explicitly allowed."""
    settings = Settings(
        SANDBOX_BACKEND=SandboxBackendType.DOCKER,
        ALLOW_INSECURE_FALLBACK=False,
    )
    sandbox = Sandbox(settings=settings)

    with (
        patch.object(sandbox._docker_runner, "is_available", return_value=False),
        pytest.raises(InsecureSandboxFallbackError, match="Docker daemon is unreachable"),
    ):
        sandbox.run_tests("print('hello')", [TestCase(input_data="", expected_output="hello")])


def test_docker_unavailable_with_fallback_allowed_warns_and_runs() -> None:
    """When allow_insecure_fallback=True, falling back to subprocess is permitted with explicit warning."""
    settings = Settings(
        SANDBOX_BACKEND=SandboxBackendType.DOCKER,
        ALLOW_INSECURE_FALLBACK=True,
    )
    sandbox = Sandbox(settings=settings)

    with (
        patch.object(sandbox._docker_runner, "is_available", return_value=False),
        patch.object(sandbox._subprocess_runner, "run_tests") as mock_subproc,
    ):
        sandbox.run_tests("print('hello')", [TestCase(input_data="", expected_output="hello")])
        mock_subproc.assert_called_once()


def test_subprocess_sanitized_environment_purges_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """CRITICAL: Subprocess environment must NEVER leak API keys, tokens, or credentials."""
    monkeypatch.setenv("GEMINI_API_KEY", "super_secret_gemini_key_12345")
    monkeypatch.setenv("OPENAI_API_KEY", "secret_openai_key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret_aws_token")
    monkeypatch.setenv("DATABASE_PASSWORD", "secret_db_pass")

    runner = SubprocessExecutionSandbox()
    clean_env = runner._build_sanitized_env()

    # None of these secret variables must exist in clean_env
    assert "GEMINI_API_KEY" not in clean_env
    assert "OPENAI_API_KEY" not in clean_env
    assert "AWS_SECRET_ACCESS_KEY" not in clean_env
    assert "DATABASE_PASSWORD" not in clean_env

    # General check: No sensitive substrings
    forbidden = ("KEY", "SECRET", "TOKEN", "AUTH", "PASS", "GEMINI", "OPENAI")
    for key in clean_env:
        assert not any(sub in key.upper() for sub in forbidden)


def test_subprocess_timeout_terminates_process() -> None:
    """Ensure infinite loop is terminated by wall-clock timeout."""
    runner = SubprocessExecutionSandbox()
    code = "import time\nwhile True:\n    pass\n"

    tc = TestCase(input_data="", expected_output="done", is_verified=True)
    summary = runner.run_tests(code, [tc], timeout_seconds=1.0)

    assert summary.overall_verdict == LocalExecutionVerdict.TIMEOUT
    assert summary.results[0].verdict == LocalExecutionVerdict.TIMEOUT


def test_subprocess_output_truncation() -> None:
    """Ensure huge stdout is truncated to prevent buffer memory bombs."""
    runner = SubprocessExecutionSandbox()
    # Print 200 KB of text
    code = "print('X' * 200000)"

    tc = TestCase(input_data="", expected_output="X", is_verified=False)
    summary = runner.run_tests(code, [tc], timeout_seconds=2.0)

    actual = summary.results[0].actual_output
    assert actual is not None
    assert len(actual.encode("utf-8")) <= 65536  # Max 64 KB limit


def test_malicious_code_blocked_by_ast_sanitizer() -> None:
    """Verify various malicious code attacks are intercepted by AST sanitizer."""
    runner = SubprocessExecutionSandbox()
    tc = TestCase(input_data="", expected_output="")

    malicious_snippets = [
        "import os\nos.system('calc')",
        "import subprocess\nsubprocess.run(['dir'])",
        "import shutil\nshutil.rmtree('/')",
        "import socket\ns = socket.socket()",
        "eval('__import__(\"os\").getcwd()')",
        "open('/etc/passwd', 'r')",
        "exec('print(1)')",
    ]

    for snippet in malicious_snippets:
        summary = runner.run_tests(snippet, [tc])
        assert summary.overall_verdict == LocalExecutionVerdict.SAFETY_VIOLATION
        assert summary.results[0].verdict == LocalExecutionVerdict.SAFETY_VIOLATION


def test_sandbox_security_capabilities_report() -> None:
    """Ensure capability report accurately reflects isolation status."""
    settings = Settings(
        SANDBOX_BACKEND=SandboxBackendType.DOCKER,
        ALLOW_INSECURE_FALLBACK=False,
    )
    sandbox = Sandbox(settings=settings)
    caps = sandbox.get_security_capabilities()

    assert "active_backend" in caps
    assert "docker_available" in caps
    assert "allow_insecure_fallback" in caps
    assert "isolation_summary" in caps
    assert caps["memory_limit_mb"] == 256
    assert caps["timeout_seconds"] == 2.0
