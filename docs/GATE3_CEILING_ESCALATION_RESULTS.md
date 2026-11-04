# Gate 3 ceiling escalation: 2048, 8192, 16384

**Version:** 0.2.0 (original 0.1.0 counters preserved; correction appended 2026-11-02)
**Datestamp:** 2026-11-02
**Source:** runs `run-3c188a0e4ce6`, `run-6cabf9e5ee70`, `run-6cabf9e5ee70-w1-c1`, and `run-9edf4133537c`; plans `pilot-10k-2048.json`, `pilot-10k-8192.json`, `multiwindow-10k-8192.json`, and `pilot-10k-16384.json`. Preserved artifacts are in `docs/gate3-pilot/`, `docs/gate3-multiwindow-8192/`, and `docs/gate3-pilot-16384/`.
**Status:** Escalation exhausted. A 16384-token ceiling still censored 1 of 20 responses, a reasoning-only stop at the cap. No fixed-cap multiwindow was run at 16384. No endpoint is qualified.
**Update rule:** Append a new version for any further ceiling run. Never edit recorded counters or metrics.

## Result

| Ceiling | Runs | Requests | Natural | Censored | Observed rate | One-sided 95% upper bound |
|---|---:|---:|---:|---:|---:|---:|
| 2,048 | `run-3c188a0e4ce6` | 20 | 13 | 7 | 35.0% | 55.8% |
| 8,192 | `run-6cabf9e5ee70` + `run-6cabf9e5ee70-w1-c1` | 40 | 38 | 2 | 5.0% | 14.9% |
| 16,384 | `run-9edf4133537c` | 20 | 19 | 1 | 5.0% | 21.6% |

Every censored response was reasoning-only: it spent the entire completion budget on provider-reported reasoning and emitted no visible tokens.

Doubling the ceiling from 8192 to 16384 did **not** reduce the observed censoring rate. The point estimate stayed at 5%, and each 20-request sample produced at least one response that was still reasoning at the cap. The 16384 natural completions ranged 260 to 10,851 tokens; one response exceeded 16,384. The tail moves with the cap rather than terminating below it.

This is the answer to the escalation question: a higher fixed cap is not a path to a zero-censoring multiwindow for this workload. It changes which requests are censored, not whether some are.

## The 16384 pilot in detail

| Field | Value |
|---|---|
| Requests / HTTP attempts | 20 / 20 |
| Natural / censored / failed | 19 / 1 / 0 |
| Natural completion lengths | 260 to 10,851 tokens, median 569 |
| Censored length | exactly 16,384 reasoning-only |
| TTFT avg / p50 / p95 | 763 / 760 / 1,100 ms |
| Request latency avg / p50 / p95 | 4,594 / 1,913 / 17,855 ms; censored for 1 trial |
| Output throughput | 561.9 tokens/s, reasoning-inclusive |
| Prompt tokens avg | 10,377 against 10,240 requested |
| Provider-versus-target | 20/20 verified |
| Engine duration | 92.0 s |
| Nominal cost | $0.124286 (cache-adjusted $0.078149) against a $0.75 guard |

The run used the same client, prompt set, and provider accounting as the 2048 and 8192 studies. The 16384 profile is a different workload identity; its evidence is not pooled with the other ceilings.

## Why the 240-request zero-censoring gate cannot be met

At the observed 5% rate, a 240-request experiment has an expected 12 censored trials. The probability of zero censoring is `0.95**240`, about `4.5e-6`. Even if the true rate were 1%, zero censoring in 240 has probability about 9%. A zero-censoring result in 240 trials would only bound the rate at 1.24% one-sided 95%; it would not demonstrate zero.

A 12-cell multiwindow at 16384 would have a `$8.18` guard (actual spend substantially lower), but its stop rule would almost certainly fire on the first few cells. Running it would spend money to confirm a conclusion already established by three samples.

## What this supports, and what it does not

Supported:

- A 5% reasoning-only tail persists at 8192 and 16384 for this prompt set.
- TTFT, capped throughput, provider conformance, and cache/reasoning telemetry are measurable at all three ceilings.
- The 2048 ceiling is clearly inadequate for completion measurement; 8192 and 16384 are still not censor-free.

Not supported:

- A completion-length distribution or a censor-free multiwindow sample.
- A claim that any higher fixed cap would eliminate the tail.
- Any qualification verdict. Thresholds remain unratified and the 1.1 contract remains scoped to 1024.

## Options, none authorized

1. **Censoring-tolerant analysis.** Use cap-hit times as per-trial lower bounds and evaluate bounded or survival statistics. This is the only path that can use the heavy-tailed data honestly. It requires a methodology and contract revision, not another paid run.
2. **Workload redesign.** Change the prompt set so reasoning terminates. That is a new benchmark identity, not a completion of this one.
3. **Declare fixed-cap completion-latency qualification out of scope.** Keep the standard-profile screening results for TTFT, throughput, and conformance; make no completion-latency claim.

No further paid run is authorized by this document. The escalation ladder is exhausted for the current workload.

## Interpretation correction (2026-11-02, version 0.2.0)

The observed counters above are preserved. This section narrows the conclusions that may be drawn from them:

1. **Finite samples.** The 8192 and 16384 point estimates are both 1/20 or 2/40 observations. They do not estimate a stable censoring probability and cannot establish that every higher fixed cap fails. The defensible statement is narrower: in this program, doubling the cap shifted which requests were censored rather than producing a zero-censoring sample. "Escalation exhausted" is a program decision about this workload and budget, not a mathematical impossibility result.
2. **Timeout.** These cells ran with a resolved 60-second request timeout, although the bounded plan declared 240 seconds. The runtime configuration is the evidence; the plan value must be reconciled and asserted before a future release.
3. **Bounded analysis.** The censoring-tolerant analysis contemplated in option 1 is now implemented in [`bounds.py`](../src/endpoint_bench/bounds.py) and [`analyze_bounded.py`](../scripts/analyze_bounded.py). It is a descriptive extension, not a contract revision and not a qualification verdict.
4. **No universal tail claim.** Any sentence that reads as "no fixed cap can ever work" should be read as scoped to the observed workload, deployment, and finite samples.
