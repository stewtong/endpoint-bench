# Changelog

All notable changes are documented here. This project distinguishes the software package version, benchmark specification/methodology version, result schema version, profile schema version, and external AIPerf engine version. Current values are software `0.0.1`, specification `0.5.0`, result schema `0.1.0`, profile schema `1.0`, and AIPerf target `0.13.0`.

## [Unreleased]

### Added

- Live-turn tool-call round-trip probe with streamed argument reassembly, identifier and JSON and argument-type validation, synthetic tool results keyed to call id, full-history resubmission, and required terminated continuation.
- Tool-call conformance variants: multiple calls, missing and inconsistent call ids, invalid JSON arguments, interrupted tool streams, absent tool calls attributed to the model, large tool observations, streaming and non-streaming, malformed and timed-out continuations.
- Four distinct token measures per trial (target, client-estimated, provider-reported, observed output) with a profile-defined tolerance and an explicit `sizing_verified` verdict. No correction factor is applied.
- Per-trial `attribution` separating provider/protocol failure from model semantic behaviour.
- Run provenance: workload type, engine and schema versions, deployment and profile identity, client profile, affinity mode, timeout and retry policy, planned-versus-actual request reconciliation, termination report, and run-relative artifact locations.
- Numeric-loopback-only execution guard rejecting hostnames, credential-bearing URLs, missing ports, non-allowlisted addresses, IPv4-mapped addresses, and ambiguous numeric forms.
- Proxy-variable stripping and redirect refusal for local execution.
- SIGTERM-then-SIGKILL process-group termination with orphan-worker detection and separate classification of timeout, cancellation, benchmark failure, and harness failure.
- `probe --scenario tool-round-trip` workflow.
- Cost preflight that computes a conservative upper-bound estimate from verified per-million rates and refuses execution when the estimate exceeds `--max-usd` or when rates are missing.
- HTTP status joined from raw evidence by request id, so attempt records carry the observed status.
- OpenAI-style `/v1` base URLs are normalised for AIPerf, which appends the endpoint path itself.
- Normalized trial evidence model separating lifecycle from outcome, with separate counts for planned, started, transport-completed, protocol-completed, successful, failed, skipped, cancelled trials and HTTP attempts.
- Local OpenAI-compatible mock provider with deterministic streaming, failure, truncation, interruption, timeout, missing-usage, and tool-call behaviors, recording every received request for server-side verification.
- Deterministic, hashable synthetic prompt fixture generator.
- Streaming and non-streaming conformance probes that classify one HTTP attempt per trial.
- Pinned AIPerf 0.13.0 subprocess wrapper: deterministic YAML generation, environment-only credentials, explicit client profiles, per-request timeout and process deadline, process-group termination, sanitized logs, and version-checked artifact parsing.
- `probe`, `run --execute`, `report`, and `compare` CLI workflows. Live execution remains fail-closed behind explicit ceilings and loopback or authorization checks.
- Registry fields for capability states, output limit, tokenizer, cache controls, and last-verified timestamp.
- `quick`, `standard`, `long-context`, and `reliability` profiles with explicit timeouts and provisional qualification metadata.
- `docs/decisions/0002-architecture-and-aiperf-verification.md` recording verified AIPerf behavior and the owner-specified integration invariants.

### Changed

- Standardized `endpoint-bench` as the canonical CLI command and `--profile` as the profile-selection option.
- Clarified that unknown pricing is allowed for offline validation/dry-run with an explicit null cost state, but blocks unattended live execution.
- Replaced YAML-specific requirements with format-neutral declarative configuration contracts.
- Separated pre-alpha source, benchmark MVP, and expanded operational qualification milestones.
- Generated AIPerf config uses the explicit `datasets:` list form because the singular `dataset:` shorthand is rejected during sweep expansion.

### Security

- Expanded ignored credential, key, result, trace, AA snapshot, log, scratch, and machine-configuration patterns.
- Added `.venv-aiperf/` to ignores for the pinned engine environment.

### Licensing

- Adopted the MIT License after owner approval.

## [0.0.1] — Unreleased pre-alpha

### Added

- Python 3.13 package and `endpoint-bench` CLI entry point.
- Offline provider registry validation and network-free dry-run manifest generation.
- Initial methodology and result-schema contracts.
- Contribution, security, development, licensing-decision, and changelog documentation.
- Offline tests, synthetic configuration/example, and GitHub Actions CI.
