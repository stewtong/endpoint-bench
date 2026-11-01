# Agent instructions — Agent Endpoint Bench

**Version:** 0.5.0
**Datestamp:** 2026-11-01
**Source:** Canonical endpoint benchmark architecture, historical Token Factory evidence, AIPerf 0.13.0 inspection, AA documentation, and the v0.5 public-readiness audit.
**Update rule:** Keep these instructions aligned with `BENCHMARK_PLAN.md` and `IMPLEMENTATION_TASKS.md`. Preserve historical evidence and methodology history separately.

These instructions apply to AI coding agents implementing or modifying this repository. Read `BENCHMARK_PLAN.md` and `IMPLEMENTATION_TASKS.md` before changing code.

## Objective and scope

Build a provider-neutral endpoint readiness and performance qualification system. Measure actual endpoint behavior: latency, throughput, stability, concurrency, prompt caching, protocol compatibility, tool-call round trips, error handling, and effective context.

This is not a model-intelligence benchmark. Do not grade reasoning quality, broad coding intelligence, general knowledge, or subjective answer quality. Artificial Analysis (AA) remains a separate external reference dataset. Do not join AA scores to endpoint measurements, produce a composite ranking, recommend a model for a workload, or build routing logic in this phase.

Lightweight deterministic probes may verify protocol and feature compatibility, but they do not establish model intelligence. Recorded trace replay is not autonomous agent execution.

## Repository and historical evidence

This repository is the implementation target for Agent Endpoint Bench. Do not create another parallel implementation repository. A fresh clone must work without external Token Factory history, private local paths, credentials, or prior benchmark results.

The prior Token Factory project and November OpenClaw evidence are optional external historical observations. Preserve them, inspect them when useful, and do not pool them with new measurements. Do not replace working historical scripts without inspecting them first.

The existing Token Factory candidate list is seed configuration only. Never hard-code Nebius or Token Factory assumptions into core orchestration.

## Working rules

1. **Inspect before building.** Inspect code, scripts, fixtures, results, documentation, dependency versions, AIPerf CLI behavior, and source APIs. Record verified capabilities and limitations.
2. **Implement incrementally.** Follow the gate order in `IMPLEMENTATION_TASKS.md`. Do not build a large abstraction with only one demonstrated implementation.
3. **Prefer composition.** Use AIPerf for performance, load, and trace replay where it works. Add custom code only for missing protocol conformance, orchestration, normalization, or reporting.
4. **Reproducibility over convenience.** Pin dependencies, use UTC timestamps, hash configs and fixtures, preserve immutable manifests/raw evidence, and make methodology changes traceable.
5. **Measure implementation, not intelligence.** Deterministic probes check protocol/schema/termination/tool-call behavior. Do not grade patch quality or subjective answers.
6. **No fabricated values.** Never claim unexecuted tests, successful APIs, precise revisions, provider limits, AA scores, or measured performance. `unknown`, `unsupported`, `unverified`, `not entitled`, and `not measured` are distinct.
7. **Separate lifecycle from outcome.** Track planned/started/transport-completed/protocol-completed/skipped/cancelled separately from success/failure outcomes. Track HTTP attempts separately from trials because retries can create multiple attempts per trial.
8. **Qualify conservatively.** Use versioned, profile-specific thresholds. Keep full metrics independent of qualification. Do not mark qualified when evidence is insufficient or the cause is ambiguous.
9. **Control client effects.** Inspect session affinity, default headers, retries, connection reuse, timeouts, batching, prompt normalization, tokenization, conversation serialization, and cache controls. Do not silently mix client configurations.
10. **Bound cost conservatively.** Default to offline/dry-run. Registry validation, offline tests, dry-run manifests, and synthetic fixtures may use unknown pricing when it is represented as `null`/unknown. Paid runs require explicit `--execute`, an allowlisted target, a dated provider-specific price source, a conservative exposure estimate, and local request/token/concurrency/duration/estimated-cost ceilings. Unknown pricing must not silently become zero. A provider-side enforcement mechanism is required before claiming a hard dollar ceiling.
11. **Secrets stay secret.** Use environment variables at execution only. Do not place credentials in source, configs, manifests, logs, reports, or process arguments. Sanitize logs and failure stacks.
12. **Match reality.** `/models`, marketing claims, AA context, and model-name similarity do not prove endpoint capabilities. Probe behavior and label confidence.
13. **Keep AA separate.** Use documented endpoints and rights. Preserve original fields, units, methodology version, access tier, attribution, and hashes. Do not commit restricted data or redistribute it without rights.
14. **Avoid infrastructure disruption.** Do not modify launchd services, restart local AI services unnecessarily, change networking/firewalls, install global dependencies unnecessarily, expose local APIs publicly, or interfere with Ollama/Open WebUI/OpenClaw.
15. **Preserve evidence.** Never delete or overwrite historical benchmark evidence to make tests pass. Keep raw, normalized, and summary artifacts distinct.

## Public-readiness rules

1. README statuses and quickstarts must reflect verified repository behavior. Never describe planned capabilities as implemented or dry-run manifests as benchmark results.
2. Keep `README.md` as public onboarding, `BENCHMARK_PLAN.md` as methodology/architecture, `IMPLEMENTATION_TASKS.md` as milestones, `docs/METHODOLOGY.md` as detailed validity, and `docs/RESULTS_SCHEMA.md` as normalized contracts.
3. Add dependencies only when they solve a demonstrated requirement. Pin compatible versions, preserve `uv.lock`, and isolate Python 3.13 for current/AIPerf work.
4. CI must run offline without provider credentials, paid traffic, private files, or the original development machine.
5. Publish no license until the owner approves it. Until then, do not represent the repository as licensed open-source software. Do not vendor AIPerf or redistribute restricted AA data.
6. Public examples must be labeled synthetic. Sanitize hostnames, usernames, account identifiers, paths, prompts, responses, and raw provider metadata before export.

## Before each implementation task

- Read the relevant plan section and gate checklist.
- Identify the smallest change that satisfies the acceptance test.
- Separate verified assumptions from unknowns.
- Check AIPerf before implementing custom throughput, scheduling, or trace generation.
- If AIPerf behavior differs from the plan, update the plan with source evidence and adapt the integration; do not silently work around the difference.
- For AA schema changes, preserve old snapshots, add regression fixtures, and document migration.
- Run relevant offline tests before declaring work complete.

## Required tests

Offline tests must not require network access or credentials. They should assert outcomes, not merely execute code paths.

At minimum, cover:

- invalid endpoint/model ID, missing credentials, unknown-pricing handling, unsupported protocol, unverified context, and revision mismatch;
- successful streaming, reasoning followed by answer, fragmented tool-call arguments, multiple tool calls, tool-result continuation, invalid JSON, truncated/interrupted streams, missing usage, timeout before/after first token, 429/5xx, retries, and cancellation;
- token-count mismatch, wrong latency denominator, invalid throughput, missing cache counters, skipped/interrupted trials marked as success, retries counted as independent trials, unsupported features marked as passes, insufficient samples marked qualified, and AIPerf schema drift;
- AA pagination, Free/Pro fixtures, missing/null scores, authorization/rate-limit responses, ambiguous aliases, revision mismatch, unknown fields, methodology/index changes, and incomplete snapshots;
- dry-run producing no paid traffic, secret sanitization, invalid targets failing closed, unknown pricing remaining explicit offline while blocking unattended live execution, invalid budget/concurrency rejection, and large-context runs requiring explicit authorization.

## Developer interface design targets

Command names may change only when implementation evidence supports it:

```text
endpoint-bench registry validate
endpoint-bench aa sync --tier auto --scope tracked
endpoint-bench aa validate --snapshot <path> --offline
endpoint-bench probe --target <deployment_key> --profile smoke --dry-run
endpoint-bench run --target <deployment_key> --profile quick --dry-run
endpoint-bench run --target <deployment_key> --profile quick --execute --max-usd <cap> --max-requests <cap>
endpoint-bench report --run <run_id>
endpoint-bench compare --baseline <run_id> --candidate <run_id>
```

Dry-run must make zero inference requests. It should resolve target/profile configuration, validate schemas, produce an inspectable manifest, estimate resource/cost exposure, and identify missing requirements.

Example dollar/request values are syntax examples, not spending authorization.

## Definition of done

For each completed gate:

1. Code, tests, and accurate documentation are updated together.
2. Relevant offline tests, lint/format checks, and CLI dry-run checks pass; report exact commands/results.
3. No secrets or unlicensed AA data enter source control.
4. Error states are observable instead of silent fallback or fictional zeros.
5. Artifacts include UTC timestamp, versions, hashes, provenance, and feature-support labels.
6. Remaining blockers and unexecuted work are explicit.

Never claim benchmark readiness from mocked tests alone. Do not describe planned work as delivered.

## Escalate rather than assume

Escalate decisions that materially affect cost, security, existing services, data rights, or benchmark validity. In particular, obtain explicit authorization for:

- paid inference;
- sustained/high-concurrency load;
- near-1M-token API requests;
- authenticated live AA synchronization;
- publication/redistribution of AA data;
- changes to existing Mac mini services or credentials;
- exposing private repository or sensitive files to third-party endpoints.

Ordinary offline implementation work does not require additional approval.

## Reporting format

At the end of a task or gate, report:

1. Specification changes and rationale.
2. Architecture/dependency verification.
3. Gates completed or partially completed.
4. Files changed.
5. Exact test commands and pass/fail evidence.
6. Risks and limitations.
7. Next safe command or gate.
8. Operations that remain blocked pending authorization.
