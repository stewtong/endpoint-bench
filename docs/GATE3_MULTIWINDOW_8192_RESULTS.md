# Gate 3 multiwindow at 8192: stopped on censoring

**Version:** 0.2.0 (original 0.1.0 counters preserved; correction appended 2026-11-02)
**Datestamp:** 2026-11-02
**Source:** pilot `run-6cabf9e5ee70`, multiwindow cell `run-6cabf9e5ee70-w1-c1`, plans [`pilot-10k-8192.json`](gate3-plans/pilot-10k-8192.json) and [`multiwindow-10k-8192.json`](gate3-plans/multiwindow-10k-8192.json). Preserved artifacts are in [`gate3-multiwindow-8192/`](gate3-multiwindow-8192/).
**Status:** The 8192 pilot was clean, but the first multiwindow cell censored 2 of 20 responses. The multiwindow stop rule fired after cell 1 of 12; C=2 and windows 2-6 were not executed. No endpoint is qualified.
**Follow-up:** The 16384 pilot also censored 1 of 20, so escalation did not remove the tail. See [`GATE3_CEILING_ESCALATION_RESULTS.md`](GATE3_CEILING_ESCALATION_RESULTS.md).
**Update rule:** Append a new version for any further run. Never edit recorded counters or metrics.

## Workload scope

This historical experiment used an 8,192-token output ceiling and a proposed six-window repeat design. The results below retain the attempted cells and their observed censoring.

The multiwindow plan is explicitly outside the 1.1 contract's 1024-token scope. It is exploratory characterization, not a qualification cell, and its evidence is excluded from that contract's evaluation sample.

## Runs

| Run | Requests | C | Input / ceiling | Natural | Truncated | Failed | Duration |
|---|---:|---:|---:|---:|---:|---:|---:|
| Pilot `run-6cabf9e5ee70` | 20 | 1 | 10,240 / 8,192 | 20 | 0 | 0 | 58.3 s |
| Multiwindow cell `run-6cabf9e5ee70-w1-c1` | 20 | 1 | 10,240 / 8,192 | 18 | 2 | 0 | 87.5 s |

Both runs: AIPerf 0.13.0, summary schema 1.4, exit 0, planned requests equal actual HTTP attempts, no retries, provider token accounting, baseline client. Provider-versus-target conformance passed 40/40 within 10%.

Client-side cost at the published rates: **$0.089443** for the pilot and **$0.108621** for the multiwindow cell, **$0.198064** total nominal. Cache-adjusted estimates are $0.064944 and $0.065307. Each run had a $0.45 guard. These are estimates, not invoices.

## The pilot was not a ceiling certificate

The pilot's 20/20 natural completions (282 to 3,563 tokens) looked clean. The next 20 requests at the identical ceiling produced 2 ceiling truncations, both at exactly 8,192 reasoning-only tokens with zero visible output. Natural completions in the second cell were 287 to 4,237 tokens (median 603).

Combined: **2 censored in 40 requests (5.0%)**. The one-sided 95% exact binomial upper bound on the censoring probability is **14.9%**. The data are therefore consistent with a censoring rate far above zero.

This matters for the plan's stop rule. A 12-cell, 240-request experiment requires **zero** censored trials to continue. If the true censoring probability were 5%, the expected number of censored trials in 240 requests is 12. Even a zero-censoring outcome in 240 trials would only bound the rate at 1.24% one-sided 95%; it would not establish zero. The zero-censoring gate is not a feasible design for a heavy-tailed reasoning distribution at this ceiling.

The per-cell observations:

| Metric | Pilot | Cell 1 |
|---|---|---|
| Natural / truncated | 20 / 0 | 18 / 2 |
| Completion tokens, natural | 282 to 3,563 | 287 to 4,237 |
| TTFT avg / p50 / p95 | 781 / 734 / 1,065 ms | 738 / 724 / 1,071 ms |
| Request latency avg / p50 / p95 | 2,778 / 2,096 / 6,813 ms | 4,234 / 1,711 / 14,501 ms; censored |
| Output throughput | 407.2 tokens/s | 455.7 tokens/s |
| Prompt tokens avg | 10,377 | 10,377 |

Latency in cell 1 is right-censored for the two truncated trials. The mean and p95 are not completion-latency estimates.

## Harness defect found and fixed

The first attempt to run the multiwindow failed with `run directory already exists: runs/run-6cabf9e5ee70`. Repeated identical cells produce the same AIPerf config hash, so the run ID was identical even though the cells are separate observations. The immutable-run guard correctly refused to overwrite the pilot.

This was a real blocker for every repeated-measurement plan, including the original 1024 multiwindow. It is fixed by an optional `--run-label` that becomes part of the run ID (`run-<config-sha>-<label>`), is validated against path separators and reserved forms, and is covered by tests. The AIPerf configuration and its hash are unchanged; only the run identity changes.

## Stop-rule evaluation

| Required condition | Observed | Result |
|---|---|---|
| Zero ceiling truncations in the cell just completed | 2 in cell 1 | **fail** |
| Actual HTTP attempts equal planned | 20 = 20 | pass |
| Validated engine schema and raw evidence | pass | pass |
| Provider-versus-target conformance within tolerance | 20/20 verified | pass |

The plan's `on_failure` branch applies: stop the sequence, preserve artifacts, do not retry, replace trials, raise the ceiling, or continue to make the sample look complete. C=2 and windows 2-6 were not executed.

## What this supports, and what it does not

Supported:

- 8192 is not a reliable ceiling for a zero-censoring 10K workload with this prompt set. The observed censoring rate is 5% over 40 requests with a 14.9% one-sided upper bound.
- The 8192 pilot's clean result was a small-sample outcome, not evidence of ceiling adequacy.
- TTFT remains measurable under this ceiling; the two censored trials do not invalidate it.
- The run-label defect is fixed and tested.

Not supported:

- A completion-latency distribution or a multiwindow qualification sample.
- A claim that a higher ceiling would eliminate censoring. The tail is not bounded by these observations.
- Any qualification verdict. Thresholds are still unratified and the contract's scope is still 1024.

## Options, none authorized

1. **Higher ceiling.** A new versioned pilot at 16,384 could test whether the two outliers terminate. It cannot establish zero censoring, and a further failure would repeat the same escalation question.
2. **Censoring-tolerant analysis.** Use cap-hit times as lower bounds and evaluate survival or bounded statistics. This was deferred because no ratified threshold consumes it, and a mean lower bound cannot answer a p50 objective.
3. **Workload redesign.** Change the prompt set so reasoning terminates before the cap. That is a new workload identity and a new benchmark, not a completion of this one.
4. **Declare completion-latency qualification out of scope.** Keep the standard-profile screening results, which are valid for TTFT, throughput and conformance, and stop claiming a completion benchmark is achievable at a fixed cap.

No option is authorized by this document. The zero-censoring multiwindow cannot be completed as designed with the evidence available.

## Interpretation correction (2026-11-02, version 0.2.0)

Original counters are preserved. This section records later corrections and does not change the stop decision.

1. **Chronology.** The window-1 C=1 cell above ran 07:41 to 07:42 UTC and was later reused in the bounded experiment. The bounded C=2 window-1 pair started 07:57 UTC, and later windows used ten-minute offsets from it. The bounded set is exploratory reused evidence, not six uniformly paired fresh windows. See [`GATE3_MULTIWINDOW_8192_BOUNDED_RESULTS.md`](GATE3_MULTIWINDOW_8192_BOUNDED_RESULTS.md).
2. **Timeout.** The plan declares 240 s, while the generated runtime configuration and report record 60 s. The runtime value governed the requests. The declarative/runtime mismatch must be fixed and asserted before a future release.
3. **Bounded analysis.** Option 2 is no longer deferred: [`bounds.py`](../src/endpoint_bench/bounds.py) and [`analyze_bounded.py`](../scripts/analyze_bounded.py) implement lower-bound analysis and publish all denominators. This is descriptive, not a contract revision or qualification verdict.
4. **Tail scope.** "A higher ceiling would eliminate censoring" remains unsupported, but the converse universal claim ("no ceiling can ever eliminate it") is also unsupported by these finite samples. See [`GATE3_CEILING_ESCALATION_RESULTS.md`](GATE3_CEILING_ESCALATION_RESULTS.md).
