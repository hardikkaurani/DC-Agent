# DC-Agent — Project Specification

## 1. Executive Summary & Vision

**DC-Agent** is an open-source, terminal-native daily competitive programming assistant. Its core purpose is to cultivate systematic daily problem-solving discipline across two primary competitive programming platforms: **LeetCode** and **Codeforces**.

Unlike generic coding assistants or ad-hoc scrapers, DC-Agent is built on strict software engineering and compliance principles:
- It acts as a local orchestrator and trainer, not an automated submission bot.
- It operates with 100% adherence to platform Terms of Service (ToS) and official API limitations.
- It enforces daily idempotency, deterministic progression rules, and rigorous sandboxed local verification of generated solutions.

---

## 2. Configured User Identities & Platform Scope

The system is configured for the following developer profiles:

| Platform | Profile Handle | Target Workflow | Verification Channel |
| :--- | :--- | :--- | :--- |
| **LeetCode** | `hardikkaurani_` | Official Daily Coding Challenge (Streak / Flame) | Public GraphQL profile query (`recentSubmissionList`) |
| **Codeforces** | `hkvariant` | Curriculum-based progressive difficulty (800–2300+) | Official read-only REST API (`user.status`) |

---

## 3. Platform-Specific Functional Specifications

### 3.1 LeetCode Integration

#### Problem Selection
- **Official Daily Challenge Only**: DC-Agent MUST retrieve the official active Daily Coding Challenge associated with LeetCode's daily streak / flame calendar.
- **No Random Selection**: DC-Agent is strictly prohibited from selecting random problems, catalog recommendations, or past challenges unless in an explicit historical review mode.
- **Metadata Retained**: Problem title, title slug, difficulty level (Easy/Medium/Hard), official date, frontend question ID, topic tags, and official example test cases.

#### Compliance & Integration Boundary
- **Zero Scraping & Zero Browser Automation**: No HTML web crawling, headless browser automation (Playwright/Selenium/Puppeteer), CAPTCHA interception, or bypass mechanisms.
- **Zero Credential / Session Storage**: Never solicit, extract, or store LeetCode passwords, OAuth tokens, or browser cookies (`LEETCODE_SESSION`, `csrftoken`).
- **Query Mechanism**:
  - DC-Agent utilizes the public, unauthenticated GraphQL endpoint `https://leetcode.com/graphql` querying the `activeDailyCodingChallengeQuestion` field.
  - A fallback CLI manual input mechanism is provided (`dc-agent leetcode set-daily --url <url>` or `--slug <slug>`) if Cloudflare WAF challenges block unauthenticated command-line GraphQL requests.

#### Solution & Local Verification
- DC-Agent scaffolds a localized problem workspace containing:
  - Solution file stub typed for Python 3.12+ (matching the LeetCode method signature).
  - Test runner extracting sample cases from the problem metadata.
- Generated code is executed in a strictly constrained local sandbox.

#### Submission & Verdict Verification
- **User-Controlled Submission**: DC-Agent displays or copies the validated solution for the user. The user manually submits the solution via their own browser session on leetcode.com.
- **Platform Confirmation Required**: DC-Agent will NEVER mark a problem as `ACCEPTED` based solely on local test passes.
- **Public Profile Verification**: DC-Agent verifies the submission by querying `recentSubmissionList(username: "hardikkaurani_")` via the public GraphQL interface. A session is marked `ACCEPTED` if and only if a submission exists for `titleSlug` with `statusDisplay == "Accepted"` and timestamp matching the active cycle date.

---

### 3.2 Codeforces Integration

#### Curriculum Structure & Progression Engine
Codeforces problems are selected dynamically from a structured skill curriculum divided into 6 distinct rating tiers:

| Tier | Rating Band | Core Focus / Category |
| :--- | :--- | :--- |
| **Tier 1** | `800 – 1000` | Fundamentals, Basic Math, Strings, Brute Force |
| **Tier 2** | `1100 – 1300` | Implementation, Greedy Algorithms, Sorting |
| **Tier 3** | `1400 – 1600` | Binary Search, Two Pointers, Prefix Sums, Number Theory |
| **Tier 4** | `1700 – 1900` | Dynamic Programming (DP), Graph Traversal/Shortest Path, Constructive Algorithms |
| **Tier 5** | `2000 – 2200` | Advanced DP, Trees/Segment Trees, Network Flow, Disjoint Set Union (DSU) |
| **Tier 6** | `2300+` | Expert Combinatorics, Advanced Geometry, String Structures (Suffix Automaton) |

#### Problem Selection Algorithm
1. **History Exclusion**:
   - Query the official Codeforces API endpoint `https://codeforces.com/api/user.status?handle=hkvariant`.
   - Aggregate all problems `(contestId, index)` having `verdict == "OK"`.
   - Exclude all previously accepted problems from the selection pool.
2. **Target Matching**:
   - Query `https://codeforces.com/api/problemset.problems`.
   - Filter problems where `rating == current_target_rating` (or within the current tier interval `[tier_min, tier_max]`).
   - Prioritize problems matching the primary category tags of the active tier.
   - Exclude recently failed or exhausted problems (cooling-off window of 14 days).
3. **Deterministic Selection**:
   - Problems are ordered deterministically by popularity (`solvedCount` descending) and hashed against the current cycle date to ensure repeatable, non-flickering daily selections.

#### Promotion, Demotion, and Retry-Budget Rules
- **Retry Budget**:
  - Each daily problem has an initial budget of **3 attempts** ($B = 3$).
  - An attempt is consumed whenever a generated solution fails local test verification or the user reports a platform rejection (WA, TLE, MLE, RTE).
  - If $B = 0$ without an accepted verdict, the daily session transitions to `EXHAUSTED`.
- **Promotion Rule**:
  - If the user achieves **3 consecutive `ACCEPTED` sessions** at the current target rating without exhausting retry budgets:
    - Target rating is promoted: $R_{\text{target}} \leftarrow R_{\text{target}} + 100$.
    - If $R_{\text{target}} > \text{Tier}_{\max}$, promote to the next Tier.
    - Reset `consecutive_failed_count = 0`.
- **Demotion Rule**:
  - If the user suffers **2 consecutive `EXHAUSTED` sessions** at the current target rating:
    - Target rating is demoted: $R_{\text{target}} \leftarrow \max(800, R_{\text{target}} - 100)$.
    - If $R_{\text{target}} < \text{Tier}_{\min}$ and $\text{Tier} > 1$, demote to the preceding tier.
    - Reset `consecutive_accepted_count = 0`.

#### Ingestion & Verification Boundary
- **Official API Limitation**: The Codeforces official API provides problem metadata ONLY. It does NOT provide problem descriptions, HTML statements, or sample test cases.
- **Non-Scraping Policy**: To strictly abide by the zero-scraping directive, DC-Agent does NOT crawl `codeforces.com/problemset/problem/...`.
- **Scaffold & Ingestion**:
  - DC-Agent outputs the direct problem URL (`https://codeforces.com/problemset/problem/{contestId}/{index}`).
  - DC-Agent initializes a local workspace file `tests.json` or `input_1.txt` / `output_1.txt`.
  - The user pastes sample inputs/outputs into the workspace or CLI, or uses standard local competitive programming tooling (e.g. Competitive Companion local receiver).
- **Verdict Verification**:
  - The user submits via web UI.
  - DC-Agent polls `https://codeforces.com/api/user.status?handle=hkvariant` to verify the submission verdict `OK`.

---

## 4. Daily Idempotency Strategy

- **Granularity**: Exactly one active challenge per platform per UTC calendar day ($D = \text{YYYY-MM-DD}$).
- **State Guarantee**:
  - Re-running `dc-agent daily` at any point during day $D$ returns the identical problem and current attempt state.
  - If the session on day $D$ is in `ACCEPTED` or `EXHAUSTED` state, the CLI outputs the terminal summary and takes no mutating actions.
  - State transitions are managed in SQLite via immediate ACID transactions (`BEGIN IMMEDIATE`) with a composite unique constraint `UNIQUE(platform, cycle_date)`.

---

## 5. Secure Local Execution Model

Untrusted code generated by AI assistants or retrieved from external sources must NEVER run directly on the host system without protection.

### Threat Vector Defense
1. **Filesystem Intrusion**:
   - Threat: Scripts accessing SSH keys, `.env` files, or attempting disk wiping.
   - Mitigation: Ephemeral isolated temporary workspace, stripped environment variables (no access to `PATH`, host secrets, or home directory), AST blocklist for `os`, `sys`, `shutil`, `pathlib`, `builtins.open` (write mode).
2. **Resource Exhaustion**:
   - Threat: Infinite loops, memory bombs (`[0] * 10**9`), recursive stack overflows, fork bombs.
   - Mitigation: Subprocess execution with strict wall-clock timeout ($T \le 2.0\text{s}$), memory limits ($M \le 256\text{MB}$ via OS job objects / rlimit), zero subprocessing privileges.
3. **Network Exfiltration**:
   - Threat: Solutions attempting to establish sockets or make HTTP requests.
   - Mitigation: AST blocklist on `socket`, `urllib`, `requests`, `http`, `asyncio`, network namespace isolation where supported.
4. **Safety Circuit Breaker**:
   - If an execution safety violation is detected (e.g., attempt to invoke forbidden built-in or import unapproved package), retry attempts are **immediately terminated** and the session is marked `SAFETY_VIOLATION`.

---

## 6. Technical Stack & Constraints

- **Language**: Python 3.12+ with strict static typing (`mypy --strict`).
- **CLI Framework**: Typer with Rich terminal formatting.
- **Persistence**: SQLite with Write-Ahead Logging (WAL) and SQLAlchemy 2.0 declarative models.
- **HTTP Client**: `httpx` with timeout management and exponential backoff retry.
- **Package & Environment Management**: `uv`.
- **Quality & Testing**: `ruff` (linter/formatter) and `pytest` with `pytest-cov`.
- **CI/CD**: GitHub Actions matrix testing against Python 3.12 and 3.13 on Ubuntu and Windows.
- **No Complex Dependencies**: No Docker requirement for base operation (Docker available as optional backend), no Redis, no background celery workers, no web server frameworks.

---

## 7. Acceptance Criteria

1. **Deterministic Selection**: LeetCode selection always matches LeetCode's active daily challenge; Codeforces selection strictly follows rating tier boundaries and excludes all solved problems for `hkvariant`.
2. **Zero False Positives**: No problem is marked `ACCEPTED` without positive platform confirmation via public profile queries or official APIs.
3. **Strict Idempotency**: Multiple invocations within the same UTC date produce zero duplicate records or state corruption.
4. **Zero Credential Storage**: No user passwords, API secrets, or session cookies exist on disk or in the database.
5. **Enforced Sandboxing**: Code execution containing forbidden calls (`os.system`, `subprocess.Popen`, socket connections) is intercepted and rejected prior to host damage.

