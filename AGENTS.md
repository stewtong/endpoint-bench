# Agent instructions — Agent Endpoint Bench

These instructions apply to AI coding agents implementing or modifying this repository (Codex, Claude Code, OpenClaw, and equivalents). Read `BENCHMARK_PLAN.md` and `IMPLEMENTATION_TASKS.md` before changing code.

## Objective and scope

Build **provider-neutral endpoint readiness/performance qualification**, not a model-intelligence benchmark. Prefer NVIDIA AIPerf as the performance engine, with a narrow independent protocol-conformance layer. Import Artificial Analysis (AA) model scores as external, source-attributed local reference snapshots. **Do not join AA scores to measured endpoint results, recommend models for workloads, or invent a ranking formula** in this implementation.

The existing Token Factory candidate list is seed configuration only. Never hard-code Nebius-specific assumptions into core orchestration. Keep direct API trials separate from any OpenClaw/framework validations.

## Working rules (enforced priorities)

1. **Inspect before building.** Inspect repository state, existing benchmark scripts/results, relevant README/docs, installed AIPerf CLI/version, and source APIs. Write down verified capability/limitation notes. Do not replace working modules without evidence.
2. **Implement incrementally.** Finish each gate in `IMPLEMENTATION_TASKS.md`, run offline tests, review diff, then proceed. Do not build a framework abstraction that has only one demonstrated implementation.
3. **Reproducibility > convenience.** Pin dependency versions, hash fixtures and configuration, use UTC timestamps, save immutable manifests and raw evidence; source code changes and API revision changes must be traceable.
4. **Measure implementation, not intelligence.** Live-turn canaries validate protocol/schema/termination/transport behavior. Do not grade patch quality or subjectively score model answers. Recorded trace replay is not an autonomous agent run.
5. **No fabricated values.** Never claim unexecuted tests, successful APIs, precise model revisions, provider limits, AA scores, or measured performance. `null`, unknown, unsupported, not-entitled, and not-measured are distinct.
6. **Hard cost and rate guards.** Default all benchmark traffic to `--dry-run` / offline. Require explicit `--execute`, allowlisted target, and hard budgets for paid calls. Do not run heavy context/load/soak profiles without explicit user authorization. Never attempt to evade rate limits.
7. **Secrets stay secret.** Access API keys only by environment variable at execution. Do not print, commit, serialize, or send them to AA or endpoint logging beyond the required auth request. Sanitize logs and failure stacks. No network requests in unit tests.
8. **Match reality, not promotional claims.** `/models`, provider marketing, AA model context, and model-name similarity are not proof of endpoint capabilities. Probe actual behavior and record confidence. A restart of our client does not prove server cold start.
9. **Keep AA separate.** Use documented REST API endpoints only. Free `/language/models/free` provides indices; Pro `/language/models` provides per-benchmark scores; preserve original score names/units and methodology version. Coding/Agentic Index fields are currently null; do not synthesize them. Respect attribution, tier limits, terms, and organizational redistribution restrictions.
10. **Avoid infrastructure disruption.** Mac mini is headless and runs existing Ollama/Open WebUI/OpenClaw services. Work in a dedicated project path, use an isolated Python environment, avoid changes to launchd/system settings, and never expose model APIs publicly for a benchmark.

## Before each implementation task

- Identify the smallest change that satisfies the acceptance test.
- State verified assumptions vs unknowns in a task note or commit/PR description.
- Look for existing AIPerf implementation before writing custom throughput, scheduling, or trace generators.
- If AIPerf behavior differs from the plan, update `BENCHMARK_PLAN.md` with source evidence **and** adapt the integration. Do not silently work around behavioral differences.
- If AA changes schema/tier/index, preserve old snapshots, update parser with a regression fixture, and document the migration. Never delete historical results to make tests pass.

## Required developer commands (design targets)

Create and document commands with equivalent behavior; exact CLI names may be adjusted during implementation:

```text
bench registry validate
bench aa sync --tier auto --scope tracked
bench aa validate --snapshot <path> --offline
bench probe --profile smoke --target <deployment_key> --dry-run
bench run --profile quick --target <deployment_key> --dry-run
bench run --profile quick --target <deployment_key> --execute --max-usd <cap> --max-requests <cap>
bench report --run <run_id>
bench compare --baseline <run_id> --candidate <run_id>
```

`--dry-run` must resolve targets, fixtures, AIPerf arguments, forecast upper bound on paid activity, validate support/credentials **without making paid endpoint calls** (it may omit credential checks if doing so would require network traffic), and produce an inspectable manifest. For AA, `sync` is a separately authenticated metadata operation and must not accidentally trigger a paid inference run.

## Required test fixtures

- Offline mocked AA Free/Pro pagination, `401`, Pro→Free `403` fallback, `429 Retry-After`, 5xx and incomplete/null score records.
- Explicit model-ID→AA-ID mappings including variant mismatch, ambiguous alias, no match, and index-version change.
- Mock endpoint records: successful stream, first reasoning vs answer token, fragmented tool-call arguments, truncated tool stream, invalid JSON, rate-limit errors, interrupted output, timeout, retry, missing usage, unusual context limit, missing cache counters.
- AIPerf result parser fixture tied to a pinned version; verify metric units and denominator before comparative reporting.
- Trial accounting asserting `successful_trials <= started_trials <= planned_trials`; track underlying HTTP attempts separately because retries can make `HTTP_attempts > started_trials`. Unexecuted/budget-skipped trials never count as passes.
- Reproducible output comparison: same fixture/manifest inputs yield the same normalized identity/hash (run timestamps excepted).

## Definition of done for any completed gate

1. Code + tests + small, accurate documentation change.
2. Offline tests and relevant lint/format checks pass; report exact commands and results.
3. No secrets or unlicensed AA data in source control.
4. Observable error states instead of silent fallback or fictional zeroes.
5. Result artifacts include provenance, UTC timestamp, version/config/fixture hashes, and test/feature support labels.
6. Note remaining blockers and **what was not run**. Never mark paid/high-context reliability qualified from smoke tests.

## Decisions to escalate rather than assume

- Access to AA paid individual benchmark scores or publication/redistribution rights.
- Any paid benchmark execution, increased concurrency, sustained load, or near-1M context test.
- Changing existing Mac mini services or agent credentials.
- Disputed model identity or AA variant mapping when it would misrepresent the model.
- Exposing private/codebase traces to third-party endpoints.

## Delivery format for implementation agents

At the end of each gate, report: (a) files changed, (b) implemented behavior, (c) test commands/results, (d) evidence artifact paths, (e) open blockers, and (f) exact next gate. Do not describe planned gates as already delivered.
