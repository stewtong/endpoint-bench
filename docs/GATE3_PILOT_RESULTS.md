# Gate 3 pilot results: 10K, 2048 ceiling, C=1

**Version:** 0.1.0
**Datestamp:** 2026-11-01
**Source:** run `run-3c188a0e4ce6`, [`pilot-10k-2048.json`](gate3-plans/pilot-10k-2048.json), and decision [`0003`](decisions/0003-qualification-threshold-and-censoring-policy.md). Preserved artifacts are in [`gate3-pilot/run-3c188a0e4ce6/`](gate3-pilot/run-3c188a0e4ce6/).
**Status:** C=1 executed. The pilot stop rule failed on output-ceiling censoring, so C=2 was not executed and no further paid cell was started. No endpoint is qualified.
**Update rule:** Append a new version for any further pilot run. Never edit recorded counters or metrics; a changed interpretation gets a new document version.

## Workload scope

The pilot used the prepared 10K/2048-token workload with a 20-request cell and a $0.20 client estimated-cost guard. The measurements below are historical observations.

The pilot plan remained the exact pre-execution artifact; its `execution_authorized` field is still `false` because the checker requires prepared plans to stay unexecuted. The run report's cost preflight records the live authorization and the `$0.20` ceiling.

## Command

```bash
uv run endpoint-bench run \
  --config ~/.endpoint-bench/registry.live.json \
  --target tf-deepseek-v4.1-flash-1017 \
  --profile-file configs/profiles/pilot-10k-2048.json \
  --profile pilot-10k-2048 \
  --execute --authorize-live \
  --max-usd 0.20 --max-requests 20 --process-timeout 1200 \
  --token-accounting server \
  --run-dir runs
```

Engine: AIPerf 0.13.0, summary schema 1.4, validated. Client: baseline, no session affinity, no automatic retries, pooled connections, provider token accounting. Process completed with exit code 0 and no timeout. Engine duration was 220.1 s.

## Counters and completion

| Field | Value |
|---|---|
| Planned / started / HTTP attempts | 20 / 20 / 20 |
| Protocol completed | 20 |
| Natural completions (`stop`) | 13 |
| Ceiling truncations (`length`) | 7 |
| Failed / skipped / cancelled | 0 / 0 / 0 |
| Censored latency fraction | 0.35 |

All seven truncated trials spent the entire 2048-token completion budget on provider-reported reasoning and emitted zero visible tokens. Natural completions used 232 to 1,089 completion tokens (median 409); they are all below 2048. The truncated group is therefore not "slightly over the cap": it is a distinct reasoning-only subpopulation whose stopping point this run did not observe.

## Stop-rule evaluation

| Required condition | Observed | Result |
|---|---|---|
| Zero ceiling truncations across both runs | 7 truncations in C=1 alone | **fail** |
| Actual HTTP attempts equal planned requests | 20 = 20 | pass |
| Validated engine schema and raw evidence present | schema 1.4, raw export present | pass |
| Provider-versus-target conformance within tolerance | 20/20 verified, 0.03 to 3.00% | pass |

One required condition failed, so the plan's `on_failure` branch applies: stop, preserve artifacts, report, review censoring, budget and schedule, and version the plan before any further paid run. C=2 was not executed. No retry, trial replacement, or cap escalation was performed. The separate standard-profile suite was later run under its own versioned plan; it is not a pilot continuation and is recorded in [`GATE3_STANDARD_PROFILE_RESULTS.md`](GATE3_STANDARD_PROFILE_RESULTS.md).

## Token accounting and telemetry

- Accounting source: `provider_usage` for all 20 trials.
- `provider_vs_target`: 20 verified, 0 failed, 0 unverifiable. Provider input tokens ranged 10,243 to 10,547 against 10,240 requested.
- `client_vs_provider`: 20 `not_observed`. No client tokenizer ran, so tokenizer agreement is structurally unavailable in this mode.
- `claims_supported`: true for delivered-workload size, limited to provider-versus-target conformance.
- Cache read: 96,000 tokens (AIPerf normalized metric). Cache write: 110,336 tokens (raw streamed `usage.prompt_tokens_details.created_cache_tokens`). Both are telemetry, not a cache-benefit measurement.
- Reasoning tokens: 19,099. Visible output tokens: 2,210. Seven trials were reasoning-only.

## Observed metrics

| Metric | Value | Reading |
|---|---|---|
| Time to first token | avg 1,268 ms, p50 1,253 ms, p95 2,027 ms, n=20 | Observed on every trial; p95 is descriptive, not a calibrated percentile claim |
| Request latency | avg 10,868 ms, p50 7,235 ms, p95 23,902 ms, n=20 | **Right-censored** for 7 trials; not completion latency |
| Output throughput | 98.0 tokens/s | Counts reasoning and visible tokens together; not answer throughput |
| Prompt tokens | avg 10,377, range 10,243 to 10,547 | Provider-reported |
| Completion tokens | avg 1,065, max 2,048 | Max is the cap, not a natural length |

## Cost

The plan's preflight bound was **$0.165888** against a $0.20 ceiling. Using the published rates and observed usage:

- Nominal usage at $0.30/M input and $1.20/M output: **$0.087834**.
- Cache-adjusted estimate using the published $0.006/M cached-read rate: **$0.059610**.

These are client-side estimates, not an invoice. Cache-read discounting and rounding are provider-side behavior.

## What this supports, and what it does not

Supported:

- The endpoint accepts a requested 10,240-token workload and provider-reported input size conforms within 3.00%.
- Time-to-first-token was observed on all 20 trials.
- Provider cache-read, cache-write, and reasoning telemetry is available and preserved.
- Under a 2048-token ceiling, 35% of requests did not finish; that is a measured property of this workload and client configuration.

Not supported:

- Completion latency or completion-length distribution. Seven trials were censored.
- Ceiling adequacy. The stop rule failed, which is the opposite of adequacy.
- Concurrency behavior. C=2 was not executed.
- Visible-answer latency or throughput.
- Any qualification verdict. Pilot evidence is exploratory and excluded from future qualification evidence.

## Next

The next paid run must be a **new, versioned plan**, not an automatic cap increase. Two defensible options remain:

1. A ceiling study at a higher declared cap, sized to observe whether the reasoning-only subpopulation terminates, with a new exposure estimate and guard.
2. Proceed with the remaining profiles at their declared ceilings and publish censored latency explicitly, making no completion-latency claim.

Neither is authorized by this document. The pilot evidence does not select between them.
