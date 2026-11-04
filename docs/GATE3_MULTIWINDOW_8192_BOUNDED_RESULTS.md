# Gate 3 bounded multiwindow at 8192: completed

**Version:** 0.2.0 (original 0.1.0 counters preserved; correction appended 2026-11-02)
**Datestamp:** 2026-11-02
**Source:** plan [`multiwindow-10k-8192-bounded.json`](gate3-plans/multiwindow-10k-8192-bounded.json), 12 run directories under [`gate3-multiwindow-8192-bounded/`](gate3-multiwindow-8192-bounded/), and [`analysis.json`](gate3-multiwindow-8192-bounded/analysis.json). Method: [`bounds.py`](../src/endpoint_bench/bounds.py).
**Status:** All 12 cells executed: 240 requests, 233 natural completions, 7 ceiling truncations, 0 endpoint failures. Completion latency is reported as a lower bound. No threshold is ratified, so no qualification verdict is produced.
**Update rule:** Append a new version for any further run. Never edit recorded counters, metrics, or bounds.

## Workload scope

This historical dataset contains twelve 20-request cells at an 8,192-token output ceiling and concurrency 1 or 2. One earlier C=1 cell was reused with the same workload settings. Censored observations remain in the lower-bound analysis.

This experiment is outside the 1.1 contract's 1024-token scope and its `dq.truncation_rate` gate. It produces bounded descriptive evidence, not a contract verdict.

## Method

A ceiling-truncated response did not finish, but its cap-hit time is a rigorous per-trial lower bound: the response was still generating at that instant. If `L_i <= T_i` for every trial, every order statistic of the lower bounds is a lower bound on the corresponding order statistic of the true completion times.

- The pooled p50/p95 of the lower bounds are **lower bounds** on the true p50/p95.
- A lower bound above an upper-bound threshold proves failure; a lower bound below it stays inconclusive.
- The analysis never returns `qualified` from a lower bound.
- Censored trials are never dropped. Failed trials are counted and excluded; none occurred.

## Cells

| Window | C=1 natural / censored | C=2 natural / censored |
|---|---|---:|
| 1 | 18 / 2 (reused `run-6cabf9e5ee70-w1-c1`) | 20 / 0 |
| 2 | 20 / 0 | 20 / 0 |
| 3 | 19 / 1 | 20 / 0 |
| 4 | 19 / 1 | 18 / 2 |
| 5 | 19 / 1 | 20 / 0 |
| 6 | 20 / 0 | 20 / 0 |
| **Total** | **115 / 5** | **118 / 2** |

Every cell had 20 planned requests, 20 HTTP attempts, validated AIPerf schema 1.4, raw evidence present, and 20/20 provider-versus-target conformance. No retries, no endpoint failures.

Overall censoring was **7 of 240 (2.9%)**, one-sided 95% exact upper bound **5.4%**. At C=1 it was 5/120 (4.2%; upper bound 8.6%); at C=2 it was 2/120 (1.7%; upper bound 5.2%).

## Pooled bounded results

| Statistic | C=1 | C=2 |
|---|---:|---:|
| Sample | 120 | 120 |
| Natural / censored | 115 / 5 | 118 / 2 |
| `request_latency` p50 lower bound | **2,767 ms** | **2,826 ms** |
| `request_latency` p95 lower bound | **19,136 ms** | **21,541 ms** |
| `request_latency` mean lower bound | 5,042 ms | 5,171 ms |
| `time_to_first_token` p50 | 760 ms | 788 ms |
| `time_to_first_token` p95 | 1,506 ms | 1,665 ms |
| Verdict | inconclusive | inconclusive |

TTFT is exact, not bounded: it precedes any ceiling. The latency bounds are not completion-latency estimates; they are the smallest values consistent with the observed data. The p95 bounds are wide because a few responses were still reasoning at 8192.

The C=1 and C=2 p50 lower bounds differ by about 58 ms (2.1%). That is a descriptive difference between two pooled samples, not a tested concurrency effect, and no threshold exists to judge it.

## Tokens and cost

Across 240 requests: 2,490,516 provider input tokens, 326,387 completion tokens, 271,149 reasoning tokens, 55,238 visible output tokens, and 2,179,584 cache-read tokens. Cache read is telemetry, not a cache-benefit measurement; the repeated prompt set plausibly explains the high read count, which this design cannot isolate.

Client-side cost at the published rates: **$1.138819 nominal**, **$0.498022 cache-adjusted**. The plan's guard was $0.45 per cell ($5.40 total); actual spend was far below the conservative bound because most responses finished well under 8192 tokens.

## What this supports, and what it does not

Supported:

- The 10K workload can be executed across six windows and two concurrency levels on this deployment with no endpoint failures.
- The pooled p50 completion latency is at least about 2.77 s at C=1 and 2.83 s at C=2.
- TTFT is measured on all 240 trials, with p50 about 760 to 788 ms.
- The censoring rate under this workload and ceiling is about 3%, with a 5.4% upper bound.

Not supported:

- An exact completion-latency distribution or a natural-completion-only p50/p95.
- A qualification verdict. Thresholds are unratified, the contract remains at 1024, and this experiment is explicitly outside it.
- A concurrency effect, a capacity limit, a cache benefit, or a visible-answer throughput claim.
- Any claim that 8192 is an adequate ceiling; 7 trials still hit it.

## Artifacts

- [`analysis.json`](gate3-multiwindow-8192-bounded/analysis.json): pooled and per-window bounded statistics.
- [`runs.json`](gate3-multiwindow-8192-bounded/runs.json): cell-to-run mapping and counters.
- [`source_hashes.json`](gate3-multiwindow-8192-bounded/source_hashes.json): SHA-256 and size of each raw stream export.
- Each run directory holds `report.json`, `trials.jsonl`, and `aiperf-config.yaml`. Raw stream exports remain in the local `runs/` directory, identified by hash.

## Next

1. Ratify performance thresholds only from an operator requirement; the bounded statistics are ready to be compared against a p50/p95 objective when one exists.
2. If exact completion latency is required, the remaining option is a workload redesign that makes reasoning terminate. A higher fixed cap is not supported by the 16384 pilot.
3. No further paid run is authorized by this document.

## Interpretation correction (2026-11-02, version 0.2.0)

The version 0.1.0 counters and bounds above are preserved. This section corrects *reading* of the evidence; it does not rewrite a recorded value.

1. **Timeout.** The plan declares `request_timeout_s: 240`, but every generated `aiperf-config.yaml` and every report in this experiment records `60.0`. The runtime value that governed these requests is 60 s. The plan value is a declared intent, not runtime evidence. Resolve the CLI/config precedence and assert manifest/runtime equality before the next live release.
2. **Window chronology.** Window 1 C=1 ran from 07:41:00 to 07:42:24 UTC and was reused as window 1. The C=2 pair started at 07:57:15 UTC; windows 2 to 6 use ten-minute offsets from that start, with alternating order. This is exploratory reused evidence, not six uniformly paired fresh windows under a prospectively frozen plan. Publish each window separately; pooling is descriptive only.
3. **Bounded analysis hardening.** The original `scripts/analyze_bounded.py` accepted the same run directory twice and reported 40 trials, and `bounds.py` accepted NaN and a truncated record with a missing finish class. Both are fixed: paths and run/request identities are rejected on duplication, values must be finite and non-boolean, completion metadata must be consistent, and every input record is validated before a statistic. Re-deriving the 12 original cells after the fix reproduces the same denominators and bounds: 120 trials per level, C=1 2767.395/19135.735 ms and C=2 2825.736/21540.974 ms, TTFT p50 759.804/787.860 ms.
4. **Tail scope.** The 8192 cap still censored 7 of 240 responses. That is a measured result for this workload, not proof that no higher cap could ever reduce censoring. The 16384 pilot's 1/20 observation is also finite-sample. No universal fixed-cap infeasibility claim is supported.
5. **Deduplication.** The 12 run identities are distinct; documentation copies are not additional trials. [`HISTORICAL_RUN_INVENTORY.md`](HISTORICAL_RUN_INVENTORY.md) groups by artifact hash and records every historical entry as retrospective with unresolved authority. No historical cell is reused or relabelled as a D0003 window.
