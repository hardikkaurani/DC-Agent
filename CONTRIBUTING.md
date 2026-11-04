# Contributing to DC-Agent

Thank you for your interest in contributing to **DC-Agent**! We welcome bug reports, feature requests, documentation improvements, and code contributions.

## Code of Conduct

All contributors must adhere to our [Code of Conduct](CODE_OF_CONDUCT.md). Please be respectful and constructive in all interactions.

## Development Setup

DC-Agent requires **Python 3.12+** and uses **[uv](https://docs.astral.sh/uv/)** for ultra-fast, reproducible package management.

### 1. Clone and Install
```bash
git clone https://github.com/hardikkaurani/DC-Agent.git
cd DC-Agent
uv sync --all-extras
```

### 2. Environment Configuration
Copy the sample environment file:
```bash
cp .env.example .env
```
Edit `.env` to configure your `GEMINI_API_KEY`. **Never commit `.env` or your real API keys.**

### 3. Run Checks
Ensure all linters, type checkers, and tests pass:
```bash
# Code formatting and linting
uv run ruff check .
uv run ruff format --check .

# Static type checking
uv run mypy src/

# Test suite and coverage
uv run pytest -v --cov=src/dc_agent
```

## Pull Request Guidelines

1. Create a descriptive topic branch (`git checkout -b feature/my-new-feature`).
2. Adhere strictly to the existing hexagonal monolithic architecture.
3. Write comprehensive unit and integration tests. Core business logic must maintain $\ge 85\%$ test coverage.
4. Update relevant documentation in `docs/` and `README.md`.
5. Ensure all CI checks pass.

