"""Unit tests for the AST Security Sanitizer."""

import pytest

from dc_agent.executor.ast_sanitizer import (
    SecurityViolationError,
    validate_code_safety,
)


def test_valid_algorithmic_solution_passes() -> None:
    """Ensure standard algorithmic code passes AST security validation."""
    valid_code = """
def twoSum(nums: list[int], target: int) -> list[int]:
    lookup = {}
    for i, num in enumerate(nums):
        diff = target - num
        if diff in lookup:
            return [lookup[diff], i]
        lookup[num] = i
    return []
"""
    # Should not raise any error
    validate_code_safety(valid_code)


@pytest.mark.parametrize(
    "forbidden_import",
    [
        "import os",
        "import sys",
        "import subprocess",
        "import socket",
        "import shutil",
        "from os import path",
        "from subprocess import Popen",
        "import urllib.request",
        "import requests",
        "import ctypes",
    ],
)
def test_forbidden_imports_raise_security_violation(forbidden_import: str) -> None:
    """Ensure attempts to import forbidden modules are rejected."""
    malicious_code = f"""
{forbidden_import}

def solve():
    return 42
"""
    with pytest.raises(SecurityViolationError) as exc_info:
        validate_code_safety(malicious_code)
    assert "FORBIDDEN_IMPORT" in str(exc_info.value)


@pytest.mark.parametrize(
    "dangerous_call",
    [
        "eval('1 + 1')",
        "exec('print(1)')",
        "compile('x = 1', '<string>', 'exec')",
        "__import__('os')",
        "globals()",
        "locals()",
    ],
)
def test_dangerous_function_calls_rejected(dangerous_call: str) -> None:
    """Ensure calls to dangerous builtins are intercepted."""
    malicious_code = f"""
def solve():
    {dangerous_call}
    return 1
"""
    with pytest.raises(SecurityViolationError) as exc_info:
        validate_code_safety(malicious_code)
    assert "FORBIDDEN_CALL" in str(exc_info.value)


def test_forbidden_attribute_access_rejected() -> None:
    """Ensure access to internal dunder attributes is intercepted."""
    malicious_code = """
def exploit():
    return ().__class__.__base__.__subclasses__()
"""
    with pytest.raises(SecurityViolationError) as exc_info:
        validate_code_safety(malicious_code)
    assert "FORBIDDEN_ATTRIBUTE" in str(exc_info.value)
