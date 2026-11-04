# 0003: Qualification threshold provenance and censoring policy

**Doc version:** 1.1
**Date:** 2026-11-01
**Status:** Reviewed direction and continuation handoff. The owner requested that the review recommendation, reasoning, instructions, and validation steps be recorded here. Contract/profile/plan changes and paid execution have not been performed by this update.
**Source:** software 0.1.2; corrected screening evidence in [`../gate3-corrections-2/`](../gate3-corrections-2/); [`../qualification/10k-slo-contract.json`](../qualification/10k-slo-contract.json); [`../gate3-plans/multiwindow-10k.json`](../gate3-plans/multiwindow-10k.json); review of reporting code, methodology, and Correction 1/2 on 2026-11-01.
**Update rule:** Re-read current sources before implementation; preserve concurrent work and historical evidence. Bump version/date for substantive changes. Distinguish reviewed direction, implemented behavior, and executed evidence. Replace a recommendation with reasoning that addresses its decisive tradeoff.

## Direction to carry forward

Publish characterization until an operator supplies a scoped requirement. Keep uncensored evidence as the current prerequisite for natural-completion latency qualification. Prepare a separately identified exploratory pilot to assess ceiling adequacy. Add metric-specific bounds only when persistent censoring and a concrete decision justify them. Freeze thresholds and evaluation rules before collecting designated qualification evidence.

| Question | Direction | Reason |
|---|---|---|
| Threshold provenance | Decision 1 C now; A when a real requirement exists | There is no ratified product requirement. Useful measurements do not require an invented pass mark. |
| Censoring | Decision 2 A for the next experiment; defer C | There is no ratified threshold for bounds to evaluate, and a better ceiling may make additional machinery unnecessary. |
| Errors and latency | Report conditional latency beside error rate; evaluate the objectives separately | Genuine endpoint errors are outcomes, not automatic evidence invalidation. |
| Next experiment | Prepare a separate 2048-token exploratory pilot | The 256-token screening evidence cannot establish a suitable ceiling. Preparation does not authorize execution. |
| Qualification | Separate future evidence under a frozen contract | Pilot, calibration, and exploratory repeats must not be relabelled as qualification after thresholds are chosen. |

## Established evidence and its limits

Corrected screening matrix: six cells, 20 requests each, 256-token output ceiling.

| Input | C | Naturally completed | Ceiling-truncated | Censoring rate |
|---|---|---|---|---|
| 1K | 1 | 0 | 20 | 100% |
| 1K | 2 | 0 | 20 | 100% |
| 10K | 1 | 0 | 20 | 100% |
| 10K | 2 | 0 | 20 | 100% |
| 32K | 1 | 1 | 19 | 95% |
| 32K | 2 | 2 | 18 | 90% |
| **Total** | | **3** | **117** | **97.5%** |

The three natural completions used 217, 222, and 249 completion tokens. They are a selected subset and do not identify the length tail above 256 or justify a particular higher ceiling.

Screening request accounting was exact (120 planned, 120 attempts); provider-versus-target discrepancies were 1.16% / 1.34% / 3.99%, within the 10% tolerance; raw evidence and validated engine schemas were present. Provider accounting is permitted with explicit provenance, without establishing independent tokenizer agreement. These checks do not satisfy the missing sample, temporal, threshold, or uncensored-completion requirements for qualification.

**Correction to version 1.0 of this decision:** the historical published latency mean was not computed only from the three natural completions. Correction 1 states that AIPerf summary means were unchanged; capped responses contributed to them. For example, `gate3-corrections-2/run-749ca85ae6c4/report.json` records two natural completions, 18 truncations, and `metrics.request_latency.count: 20`. The defect was interpreting time to termination as natural completion latency. Do not repeat the three-of-120 mean claim.

The corrections directory also includes a two-request smoke run. Select the six screening profiles explicitly when reproducing the 120-trial matrix; do not aggregate all seven runs. Correction 2 records the smoke run as zero successes and two truncations.

## Decision 1: threshold provenance

### Options and decisive tradeoff

**A. Operator requirement:** a responsible operator supplies a requirement with metric, statistic, units, inequality, workload, concurrency, client configuration, deployment/region, temporal scope, and origin/date. This can support a readiness claim once evidence is adequate. No such requirement currently exists in the contract.

**B. Disjoint calibration:** a frozen historical baseline plus a declared tolerance can support a regression question: “Has this endpoint degraded relative to that baseline?” It does not establish fitness for a product. Define the calibration population, derivation, tolerance rationale, uncertainty policy, and future evaluation evidence before examining the evaluation data. Preserve calibration data for provenance, but exclude it from qualification evidence under this design.

Version 1.0 overstated the case against B. Independent calibration is not inherently circular. Its meaning is narrower than product readiness. Multiplying a measured p95 by 1.2 and rounding does not supply product justification or remove calibration uncertainty. Two calibration windows yield 40 requests per concurrency level and leave 80 for evaluation, a one-third reduction from 120, not a halving. That is thin tail evidence; adequacy depends on the claim and precision required.

**C. Characterization:** publish measured behavior with scope, validity, counts, and limitations; keep qualification `inconclusive` because thresholds are absent. This is the direction now. A future evaluator can be tested with explicitly synthetic contracts and evidence without claiming any live endpoint qualified. Lack of a product requirement does not prevent software validation.

The decisive tradeoff is the meaning of the claim. Use A for requirements-based readiness, B for an explicitly identified regression contract, and C when neither question has been defined. Do not invent thresholds to exercise the qualification path or complete a milestone.

## Decision 2: censoring

### Current policy

Retain zero ceiling truncation for natural-completion latency qualification. Preserve observed time-to-termination, TTFT, token rates, and censoring counts with their existing metric-specific validity. Ceiling censoring alone does not invalidate TTFT observations. Do not broaden the gate into a claim that all metrics or all percentiles become unusable.

A mean over natural completions describes a selected subset. It cannot stand in for the full completion distribution. Longer output often takes longer, but queueing and emission speed also vary; do not claim that selection by token length guarantees a particular latency bias in every dataset.

Zero censoring is conservative, not “unsatisfiable by design.” If the censoring probability is positive, larger samples are more likely to contain a censored response. Zero observed censoring can still occur and does not establish a zero population censoring rate.

### Deferred bounded reporting

For a fixed response trajectory interrupted by the cap, the observed cap-hit time is a lower bound on that trajectory's completion time. With exact natural times and censored lower bounds, averaging or taking the same monotone sample quantile yields a lower bound on the corresponding full-sample statistic.

State the trajectory assumption. This argument does not automatically bound a separate higher-cap execution: a changed request may change generation behavior or serving conditions. It also does not turn a deterministic sample bound into a confidence bound for a population SLO.

Bounded reporting remains a useful extension, but implement it only if persistent censoring and a concrete decision justify it. A pilot truncation triggers review, not automatic implementation or cap escalation. Before implementation:

1. Match the bound to the objective. The current completion objective is p50 request latency; a `mean_lower` cannot be compared to a p50 threshold. Document the quantile estimator and denominator.
2. Require valid per-trial observations and clear treatment of failures, missing times, cancellations, and censoring. Never silently shrink the denominator.
3. Separate sample-statistic bounds from population uncertainty. Define exactly which claim a bound may fail and the evidence policy for that verdict.
4. Scope validity and verdict effects per objective. Keep uncensored TTFT usable and do not let missing unrelated evidence erase a trustworthy observation. Any change to global verdict precedence requires an explicit contract change and tests.
5. Preserve zero censoring as the current completion-latency qualification policy. A later policy may support some quantiles despite censoring; blanket mathematical claims that censoring forbids every quantile or success are too strong. A mean has no finite upper bound when a censored duration is unbounded, but some sample quantiles can have finite bounds.

There is no reason to implement these changes now merely to enable a failure against thresholds that remain null.

## Errors and conditional latency

Use the reporting fix: publish latency conditioned on the stated eligible response population alongside endpoint error count/rate, attribution, and denominator for each window and concurrency level. Do not make every nonzero endpoint error rate automatically invalidate latency observations.

Inspect actual engine aggregation before assigning a scope label. The capped screening reports demonstrate that summary count is not necessarily `successful_trials`; future failure-containing runs need the same audit. TTFT observed before a later stream failure may have a different eligible population from natural-completion latency. Derive the scope from actual records or withhold an unsupported interpretation.

A future qualification verdict must satisfy both the latency objectives and the ratified error objective. A latency pass among successful responses alone cannot establish overall service readiness. If the intended requirement is “requests successfully complete within a deadline,” define that joint objective explicitly rather than implying it from a conditional latency statistic.

## Sample and window rules requiring resolution

The draft uses 20 requests per window per concurrency level, prohibits percentile claims below 30, requires evaluating windows before pooling, and requires three passing windows per level. It does not explain how a 20-request window can pass its p50/p95 objectives.

Resolve this before collecting qualification evidence. The recommended approach is to define pooled percentile objectives for identical settings, with separate per-window evidence-validity and temporal-stability checks. State precisely what a “passing window” means and how a bad window affects the verdict. Alternatively increase per-window samples if the requirement truly demands percentile compliance in every window. Do not choose the semantics after seeing results, and do not reduce the floor merely to make the existing plan pass.

The 30-sample floor is a guardrail, not proof of p95 precision. Define whether qualification means descriptive compliance over the observed sample or inference about a population, then choose sample sizes and uncertainty rules accordingly. Do not average window percentiles to obtain a pooled percentile; pool eligible per-trial observations.

## Continuation instructions

This is one handoff, not a paid-run authorization. Read repository `AGENTS.md`, `BENCHMARK_PLAN.md`, `IMPLEMENTATION_TASKS.md`, and current sources before changes. Re-read the working tree and preserve the existing smoke correction and other concurrent edits. Do not commit, push, publish, or send paid traffic solely because this record describes future work.

1. **Align the contract and explanations.** Update `docs/qualification/10k-slo-contract.json` and `docs/qualification/README.md` for characterization now, conditional latency/error reporting, calibration as a separate regression use case, and explicit pooled/window evaluation semantics. Keep all unratified thresholds null. Record unresolved statistical choices rather than inventing product requirements. Update owning methodology/schema/task documentation only where behavior or claims require it.
2. **Prepare the exploratory pilot.** Recommend a separately named profile/plan for 40 requests total: 20 at C=1 and 20 at C=2, 10,240 requested input tokens, 2048-token output ceiling. Keep the existing 1024-token prepared experiment as a distinct identity; do not silently mutate it into a new workload. Remove any claim that three selected natural lengths establish ceiling adequacy. Verify current supported CLI syntax rather than copying a speculative live command.
3. **Recompute exposure and guards.** Use the owning estimator and verify pricing before any live run. The historical planning arithmetic at input $0.30/M, output $1.20/M, and 1.5× multiplier is $0.165888 per 20-request cell, $0.331776 for the pilot, and $1.990656 for 12 cells. These are planning estimates, not current-price verification or provider-enforced limits. The existing $0.15 cell guard is below the 2048 estimate and must not be reused. Check input/output totals, concurrency, request limits, timeouts, process deadline, schedule, and zero retries.
4. **Keep the stop rule.** Stop after the pilot on any ceiling censoring or invalid evidence; preserve artifacts. Report endpoint errors as outcomes and review their effect on continuation explicitly. Do not retry, replace trials, escalate the cap automatically, or merge runs with different ceilings. A clean pilot permits consideration of an authorized exploratory continuation, not automatic qualification.
5. **Separate future qualification.** Freeze scoped thresholds and evaluation rules before new designated qualification windows. Exclude pilot/calibration/exploratory evidence from that role. Even ratifying thresholds before the remaining exploratory windows is insufficient unless a new qualification design is explicitly frozen first.
6. **Defer bounds and evaluator expansion.** Implement only what is needed for this handoff's contract/reporting consistency and pilot preparation. No live qualification evaluator exists today. If one is later added, document its implementation separately from evidence that an endpoint qualifies. Revisit bounds after the pilot only when they answer a concrete question.

A clean 40-request pilot cannot certify the ceiling. Under independent identically distributed censoring indicators, zero censorings gives a one-sided 95% exact upper bound `1 - 0.05**(1/40)`, about 7.2%, on the censoring probability. Dependence and temporal drift limit that model. The illustrative 1,200-token “headroom” figure from version 1.0 was not a validated acceptance rule and is withdrawn.

## Validation and completion bar

### Validate this document update

- Reproduce the six-cell 3-natural / 117-truncated / 120-trial totals using explicit screening-profile selection, excluding the smoke run.
- Confirm the counterexample report has two successes but 20 latency observations and that Correction 1 says summary means were unchanged.
- Check local Markdown links, cost arithmetic, the pilot confidence-bound arithmetic, and absence of contradictory old recommendations.
- Inspect the final diff and verify that this update changes only Decision 0003. Report performed checks separately from follow-on checks below; do not claim implementation or execution.

### Validate follow-on changes before handoff

Use the existing local environment and offline inputs; do not install global packages or access provider credentials. Start with the nearest relevant checks:

```sh
uv run --offline python scripts/check_plan_contract.py
uv run --offline pytest tests/test_measurement_integrity.py tests/test_dry_run.py -q
uv run --offline ruff check .
```

Inspect test filenames before execution; adjust if the repository changes. Update the consistency checker to validate any new pilot profile/plan explicitly, including requested output-token totals, estimator-derived exposure, adequate cell guards, authorization remaining false, and cross-document scope consistency. Passing the existing checker alone does not validate the new pilot, window semantics, or a qualification verdict.

Add meaningful regression cases for implemented changes:

- An unratified required threshold cannot yield `qualified`; exploratory/pilot evidence cannot silently become qualification evidence.
- A 20-request window cannot claim p95 under the existing floor. Pooled percentiles use compatible per-trial records and preserve per-window counts and failures.
- A ceiling-truncated response remains separate from success and endpoint failure; natural-completion statistics remain withheld while independently observed TTFT survives.
- A successful-response latency result accompanied by endpoint errors retains explicit conditional scope/counts and the error denominator. An all-error group has no invented completion latency.
- A revised pilot's dry-run makes zero inference requests, records 2048 as the actual output ceiling, and refuses an insufficient guard. Preserve one attempt per trial and explicit timeouts.

Use the repository's existing reprocessing validation if normalization/reprocessing changes; preserve original evidence hashes. Run broader offline verification when code changes warrant it. Inspect `scripts/verify.sh` before using it: it syncs dependencies, launches a loopback mock, and clears named `/tmp` scratch directories. It is not necessary merely for this prose update.

**Completion bar for the continuation:** aligned versioned documents; an inspectable offline pilot manifest/plan with consistent exposure and guards; relevant checks passed with exact commands/results; no invented thresholds; no new endpoint qualification claim; original evidence unchanged; remaining statistical choices explicit; no paid requests without separate authorization. Stop with a reviewable prepared experiment, not an implied live-run approval.

## Revision history

- **1.0 (2026-11-01):** Proposed external requirements with characterization fallback and bounded censoring reporting. Included the historical three-natural-completion mean error, an overstatement against independent calibration, and an unconditional bounded-reporting recommendation.
- **1.1 (2026-11-01):** Recorded the review direction: characterization now, zero-censoring policy retained, bounds deferred, conditional latency/error reporting, window-rule reconciliation, separate pilot preparation, and concrete continuation/validation instructions. Corrected historical and statistical claims. No contract/profile/plan/code or paid-execution change is implied.

## References

- [`../qualification/10k-slo-contract.json`](../qualification/10k-slo-contract.json) and [`../qualification/README.md`](../qualification/README.md) ;  current draft, aggregation and verdict rules; no evaluator implemented.
- [`../GATE3_RESULTS_CORRECTION_1.md`](../GATE3_RESULTS_CORRECTION_1.md) and [`../GATE3_RESULTS_CORRECTION_2.md`](../GATE3_RESULTS_CORRECTION_2.md) ;  corrected findings and evidence limits.
- [`../gate3-corrections-2/run-749ca85ae6c4/report.json`](../gate3-corrections-2/run-749ca85ae6c4/report.json) ;  two natural completions and 20 observed latency samples.
- [`../RESULTS_SCHEMA.md`](../RESULTS_SCHEMA.md) and [`../METHODOLOGY.md`](../METHODOLOGY.md) ;  metric validity, censoring, identity, and reliability accounting.
- [`../gate3-plans/multiwindow-10k.json`](../gate3-plans/multiwindow-10k.json) ;  existing exploratory 1024-token plan, stop rule, and authorization state.
- [`../../configs/profiles/qualification-10k.json`](../../configs/profiles/qualification-10k.json) ;  current 20-request profile; historical description needs reconciliation during follow-on work.
- [`../../scripts/check_plan_contract.py`](../../scripts/check_plan_contract.py) ;  selected consistency checks, not a qualification evaluator.
- [`0002-architecture-and-aiperf-verification.md`](0002-architecture-and-aiperf-verification.md) ;  earlier engine and token-accounting verification.
- [NIST confidence intervals for proportions](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm) ;  exact binomial construction; the one-sided zero-event pilot example above is calculated from that model.
