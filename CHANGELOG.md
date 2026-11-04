# Changelog

All notable changes to **DC-Agent** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-11-04

### Added
- **Gemini AI Integration**:
  - Official Google Gen AI Python SDK client (`GeminiProvider`).
  - Structured algorithmic solution generation, independent adversarial review, and failure diagnosis.
  - Exponential backoff with jitter on rate limits (429) and transient errors.
  - Secret masking to guarantee API keys are never leaked in logs or exception traces.
  - Secondary fallback provider via Ollama.
- **Codeforces Progression Engine**:
  - Official Codeforces REST API client (`problemset.problems`, `user.status`, `user.info`) with conservative rate-limiting (1 req / 2s).
  - 6-tier curriculum progression from Beginner (800) to Master (2300+).
  - Automated exclusion of previously solved problems for `hkvariant`.
  - Deterministic daily problem selection and persistent promotion (+100 rating) / demotion (-100 rating) state.
- **LeetCode Integration**:
  - Official streak/flame Daily Challenge ingestion via public GraphQL with user-assisted fallback (`dc-agent leetcode set-daily`).
  - Strict compliance with LeetCode Terms of Service (zero scraping, zero cookie storage, zero automated submission).
  - Public profile submission verification for `hardikkaurani_`.
- **Multi-Tier Execution Sandbox**:
  - Primary backend: Containerized Docker isolation with non-root user, read-only root, and no network access.
  - Secondary backend: Host subprocess runner with Windows Job Objects memory caps / POSIX `rlimit`, empty environment, and wall-clock timeout.
  - Defense-in-depth: Static AST security analyzer blocking forbidden imports and runtime escapes.
- **Persistent Daily Scheduler**:
  - APScheduler integration with Asia/Kolkata timezone support (defaults to 17:15 / 5:15 PM).
  - Alternating daily platform policy (LeetCode ↔ Codeforces).
  - Database locking to prevent concurrent runs.
- **Local FastAPI Dashboard**:
  - Localhost web interface at `http://127.0.0.1:8000` to inspect today's challenge, generated solutions, curriculum trajectory, and past attempts.
- **Production CLI**:
  - Comprehensive Typer commands: `init`, `config`, `doctor`, `status`, `daily`, `today`, `leetcode set-daily`, `codeforces profile`, `codeforces progress`, `test`, `record-verdict`, `verify`, `history`, `stats`, `schedule`, `backup`, `reset`, `dashboard`.
- **Relational Persistence**:
  - SQLite with Write-Ahead Logging (WAL) and SQLAlchemy 2.0 declarative models enforcing strict daily idempotency.
- **Automated CI/CD**:
  - GitHub Actions matrix testing against Python 3.12 and 3.13 on Ubuntu and Windows.
  - Automated daily challenge workflow (`daily.yml`).

