# Security Policy

## Scope

sapimo is a mock for **local development only**. By design it does not verify JWT signatures,
allows all CORS origins, and passes dummy AWS credentials to Lambda functions.
These are not considered vulnerabilities. Do not run sapimo in shared or production environments.

## Supported Versions

Only the latest commit on the `main` branch is supported.

## Reporting a Vulnerability

Please do **not** open a public issue for security problems.
Report them privately through GitHub:
[Report a vulnerability](https://github.com/angle-eight/sapimo/security/advisories/new)
(Security tab → "Report a vulnerability").

Please include the affected version or commit, steps to reproduce, and the impact.
We aim to acknowledge reports within 7 days.
