# ADR 0001: Platform Integration Boundaries, ToS Compliance, and Verdict Verification

- **Status**: Accepted
- **Date**: 2026-11-04
- **Author**: Senior Infrastructure Engineer
- **Scope**: Platform communication, authentication, local execution, and verdict verification boundaries.

---

## 1. Context and Problem Statement

DC-Agent is designed to orchestrate daily competitive programming practice for LeetCode (`hardikkaurani_`) and Codeforces (`hkvariant`). 

Both target platforms exhibit drastically different operational profiles, API surfaces, legal restrictions, and anti-abuse policies:

1. **LeetCode**:
   - Strictly enforces Terms of Service against crawling, scraping, automated harvesting, and spidering.
   - Utilizes Cloudflare Bot Management and Web Application Firewalls (WAF).
   - Does not provide a documented, contractual public REST API for developers.
   - Requires session cookies (`LEETCODE_SESSION`) and CSRF tokens for authenticated actions (e.g. submitting solutions), which can trigger CAPTCHA challenges.
   - Operates an unauthenticated GraphQL endpoint (`https://leetcode.com/graphql`) utilized by its web frontend for public metadata (daily challenge and public user profiles).

2. **Codeforces**:
   - Provides an official, documented read-only REST API (`https://codeforces.com/api/`).
   - Implements a strict rate limit of 5 requests per second.
   - Crucially, the official API **omits problem statements, HTML descriptions, and sample test cases**. Only metadata (ratings, tags, names, contest IDs) and submission records are provided.
   - Automated submission requires web authentication / CSRF parsing, which is non-standard and fragile.

3. **Core Safety Directives**:
   - Never store passwords or browser session cookies.
   - Never log secrets.
   - Never execute untrusted generated code directly on the host without sandboxing.
   - Never claim `Accepted` without a platform-confirmed verdict.
   - Never crawl, scrape, reverse engineer, or perform unattended browser automation.

We must define clear, immutable architectural boundaries governing how DC-Agent communicates with each platform, executes code locally, and confirms submission verdicts.

---

## 2. Decision Drivers

- **Zero Risk of Account Termination**: Neither configured account (`hardikkaurani_` or `hkvariant`) must ever be placed in jeopardy of bans due to bot detection, aggressive rate-limit violations, or ToS breaches.
- **Security & Secret Minimization**: No credential leakage surface. The repository and local state must remain safe even if inspected or open-sourced.
- **Architectural Simplicity & Maintainability**: No headless browsers (Selenium/Playwright/Puppeteer), reverse-engineered cookie extractors, or brittle web scrapers that break upon UI revisions.
- **Absolute Truth in Progression**: Progress through Codeforces curriculum and LeetCode streak tracking must reflect verified platform state, preventing false confidence or unearned streaks.

---

## 3. Considered Options

### Option A: Full Browser Automation & Cookie Extraction (Rejected)
- **Concept**: Embed Playwright or Puppeteer to automate the user's browser, capture session cookies, scrape problem statements, and automatically submit solutions.
- **Reason for Rejection**:
  - Explicitly violates LeetCode ToS and anti-scraping guidelines.
  - Requires storing session cookies or user credentials.
  - Highly brittle to frontend changes, Cloudflare Turnstile, and CAPTCHAs.
  - Violates the core safety directive of DC-Agent.

### Option B: Local Mocking & Synthetic Acceptance (Rejected)
- **Concept**: Run solutions against local sample tests and automatically declare problems "Accepted" locally without platform submission.
- **Reason for Rejection**:
  - Sample test cases are deliberately non-exhaustive; solutions passing samples frequently fail hidden edge cases, large inputs, or strict time limits on competitive programming judges.
  - Violates the directive: *"Never claim Accepted without a platform-confirmed verdict."*
  - Fails to maintain the user's actual platform streak and rating progress.

### Option C: Decoupled Read-Only Integration with User-Controlled Submission (Selected)
- **Concept**:
  - Read-only querying of public metadata and official APIs.
  - Local scaffolding and sandboxed execution against sample tests.
  - Human-in-the-loop submission via official web interfaces.
  - Asynchronous verification of platform-confirmed verdicts via public profiles and official APIs.

---

## 4. Decision Outcome

DC-Agent adopts **Option C**. The precise platform boundaries are established as follows:

### 4.1 LeetCode Boundary Specifications
1. **Daily Problem Ingestion**:
   - DC-Agent issues a single, read-only HTTP POST request to `https://leetcode.com/graphql` using standard client headers, querying the `activeDailyCodingChallengeQuestion` field.
   - **Fallback**: If Cloudflare WAF restricts the request, the user is prompted to provide the problem slug or URL via CLI (`dc-agent leetcode set-daily --slug <slug>`). Random problems are NEVER substituted.
2. **Authentication & Credential Policy**:
   - Zero credentials, passwords, or session cookies (`LEETCODE_SESSION`) are stored, cached, or requested.
3. **Submission Boundary**:
   - DC-Agent generates and verifies solutions locally, then presents the validated code to the user.
   - The user submits the code manually via `leetcode.com` in their personal browser.
4. **Verdict Verification**:
   - DC-Agent queries the public GraphQL field `recentSubmissionList(username: "hardikkaurani_")`.
   - A session is marked `ACCEPTED` if and only if a submission record exists with `titleSlug == active_slug`, `statusDisplay == "Accepted"`, and `timestamp >= cycle_start_timestamp`.

### 4.2 Codeforces Boundary Specifications
1. **Problemset & Solved History Ingestion**:
   - DC-Agent uses the official REST API:
     - `GET https://codeforces.com/api/user.status?handle=hkvariant` to retrieve past submissions and build an exclusion set of solved problems.
     - `GET https://codeforces.com/api/problemset.problems` to retrieve candidate problems matching current tier rating targets.
   - All requests are rate-limited to $\le 1$ request every 2 seconds (well below the 5 req/s hard ceiling).
2. **Problem Statement & Sample Test Ingestion**:
   - Because the Codeforces API does not provide problem statements or test cases, DC-Agent does **NOT** scrape `codeforces.com`.
   - DC-Agent scaffolds local test files (`tests.json` or sample test files) and provides the direct problem URL (`https://codeforces.com/problemset/problem/{contestId}/{index}`).
   - The user supplies sample test inputs and outputs via CLI prompt, file paste, or local CP tool integration (e.g. Competitive Companion).
3. **Submission & Verdict Verification**:
   - The user submits their solution through the Codeforces web interface.
   - DC-Agent queries `user.status?handle=hkvariant` to verify that a submission for `(contestId, index)` has received `verdict == "OK"`.

### 4.3 Secure Local Execution Boundary
1. **Static AST Analysis**: Before any subprocess invocation, the code is parsed into a Python AST. Any import of blacklisted libraries (`os`, `sys`, `socket`, `subprocess`, etc.) or invocation of dangerous functions (`eval`, `exec`) raises an immediate `SecurityViolationError`.
2. **Process Isolation**: The code executes in an ephemeral temporary directory with an empty environment (`env={}`) and a hard wall-clock timeout of 2.0 seconds.
3. **Retry Termination**: On any security violation or platform acceptance, retries stop immediately.

---

## 5. Consequences & Trade-offs

### Positive Consequences
- **100% Platform Compliance**: Complete alignment with LeetCode Terms of Service and Codeforces API policies.
- **Zero Risk of Compromise**: No secret management or credential exposure surface.
- **High Architectural Stability**: Independent of frontend DOM changes, CSS selectors, or obfuscated JavaScript bundles.
- **Accurate Progression Tracking**: Every progression step is backed by cryptographically/platform-confirmed records.

### Negative Consequences / Trade-offs
- **Semi-Automated Workflow**: Requires the user to manually copy/paste the solution into their browser and click Submit on the platform.
- **Manual Sample Ingestion for Codeforces**: Until an official Codeforces statement API exists, sample test cases must be provided by the user for local verification.
- **Public Profile Latency**: A delay of a few seconds may occur between platform submission and public profile indexing.

---

## 6. Compliance Matrix

| Constraint | Strategy | Status |
| :--- | :--- | :--- |
| **No scraping / crawling** | Read-only GraphQL & official REST API; no HTML parsing | COMPLIANT |
| **No browser automation** | Submission handled directly by user in their own browser | COMPLIANT |
| **No session cookie storage** | Zero cookie or credential persistence | COMPLIANT |
| **No CAPTCHA bypass** | All human interaction happens in user's browser | COMPLIANT |
| **No unauthorized code execution** | Multi-tier sandbox: AST blocklist + isolated subprocess | COMPLIANT |
| **No false Accepted verdicts** | State transitions to ACCEPTED only upon platform verification | COMPLIANT |

