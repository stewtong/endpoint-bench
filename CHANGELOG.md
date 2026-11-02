# Changelog

All notable changes are documented here. This project distinguishes the software package version, benchmark specification/methodology version, result schema version, profile schema version, and external AIPerf engine version. Current values are software `0.1.2`, specification `0.5.3`, result schema `0.2.0`, profile schema `1.0`, and pinned engine AIPerf `0.13.0`.

## [0.1.2] — 2026-11-01

- Reject malformed SSE even when followed by a natural terminal frame; enforce natural finish-reason/class consistency in trial validation and probe results.
- Keep provider-token observations separate from requested-workload conformance; withhold natural-completion means and percentiles from censored latency.
- Add regression coverage, versioned Correction 2 evidence, and an exploratory repeat plan with explicit window spacing and a pilot stop rule.
- Allow provenance-verified provider accounting for delivered-workload performance; move endpoint error rate to an unratified performance objective. Qualification remains unimplemented and inconclusive.

## [0.1.1] — 2026-11-01

Measurement-integrity correction. Three harness defects were found by reprocessing the six published screening cells from their original artifacts; two of them changed published numbers.

### Fixed

- **Token sizing was circular under provider accounting.** AIPerf derives `input_sequence_length` from the provider's `usage.prompt_tokens` in that mode, so the client-versus-provider comparison compared a number with itself: `discrepancy_pct` was always `0.0` and `sizing_verified` was always `true`, regardless of what the provider received. Sizing is now two separate, always-published verdicts — provider-versus-target conformance and client-versus-provider agreement — and the client measure is left absent under provider accounting rather than back-filled. `TokenAccounting` raises at construction if a client estimate is attached under provider accounting, so the defect cannot be reintroduced by a caller.
- **Every protocol-completed response was scored `success`.** `finish_reason` was never read, and was `null` in all 120 stored trial records. In fact 117 of 120 responses stopped at the 256-token output ceiling. Terminal reasons are now recovered from the raw stream, joined by `x_request_id`, and mapped to natural, ceiling, content-filter, absent, or unknown. Ceiling truncation becomes `output_truncated` attributed to `harness` and is counted in a new `truncated_trials` counter, separate from `failed_trials`. `success` now requires a witnessed 2xx status and a natural finish reason, enforced in `Trial.validate()`.
- **A non-2xx response could be recorded as success.** Status mapping now takes precedence over body content for 429, 5xx, 413/422, and 4xx, with context rejection distinguished by message where the body supports it.
- **Timings were discarded when no trial completed naturally.** `metric_validity` treated `successful_trials == 0` as "no observation", which marked time-to-first-token and latency `not_observed` in a run that had measured both. Presence of the metric now governs observation, independent of outcome classification.
- **Provider cache and reasoning telemetry was discarded.** Both were present in every run and reached no report. Cache-write has no AIPerf metric at all and is now recovered from the raw streamed `usage.prompt_tokens_details.created_cache_tokens`. Every field carries its source, and absence stays `null` rather than becoming zero.

### Added

- `completion` block on trials and reports: `finish_class`, `finish_reason_counts`, `natural_completions`, `ceiling_truncated`, `latency_censored`, `latency_censored_fraction`.
- `provider_telemetry` block on trials and reports: cache-read, cache-write, reasoning, and derived visible-output tokens, with per-field provenance and reporting counts.
- `censored` flag on `metric_validity` entries, set for latency metrics when any completed trial was ceiling-truncated. Time-to-first-token is never censored.
- `provider_telemetry_summary`, `completion_summary`, and a `client_estimates_coinciding_with_provider` check that surfaces the signature of the old circularity.
- `endpoint-bench reprocess --run <dir> --out <dir>`: re-derives a completed run's evidence from its original artifacts without re-executing anything, writing a corrected `report.json` and a `correction.json` with per-field before/after, source artifact hashes, and an explicit unrecoverable list. The source directory is read-only and the command refuses to write inside it.
- Legacy report loading: schema `0.1.0` remains readable but is labelled `legacy_schema: true` with a reason, never silently upgraded.
- `compare` refuses to compare runs whose token accounting source differs.
- `qualification-10k` profile (1024-token output ceiling, sized from the measured natural completion length).
- `docs/GATE3_RESULTS_CORRECTION_1.md`, `docs/qualification/` SLO contract, and `docs/gate3-plans/multiwindow-10k.json`.
- 33 regression fixtures in `tests/test_measurement_integrity.py`.

### Documented

- `docs/GATE3_RESULTS.md` annotated with a correction notice; the original content is retained unmodified as the published record.
- `docs/RESULTS_SCHEMA.md` raised to `0.2.0` with the two-sided token accounting, completion classification, telemetry, censoring, and reprocess contract.
- `docs/METHODOLOGY.md` adds why the two sizing comparisons are not interchangeable, completion classification, and the boundary between cache telemetry and a cache measurement.

### Changed

- `benchmark_failed` now means the endpoint produced nothing scoreable: no successful responses *and* no ceiling-truncated ones. A run in which every response was cut by our own ceiling measured real first-token latency and token rates, so it is reported as truncated with censoring limitations instead of as a benchmark failure.
- Result schema `0.1.0` → `0.2.0`; trial schema `0.1.0` → `0.2.0`.

## [Unreleased]

### Added

- Live-turn tool-call round-trip probe with streamed argument reassembly, identifier and JSON and argument-type validation, synthetic tool results keyed to call id, full-history resubmission, and required terminated continuation.
- Tool-call conformance variants: multiple calls, missing and inconsistent call ids, invalid JSON arguments, interrupted tool streams, absent tool calls attributed to the model, large tool observations, streaming and non-streaming, malformed and timed-out continuations.
- Per-trial `attribution` separating provider/protocol failure from model semantic behaviour.
- Run provenance: workload type, engine and schema versions, deployment and profile identity, client profile, affinity mode, timeout and retry policy, planned-versus-actual request reconciliation, termination report, and run-relative artifact locations.
- Numeric-loopback-only execution guard rejecting hostnames, credential-bearing URLs, missing ports, non-allowlisted addresses, IPv4-mapped addresses, and ambiguous numeric forms.
- Proxy-variable stripping and redirect refusal for local execution.
- SIGTERM-then-SIGKILL process-group termination with orphan-worker detection and separate classification of timeout, cancellation, benchmark failure, and harness failure.
- `probe --scenario tool-round-trip` workflow, plus `probe --dry-run` planning with cost preflight.
- `endpoint-bench mock` command to serve the local provider on an explicit loopback port.
- Agent-shaped Mooncake trace replay: `run --trace-file` / `--trace-type`, with the workload labelled `recorded_multi_turn_replay`.
- Metric-specific validity states (`valid`, `valid_mean_only`, `invalid_token_sizing`, `invalid_client_tokenization`, `not_observed`) so one measurement problem does not invalidate unrelated evidence.
- `--concurrency` override on `run`, validated against the profile's request count.
- `screening-1k`, `screening-10k`, `screening-32k` profiles.
- Expanded CI: separate lint, test, and offline-workflow jobs plus an opt-in engine-integration job that installs the pinned AIPerf release.
- `probe --authorize-live` for explicitly authorized non-loopback conformance probes, recording the authorization and cost preflight in the manifest.
- `run --token-accounting server` to account tokens from provider usage instead of the built-in tokenizer, with the resulting limitation recorded in the report.
- `scripts/verify.sh` for the full local verification suite and `scripts/push-and-open-pr.sh` for the CI push path.

### Documented

- `docs/GATE3_RESULTS.md`: screening matrix for one deployment at 1K/10K/32K and concurrency 1/2, with explicit statements of what the evidence supports and what it does not.

### Fixed

- Truncation by our own output ceiling was reported as `tool_call_absent` with attribution `model`. It is now `output_truncated` with attribution `harness`; a model must not be blamed for a limit we imposed.
- Tool-call probes raised the output budget to a 512-token floor and use a tool-directed instruction. Reasoning deltas counted against a text-sized budget and truncated the call before it appeared.
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

## [0.1.0] — 2026-11-02

Benchmark MVP. The plan's MVP criteria are met: functional AIPerf integration, endpoint performance measurement, streaming and tool-call conformance probes, repeatable benchmark profiles, normalized metrics and evidence, working reports, and reproducible authorized live experiments.

This is not a qualification milestone. No endpoint has been qualified, no threshold is calibrated, and every measurement so far is single-window screening.

See [Unreleased] above for the change list.

## [0.0.1] — Unreleased pre-alpha

### Added

- Python 3.13 package and `endpoint-bench` CLI entry point.
- Offline provider registry validation and network-free dry-run manifest generation.
- Initial methodology and result-schema contracts.
- Contribution, security, development, licensing-decision, and changelog documentation.
- Offline tests, synthetic configuration/example, and GitHub Actions CI.
