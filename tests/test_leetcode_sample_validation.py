"""Tests for LeetCode sample validation, unverified test handling, and harness wrapping."""

from dc_agent.adapters.base import TestCase
from dc_agent.adapters.leetcode import LeetCodeAdapter
from dc_agent.core.harness import wrap_leetcode_code
from dc_agent.executor.base import LocalExecutionVerdict
from dc_agent.executor.subprocess_runner import SubprocessExecutionSandbox


def test_leetcode_html_output_extraction() -> None:
    """Ensure expected outputs are correctly extracted from LeetCode HTML content."""
    html_content = """
    <p>Given an array of integers <code>nums</code> and an integer <code>target</code>...</p>
    <p><strong class="example">Example 1:</strong></p>
    <pre>
    <strong>Input:</strong> nums = [2,7,11,15], target = 9
    <strong>Output:</strong> [0,1]
    <strong>Explanation:</strong> Because nums[0] + nums[1] == 9, we return [0, 1].
    </pre>
    <p><strong class="example">Example 2:</strong></p>
    <pre>
    <strong>Input:</strong> nums = [3,2,4], target = 6
    <strong>Output:</strong> [1,2]
    </pre>
    """
    outputs = LeetCodeAdapter.extract_expected_outputs_from_content(html_content)
    assert len(outputs) == 2
    assert outputs[0] == "[0,1]"
    assert outputs[1] == "[1,2]"


def test_unverified_sample_test_never_claims_pass() -> None:
    """CRITICAL: If a sample test case lacks verified expected output, execution must return UNVERIFIED, never PASS."""
    sandbox = SubprocessExecutionSandbox()

    solution_code = """
def solve():
    try:
        line = input().strip()
    except EOFError:
        line = ""
    print("Computed result: " + line)

if __name__ == '__main__':
    solve()
"""
    # Sample test with missing expected output (input-only)
    unverified_tc = TestCase(
        input_data="SampleInputData\n",
        expected_output=None,
        is_sample=True,
        is_verified=False,
    )

    summary = sandbox.run_tests(solution_code, [unverified_tc])

    # The test ran without error, BUT must be marked UNVERIFIED, NOT PASS!
    assert summary.results[0].verdict == LocalExecutionVerdict.UNVERIFIED
    assert summary.overall_verdict == LocalExecutionVerdict.UNVERIFIED
    assert summary.passed_tests == 0
    assert summary.unverified_tests == 1


def test_verified_test_checks_output_equality() -> None:
    """When verified expected output is provided, test runner checks equality strictly."""
    sandbox = SubprocessExecutionSandbox()

    solution_code = "print(42)"

    # Matching test
    tc_pass = TestCase(input_data="", expected_output="42", is_verified=True)
    summary_pass = sandbox.run_tests(solution_code, [tc_pass])
    assert summary_pass.results[0].verdict == LocalExecutionVerdict.PASS
    assert summary_pass.overall_verdict == LocalExecutionVerdict.PASS

    # Mismatching test
    tc_fail = TestCase(input_data="", expected_output="100", is_verified=True)
    summary_fail = sandbox.run_tests(solution_code, [tc_fail])
    assert summary_fail.results[0].verdict == LocalExecutionVerdict.FAIL
    assert summary_fail.overall_verdict == LocalExecutionVerdict.FAIL


def test_wrap_leetcode_solution_preserves_class() -> None:
    """Ensure wrap_leetcode_code preserves class Solution and adds stdin driver."""
    solution_code = """class Solution:
    def twoSum(self, nums: list[int], target: int) -> list[int]:
        lookup = {}
        for i, n in enumerate(nums):
            if target - n in lookup:
                return [lookup[target - n], i]
            lookup[n] = i
        return []
"""
    wrapped = wrap_leetcode_code(solution_code)

    assert "class Solution:" in wrapped
    assert "def twoSum(" in wrapped
    assert "if __name__ == '__main__':" in wrapped
    assert "sol = Solution()" in wrapped

    # Running with input
    sandbox = SubprocessExecutionSandbox()
    tc = TestCase(
        input_data="[2, 7, 11, 15]\n9\n",
        expected_output="[0, 1]",
        is_verified=True,
    )
    summary = sandbox.run_tests(wrapped, [tc])
    assert summary.overall_verdict == LocalExecutionVerdict.PASS
