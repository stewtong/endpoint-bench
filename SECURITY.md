# Security policy

## Reporting

Do not open a public issue for a security vulnerability.

Report privately through [GitHub security advisories](https://github.com/stewtong/endpoint-bench/security/advisories) for this repository. Include a minimal reproduction, affected command/commit, environment, and any logs with credentials removed.

## Supported configuration

Only the latest default branch and tagged releases are supported. The project is pre-release; live inference execution is intentionally unimplemented.

## Handling credentials

- Store credentials only in environment variables at execution time.
- Do not put credentials in source, config files, manifests, reports, logs, issue reports, screenshots, or command-line arguments.
- Do not paste API keys in GitHub issues.
- Do not send prompts, traces, private repositories, finance/health/personal content, or restricted third-party data to an inference endpoint without explicit authorization.

## Execution safeguards

- Offline workflows must not perform inference requests.
- `--execute` fails closed unless live execution has been implemented and explicitly authorized.
- Unknown pricing is not treated as zero cost.
- CI must not receive provider credentials.
- External pull-request workflows must not run paid or live inference.
- API responses and provider metadata are untrusted input; response text is never executed.

GitHub private vulnerability reporting and secret scanning cannot be verified from a local checkout. The repository owner should confirm both repository settings before public promotion.

## Data handling

Prefer sanitized hashes over raw prompts/responses for public reports. Raw benchmark evidence remains local unless rights and privacy review permit publication. Restrict AA snapshots according to AA terms and access tier. Redact account identifiers, usernames, hostnames, IP addresses, and other sensitive metadata before export.
