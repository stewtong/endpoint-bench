# Qualification contracts

**Version:** 2.4 ;  **Datestamp:** 2026-11-03
**Source:** `10k-slo-contract.json` v1.1, [`../decisions/0003-qualification-threshold-and-censoring-policy.md`](../decisions/0003-qualification-threshold-and-censoring-policy.md) v1.1, and the corrected screening evidence.
**Update rule:** Keep this explanation aligned with the versioned contract. Distinguish reviewed direction, implemented behavior, and executed evidence. Do not restate a prepared experiment as evidence.

## What this directory contains

**Status:** optional future scope. Qualification is not a default done bar for the D0010 same-day tooling, and no threshold is ratified. Nothing here blocks acceptance of the same-day command or its designated results.

The active [draft contract](10k-slo-contract.json) scopes 10K delivered-workload performance at C=1 and C=2. It is a specification. A contract evaluator exists in `src/endpoint_bench/qualification.py`, but it is exercised only against synthetic evidence in `tests/test_qualification.py`; it is not wired to run reports, and no endpoint has been qualified or failed.

## Direction: characterization now

The current direction is **characterization**: publish measured behavior with scope, validity states, counts and limitations, and leave the verdict `inconclusive` because no performance threshold is ratified.

An operator can change that by supplying a requirement ;  metric, statistic, unit, inequality, workload, concurrency, client configuration, deployment, region, temporal scope, and the requirement's origin and date. Nothing in this repository invents one. A pass mark derived from the measurement it judges is the same circularity the token-sizing check was corrected for.

The synthetic evaluator now enforces that distinction mechanically. A non-null threshold is not ratification: the contract must declare `status: "ratified"`, `frozen: true`, a freeze timestamp, and a per-objective record naming origin, date, workload, concurrency, client profile, deployment, region and expiry. The record must also **apply**: its workload, client profile, deployment, region and concurrency are compared with the actual evidence and objective, its date/expiry window must contain the evaluation instant, and the freeze must precede evaluation without predating the requirement or following its expiry. Evaluation time is supplied explicitly for reproducible expiry checks. Before any ratio or statistic, counters must be finite nonnegative integers (not booleans) and observation values must be finite nonnegative numbers; successful, failed and truncated cohorts must reconcile to started trials. It also requires at least three evaluation windows per required concurrency level, unique window and trial identities, and one explicit workload/client/deployment/region cohort per pooled level. A level present in the evidence but absent from the contract's objectives is an error, not silently ignored. Real thresholds remain null, so the reachable state remains `inconclusive`.

Three questions map to three different contracts:

| Question | Use | Limitation |
|---|---|---|
| Does this endpoint meet a product requirement? | Operator requirement (option A) | Requires a real requirement to exist |
| Has it degraded against a frozen baseline? | Disjoint calibration (option B) | Answers regression only, not fitness for a product |
| What does it actually do? | Characterization (option C) | Makes no adequacy claim |

## Evaluation model: pooled performance, windowed validity

Version 1.0 of the contract required three passing windows per level, a 30-sample percentile floor, and p50/p95 objectives, while each window holds 20 requests. Those were mutually unsatisfiable: a 20-request window cannot support a p50 under the contract's own floor. Version 1.1 fixes this by separating the two roles:

- **A window is the unit of evidence validity.** Every data-quality gate is evaluated per window. A window passes when the evidence is trustworthy ;  never because performance looked good.
- **Performance objectives are evaluated on the pooled eligible per-trial sample** at one concurrency level, with identical workload, client configuration, deployment and region. Six windows give 120 pooled observations per level; four give 80. Both exceed the floor.
- **Per-window statistics are still published** so temporal drift stays visible. Per-window percentiles are never averaged into a pooled percentile, and a percentile objective is never evaluated on a single 20-request window.

A window that fails a data-quality gate makes the whole condition `inconclusive`. It is not averaged away.

## Data-quality gates and endpoint errors

Data-quality gates concern whether the evidence can be trusted. Genuine endpoint errors are a different thing: they are **outcomes**, reported under the ratified error-rate objective with count, rate, attribution and denominator, and they do not automatically invalidate a measurement because the endpoint performed poorly.

Latency is therefore published **conditioned on an explicitly stated eligible response population**, beside the error count and rate. A latency pass among successful responses alone cannot establish overall service readiness; if the intended requirement is "requests complete within a deadline," that is a joint objective and must be defined as one.

The eligible population must be derived from per-trial records. In the corrected screening evidence the engine summary `count` (20) exceeded the natural-completion count (2), so assuming `count == successful_trials` would misstate the population.

## Censoring

Unadjusted completion-latency evidence must be uncensored. Any output-ceiling truncation withholds natural-completion means and percentiles for that window, because a censored response's observed time is time-to-termination, not completion.

Ceiling censoring alone does not invalidate everything: observed time-to-termination, TTFT, token rates and the censoring count stay reportable with their existing metric-specific validity.

Bounded reporting (using cap-hit times as per-trial lower bounds) is implemented in [`bounds.py`](../../src/endpoint_bench/bounds.py) and exercised by [`analyze_bounded.py`](../../scripts/analyze_bounded.py). It sits outside this contract's verdict rules: it can prove an upper-bound objective is missed, but it never returns `qualified` from a lower bound. The p50 objective is compared against a p50 lower bound; a mean lower bound is not substituted for it.

## Evidence requirements

A run may only feed a verdict if it carries report and trial schema `0.2.0` and keeps its raw export. Schema `0.1.0` is rejected: it has no truncation counter and its `sizing_verified` field came from the circular comparison.

Pilot, calibration and exploratory evidence is excluded from qualification evidence permanently. It cannot be relabelled after thresholds are chosen.

## Experiment status

| Plan | Status |
|---|---|
| [`pilot-10k-2048`](../gate3-plans/pilot-10k-2048.json) | Executed C=1; stopped on 7/20 censoring; C=2 not run |
| [`multiwindow-10k`](../gate3-plans/multiwindow-10k.json) | Not executed; its 1024 ceiling is contradicted by the measured tail |
| [`pilot-10k-8192`](../gate3-plans/pilot-10k-8192.json) | Executed; 20/20 natural |
| [`multiwindow-10k-8192`](../gate3-plans/multiwindow-10k-8192.json) | First attempt stopped after 1 of 12 cells; 2/20 censored |
| [`pilot-10k-16384`](../gate3-plans/pilot-10k-16384.json) | Executed; 1/20 censored; program ceiling escalation stopped, not proven universally infeasible |
| [`multiwindow-10k-8192-bounded`](../gate3-plans/multiwindow-10k-8192-bounded.json) | All 12 cells executed; 240 requests, 233 natural, 7 censored; bounded analysis, no contract verdict |

The plans are separate identities: a different output ceiling is a different workload, so no plan replaces or mutates another. A clean pilot does not certify its ceiling; zero censoring in 20 trials leaves a wide upper bound on the censoring probability.

## Interpretation corrections (2026-11-02)

These are versioned readings of existing artifacts. Original counters and reports are unchanged.

1. **Timeout.** [`multiwindow-10k-8192-bounded.json`](../gate3-plans/multiwindow-10k-8192-bounded.json) declares `request_timeout_s: 240`, but every generated run configuration and report records `60`. The resolved runtime value is 60 s. The plan value is a declared intent, not evidence of the runtime timeout; the CLI/config precedence must be asserted at the next live release.
2. **Window chronology.** Window 1 C=1 began at 07:41 UTC and ended 07:42; it was reused as window 1. The C=2 pair began 07:57 UTC, and windows 2 to 6 use ten-minute offsets from that start with alternating order. This is exploratory reused evidence, not six uniformly paired fresh windows under a prospectively frozen plan. Each window is reported separately, and pooling is descriptive only.
3. **Ceiling-tail scope.** The 8192 fixed cap still censored 7 of 240 responses. That does not prove that no higher cap could reduce censoring for this workload; the 16384 pilot retained a 1/20 observed rate, but 20 trials cannot establish a universal tail. The escalation stop is a program decision, not a mathematical impossibility result.
4. **Deduplication.** Duplicate documentation copies are not additional trials. The reconstructed inventory at [`../HISTORICAL_RUN_INVENTORY.md`](../HISTORICAL_RUN_INVENTORY.md) groups by trial-artifact hash and marks every entry retrospective with unresolved authority.

## Bounded censoring extension (outside this contract)

The contract's `dq.truncation_rate` gate still treats any output-ceiling censoring as a data-quality failure. The measured workload did not cooperate with that gate: the 2048, 8192, and 16384 pilots censored 7/20, 2/40, and 1/20 responses, all reasoning-only stops at the cap. A zero-censoring 240-request multiwindow was not achieved in this program. That is a finite-sample, workload-specific observation; it is not a proof that no higher cap or different workload could ever achieve zero censoring.

`src/endpoint_bench/bounds.py` implements the extension that describes what censored evidence supports. A cap-hit time is a per-trial lower bound on completion latency; every order statistic of the lower bounds is a lower bound on the corresponding order statistic of the true times. A lower bound above an upper-bound threshold proves failure; a lower bound below it stays inconclusive; a lower bound never proves success. Censored trials are never dropped.

This is a descriptive method, not a contract revision. The contract's verdict remains `inconclusive`, its thresholds remain null, and the 1.1 scope remains 1024. The completed bounded multiwindow at 8192 is recorded in [`../GATE3_MULTIWINDOW_8192_BOUNDED_RESULTS.md`](../GATE3_MULTIWINDOW_8192_BOUNDED_RESULTS.md).

## Unresolved statistical choices

Recorded rather than guessed:

- Whether qualification describes the observed pooled sample or infers a population parameter, which determines sample size and uncertainty reporting.
- How temporal instability between windows should modify a pooled verdict.
- Who ratifies a requirement, and what record constitutes ratification.
- Whether a visible-answer throughput objective should exist, which needs reasoning tokens separated from content tokens per trial.
- Whether the real requirement is a joint success-within-deadline objective.

## Related

- [Decision 0003](../decisions/0003-qualification-threshold-and-censoring-policy.md) ;  reasoning behind the threshold-provenance and censoring questions, including the withdrawn claims from its own version 1.0.
- [Correction 1](../GATE3_RESULTS_CORRECTION_1.md) and [Correction 2](../GATE3_RESULTS_CORRECTION_2.md) ;  what was corrected and what remains unrecoverable.
