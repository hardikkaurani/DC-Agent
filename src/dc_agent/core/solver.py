"""Solution generation and independent review pipeline."""

import logging

from dc_agent.adapters.base import TestCase
from dc_agent.core.harness import wrap_leetcode_code
from dc_agent.providers.base import IAIProvider
from dc_agent.providers.schemas import GeneratedSolution, SolutionReview

logger = logging.getLogger(__name__)


class SolverService:
    """Manages AI solution generation, formatting, and independent review."""

    def __init__(self, provider: IAIProvider) -> None:
        self.provider = provider

    async def solve_problem(
        self,
        platform: str,
        title: str,
        description: str,
        constraints: str = "",
        sample_tests: list[TestCase] | None = None,
        method_signature: str | None = None,
    ) -> tuple[GeneratedSolution, SolutionReview, str]:
        """Generate solution, review it independently, and return executable code.

        Returns:
            Tuple of (GeneratedSolution, SolutionReview, executable_code)
        """
        clean_desc = (description or "").strip()
        placeholder_candidates = (
            title.strip(),
            f"{title} on {platform.upper()}",
            f"{title} on {platform.lower()}",
            f"{title} on {platform.title()}",
        )
        if not clean_desc or clean_desc in placeholder_candidates:
            raise ValueError(
                f"Problem statement is unavailable for '{title}' on {platform.upper()}. "
                "Cannot generate solution without official problem statement and constraints. "
                "Please provide the problem statement first."
            )

        logger.info(
            "Generating solution with %s (%s)...",
            self.provider.provider_name,
            self.provider.model_name,
        )

        formatted_tests: list[dict[str, str]] = []
        if sample_tests:
            for tc in sample_tests:
                formatted_tests.append(
                    {
                        "input": tc.input_data,
                        "expected_output": tc.expected_output or "",
                    }
                )

        # Step 1: Solution Generation
        solution = await self.provider.generate_solution(
            problem_title=title,
            problem_description=clean_desc,
            platform=platform,
            constraints=constraints,
            sample_tests=formatted_tests,
            method_signature=method_signature,
        )

        # Step 2: Independent Review
        logger.info("Performing independent code review...")
        review = await self.provider.review_solution(
            problem_title=title,
            problem_description=description,
            platform=platform,
            solution_code=solution.python_code,
        )

        # Step 3: Format code for execution
        executable_code = solution.python_code
        if platform.lower() == "leetcode":
            executable_code = wrap_leetcode_code(solution.python_code)

        return solution, review, executable_code
