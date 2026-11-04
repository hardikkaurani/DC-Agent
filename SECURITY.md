# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

## Reporting a Vulnerability

Security is a fundamental design pillar of DC-Agent. If you discover a security vulnerability, please do NOT file a public issue.

Instead, please report security vulnerabilities directly to:
`hardikkaurani@users.noreply.github.com`

Please include:
1. Description of the vulnerability and attack vector.
2. Steps to reproduce or proof-of-concept code.
3. Potential impact on the user's host environment or platform credentials.

We will acknowledge receipt within 48 hours and provide updates until the vulnerability is patched.

## Architectural Security Guarantees

1. **Zero Credential Persistence**: DC-Agent NEVER requests, logs, or stores LeetCode passwords, OAuth tokens, or browser cookies (`LEETCODE_SESSION`).
2. **Untrusted Code Containment**:
   - Primary defense: Containerized Docker isolation (`--network none`, read-only root, non-root user, memory & CPU limits).
   - Secondary defense: Host subprocess isolation with Windows Job Objects memory caps / POSIX `rlimit`, empty environment variables, and strict wall-clock timeouts.
   - Defense-in-depth: Static AST inspection rejecting dangerous imports (`os`, `sys`, `subprocess`, `socket`, `shutil`) and execution escapes (`eval`, `exec`).
3. **Platform Integrity**: A problem is NEVER marked `ACCEPTED` based solely on local tests. A platform-confirmed verdict is required.

