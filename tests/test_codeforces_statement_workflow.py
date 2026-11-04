"""Tests for Codeforces problem statement workflow and refusal to solve with missing statements."""

from unittest.mock import AsyncMock, patch

import pytest

from dc_agent.adapters.base import Platform, ProblemMetadata
from dc_agent.config import SandboxBackendType, Settings
from dc_agent.core.orchestrator import DailyOrchestrator
from dc_agent.core.solver import SolverService
from dc_agent.db.repository import Repository
from dc_agent.db.session import get_db, init_db
from dc_agent.providers.base import IAIProvider


@pytest.mark.asyncio
async def test_solver_refuses_empty_or_title_only_statement() -> None:
    """CRITICAL: SolverService must refuse solution generation when problem statement is missing or title-only."""
    mock_provider = AsyncMock(spec=IAIProvider)
    solver = SolverService(provider=mock_provider)

    # 1. Empty statement
    with pytest.raises(ValueError, match="Problem statement is unavailable"):
        await solver.solve_problem(
            platform="codeforces",
            title="Watermelon",
            description="",
        )

    # 2. Title-only placeholder
    with pytest.raises(ValueError, match="Problem statement is unavailable"):
        await solver.solve_problem(
            platform="codeforces",
            title="Watermelon",
            description="Watermelon on CODEFORCES",
        )

    # Provider must NEVER have been called with missing statement
    mock_provider.generate_solution.assert_not_called()


@pytest.mark.asyncio
async def test_orchestrator_sets_statement_required_for_codeforces() -> None:
    """When a Codeforces problem is selected with API metadata only, status must be STATEMENT_REQUIRED."""
    settings = Settings(
        DATABASE_URL="sqlite:///:memory:",
        ALLOW_INSECURE_FALLBACK=True,
        SANDBOX_BACKEND=SandboxBackendType.SUBPROCESS,
    )
    init_db(settings.database_url)
    orchestrator = DailyOrchestrator(settings=settings)

    # Metadata from Codeforces official API (no statement text)
    mock_meta = ProblemMetadata(
        platform=Platform.CODEFORCES,
        external_id="1890_A",
        title="Doremy's Paint 3",
        slug="1890-a",
        url="https://codeforces.com/problemset/problem/1890/A",
        difficulty_label="800",
        raw_content=None,  # No statement provided by official API
    )

    with (
        patch.object(orchestrator, "_select_codeforces_problem", AsyncMock(return_value=mock_meta)),
        patch.object(orchestrator.solver, "solve_problem", AsyncMock()) as mock_solve,
    ):
        result = await orchestrator.run_daily(force_platform="codeforces")

        # Must report STATEMENT_REQUIRED and NOT call solver
        assert result["status"] == "STATEMENT_REQUIRED"
        mock_solve.assert_not_called()
        assert "Please provide the problem statement" in result["message"]


def test_user_supplied_statement_and_test_cases() -> None:
    """Test user-supplied problem statement workflow updating the database."""
    settings = Settings(DATABASE_URL="sqlite:///:memory:")
    init_db(settings.database_url)

    with get_db() as db:
        repo = Repository(db)
        prob = repo.create_or_get_problem(
            platform="codeforces",
            external_id="1890_A",
            title="Doremy's Paint 3",
            slug="1890-a",
            url="https://codeforces.com/problemset/problem/1890/A",
            difficulty_label="800",
            description=None,
        )
        assert prob.description is None
        assert len(prob.test_cases) == 0

        # User provides statement and sample tests
        statement_text = (
            "An array a is good if all pairs satisfy conditions... "
            "Determine if array can become good."
        )
        user_tests = [
            {"input_data": "1\n2\n8 9\n", "expected_output": "Yes\n", "is_verified": True}
        ]

        repo.set_problem_statement(prob, description=statement_text, test_cases=user_tests)
        db.commit()

        # Verify updated problem in database
        updated = repo.create_or_get_problem(
            platform="codeforces",
            external_id="1890_A",
            title="Doremy's Paint 3",
            slug="1890-a",
            url="https://codeforces.com/problemset/problem/1890/A",
            difficulty_label="800",
        )
        assert updated.description == statement_text
        assert len(updated.test_cases) == 1
        assert updated.test_cases[0].input_data == "1\n2\n8 9\n"
        assert updated.test_cases[0].expected_output == "Yes\n"
        assert updated.test_cases[0].is_verified is True
