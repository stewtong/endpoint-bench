# Qualification contracts

**Version:** 1.1 — **Datestamp:** 2026-11-01
**Source:** `10k-slo-contract.json`, measurement-integrity review.
**Update rule:** Keep this explanation aligned with the versioned contract.

The active [draft contract](10k-slo-contract.json) scopes 10K delivered-workload performance at C=1/C=2. It is a specification; no qualification evaluator is implemented. The consistency checker validates selected document properties, not measured windows.

Data-quality gates concern trustworthy evidence. Genuine endpoint errors belong to a ratified error-rate performance objective, with observed count, denominator, attribution and uncertainty. They are not discarded as invalid data because an endpoint performed poorly.

All six performance thresholds remain null. The only current verdict is `inconclusive`; an operator must ratify thresholds or designate disjoint calibration data before collecting qualification evidence.

Provider accounting is allowed for delivered-workload performance when usage provenance and target conformance are verified and independent client counts stay absent. It does not establish tokenizer equivalence. Client accounting requires actual independent client/provider agreement. Switching to a known mismatched tokenizer is not a qualification prerequisite.

Unadjusted completion-latency evidence must be uncensored. Any output-ceiling truncation withholds completion means/percentiles; observed time-to-termination and TTFT remain reportable. The 30-sample floor is a minimum guard, not proof of tail precision.

The [six-window plan](../gate3-plans/multiwindow-10k.json) is exploratory. Starts are spaced ten minutes apart. Its first window is a pilot with a stop rule for censoring or invalid evidence; its untested 1024-token cap is not inferred to cover the uncensored population from three selected natural completions. No paid execution is authorized. Future qualification needs a frozen contract and separate qualification windows.
