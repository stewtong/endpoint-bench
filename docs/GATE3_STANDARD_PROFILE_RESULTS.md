# Gate 3 standard profile results: 2026-11-01

**Version:** 0.1.0
**Datestamp:** 2026-11-01
**Source:** run IDs `run-6351f7471cea`, `run-4443906cc72b`, `run-5379290bed52`, `run-dede1f4a445d`; plan [`standard-profiles.json`](gate3-plans/standard-profiles.json); pilot result [`GATE3_PILOT_RESULTS.md`](GATE3_PILOT_RESULTS.md). Preserved artifacts are in [`gate3-standard-profiles/`](gate3-standard-profiles/).
**Status:** All four standard performance profiles executed once at their declared settings. No endpoint failures. Completion latency is heavily censored and supports no completion claim. No endpoint is qualified.
**Update rule:** Append a new version for any further profile run. Never edit recorded counters or metrics.

## Workload scope

The four standard profiles were each run once using the plans and client guards in [standard-profiles.json](gate3-plans/standard-profiles.json). These are finite historical observations.

This authorization did not extend to the pilot C=2 cell, the multiwindow qualification experiment, a raised output ceiling, or any qualification claim. The separate 10K pilot had already stopped on ceiling censoring; these profiles ran at their own declared ceilings as screening measurements, not as a continuation of that pilot.

**Scope note.** `long-context` and `reliability` are Gate 4 subjects. These are single-run screening measurements retained as historical observations. They do not satisfy Gate 4's multi-window reliability, effective-context sentinel, or capacity requirements, and they do not exit Gate 4.

## Runs

| Profile | Run | Requests | Concurrency | Input / output ceiling | Engine duration | Natural | Truncated | Failed |
|---|---|---|---:|---:|---:|---:|---:|---:|
| quick | `run-6351f7471cea` | 20 | 2 | 2,048 / 256 | 35.0 s | 1 | 19 | 0 |
| standard | `run-4443906cc72b` | 200 | 4 | 4,096 / 512 | 181.0 s | 40 | 160 | 0 |
| reliability | `run-5379290bed52` | 300 | 2 | 1,024 / 128 | 141.3 s | 0 | 300 | 0 |
| long-context | `run-dede1f4a445d` | 8 | 1 | 131,072 / 512 | 34.7 s | 7 | 1 | 0 |

All runs: AIPerf 0.13.0, summary schema 1.4, engine exit 0, planned requests equal actual HTTP attempts, no retries, no timeouts, provider token accounting, baseline client with no session affinity. All 528 provider-versus-target checks passed within the 10% tolerance: quick 0.54 to 3.91%, standard 0.02 to 4.22%, reliability 0.98 to 7.81%, long-context 0.68 to 1.65%.

## Per-profile observations

### quick: 2K input, 256 output, C=2

- 1 natural completion, 19 ceiling truncations (95%), 0 endpoint failures.
- 19 of 19 truncated responses were reasoning-only and emitted no visible answer.
- TTFT: avg 752 ms, p50 737 ms, p95 1,082 ms, n=20.
- Request latency: avg 3,039 ms, p50 2,751 ms, p95 4,275 ms, n=20. **Censored for 19 trials; these are time-to-cap values, not completion latency.**
- Output throughput: 158.5 tokens/s, counting reasoning and visible tokens.
- Prompt tokens: avg 2,102 against 2,048 requested.
- Completion tokens: avg 255.8; the maximum is the 256 cap. The one natural completion used 251 tokens, 121 of them visible.

### standard: 4K input, 512 output, C=4

- 40 natural completions, 160 ceiling truncations (80%), 0 endpoint failures.
- 119 of 160 truncated responses were reasoning-only.
- TTFT: avg 906 ms, p50 733 ms, p95 1,719 ms, n=200.
- Request latency: avg 3,535 ms, p50 3,378 ms, p95 5,620 ms, n=200. **Censored for 160 trials.**
- Output throughput: 545.6 tokens/s, reasoning-inclusive.
- Prompt tokens: avg 4,166 against 4,096 requested.
- Completion tokens: avg 485.8; p50 and p95 are at the 512 cap. The 40 natural completions used 245 to 508 tokens (median 391).

### reliability: 1K input, 128 output, C=2

- 0 natural completions, 300 ceiling truncations (100%), 0 endpoint failures.
- All 300 responses were reasoning-only and emitted zero visible tokens.
- TTFT: avg 623 ms, p50 587 ms, p95 965 ms, n=300.
- Request latency: avg 918 ms, p50 860 ms, p95 1,221 ms, n=300. **Fully censored; this is time-to-cap.**
- Output throughput: 277.2 tokens/s, reasoning-inclusive.
- Prompt tokens: avg 1,064 against 1,024 requested.
- Completion tokens: exactly 128 on every trial.

This run is a valid TTFT and throughput measurement under a 128-token cap. It is not a reliability measurement of successful requests, because no request succeeded.

### long-context: 131K input, 512 output, C=1

- 7 natural completions, 1 ceiling truncation (12.5%), 0 endpoint failures.
- TTFT: avg 3,298 ms, p50 3,251 ms, p95 3,951 ms, n=8.
- Request latency: avg 3,979 ms, p50 3,858 ms, p95 4,747 ms, n=8. **Censored for 1 trial.**
- Output throughput: 93.2 tokens/s, reasoning-inclusive.
- Prompt tokens: avg 132,689 against 131,072 requested, range 131,957 to 133,229 (+0.68 to 1.65%).
- Completion tokens: avg 371; one trial hit 512. The 7 natural completions used 263 to 483 tokens (median 340).

The endpoint accepted and served a requested 131,072-token input on all eight attempts, and provider-reported input size conformed. This is **accepted-context evidence, not demonstrated effective context**: no sentinel checks were run, and an HTTP 200 does not prove the model attended to the full input.

## Cross-profile totals

Including the 20-request pilot C=1 cell for context:

| Measure | Standard profiles (528) | Pilot C=1 (20) | Total (548) |
|---|---:|---:|---:|
| Natural completions | 48 | 13 | 61 |
| Ceiling truncations | 480 | 7 | 487 |
| Endpoint failures | 0 | 0 | 0 |
| Truncated fraction | 90.9% | 35.0% | 88.9% |

Token totals across all 548 requests: 2,463,530 provider input tokens, 164,957 completion tokens, 151,831 reasoning tokens, 13,126 visible output tokens. Cache telemetry: 238,592 cache-read tokens and 2,197,248 cache-write tokens.

The completion-length pattern is bimodal rather than a single distribution. Natural completions cluster just below each configured cap (quick 251 of 256; standard 245 to 508 of 512; long-context 263 to 483 of 512), while every truncated response is reasoning-only with zero visible output. This is consistent with two behaviors: a short visible answer, or reasoning that does not terminate inside the budget. It is not evidence that a single higher cap would make every request complete.

Cost estimate at the published rates:

- Standard profiles, nominal usage: **$0.849174**; cache-adjusted: **$0.807252**.
- Pilot C=1, nominal usage: **$0.087834**; cache-adjusted: **$0.059610**.
- Total nominal: **$0.937008**; total cache-adjusted: **$0.866862** against **$1.85** in run guards.

These are client-side estimates, not invoices.

## What this supports, and what it does not

Supported:

- The endpoint accepted requested input sizes from 1,024 to 131,072 tokens, with provider-versus-target conformance within 7.82% across all runs.
- TTFT was observed on every one of the 548 requests.
- Throughput under each configured output ceiling is measurable; it counts reasoning tokens as well as visible tokens.
- Provider cache-read, cache-write, and reasoning telemetry is available on every run.
- No endpoint or transport failures occurred in 548 requests.

Not supported:

- Completion latency. 487 of 548 responses (88.9%) stopped at the configured ceiling; their latency is right-censored.
- Visible-answer throughput. Most truncated responses emitted no visible content.
- Ceiling adequacy. The low-ceiling profiles confirm the opposite.
- Capacity, sustained-load stability, cache benefit, effective context, or any qualification verdict.
- Comparisons between profiles: input size, output ceiling, concurrency, and request count all differ. The numbers are not a controlled sweep.

## Completion audit against "all performance benchmarks"

| Benchmark | Executed? | Evidence |
|---|---|---|
| Screening 1K, 10K, 32K at C=1 and C=2 | yes, six cells | [`GATE3_RESULTS.md`](GATE3_RESULTS.md), Correction 1/2 |
| Live smoke | yes | [`gate3-corrections-2/run-34efa7f73ac1/`](gate3-corrections-2/run-34efa7f73ac1/) |
| quick | yes, this document | `run-6351f7471cea` |
| standard | yes, this document | `run-4443906cc72b` |
| reliability | yes, this document | `run-5379290bed52` |
| long-context | yes, this document | `run-dede1f4a445d` |
| Pilot 10K / 2048 / C=1 | yes, stopped | [`GATE3_PILOT_RESULTS.md`](GATE3_PILOT_RESULTS.md) |
| Pilot 10K / 2048 / C=2 | not executed, superseded | the 8192 and 16384 ceiling studies answered the completion-length question |
| Pilot 10K / 8192 / C=1 | yes, clean | [`GATE3_MULTIWINDOW_8192_RESULTS.md`](GATE3_MULTIWINDOW_8192_RESULTS.md) |
| Multiwindow 10K / 8192 (12 cells) | stopped after 1 cell | 2 of 20 censored; [`GATE3_MULTIWINDOW_8192_RESULTS.md`](GATE3_MULTIWINDOW_8192_RESULTS.md) |
| Pilot 10K / 16384 / C=1 | yes, stopped | 1 of 20 censored; [`GATE3_CEILING_ESCALATION_RESULTS.md`](GATE3_CEILING_ESCALATION_RESULTS.md) |
| Multiwindow 10K / 16384 (12 cells) | not needed | the 8192 bounded multiwindow completed with the same workload |
| Multiwindow 10K / 8192 bounded (12 cells) | yes | 240 requests, 233 natural, 7 censored; [`GATE3_MULTIWINDOW_8192_BOUNDED_RESULTS.md`](GATE3_MULTIWINDOW_8192_BOUNDED_RESULTS.md) |
| Qualification 10K / 1024 multiwindow (12 cells) | no | superseded; the fixed-cap zero-censoring design is infeasible |
| Cache scenarios | no | not implemented |
| AA reference sync | no | out of scope for this goal |

The standard performance profile suite and the 12-cell bounded multiwindow are complete. The bounded run executed all 240 requests, retained all 7 censored trials as lower bounds, and reports pooled p50/p95 lower bounds at C=1 and C=2. It does not produce a qualification verdict: thresholds remain unratified and the 1.1 contract is still scoped to 1024. The fixed-cap zero-censoring multiwindows were not run because the measured 5% reasoning tail persisted at 8192 and 16384.

## Next

1. Ratify performance thresholds only from an operator requirement. The bounded p50/p95 lower bounds are ready to compare against a real p50/p95 objective; none exists.
2. If exact completion latency is required, a workload redesign is the remaining option. The 16384 pilot showed that a higher fixed cap does not remove the tail.
3. Do not run the 1024-token fixed-cap multiwindow; it is superseded by the completed bounded experiment.
4. No further paid run is authorized by this document.
