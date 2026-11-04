# Correction 2: measurement guardrails

**Doc version:** 1.0
**Datestamp:** 2026-11-01
**Source:** software 0.1.2, the independent integrity review, and reprocessing of the same six original screening artifact sets.
**Supersedes:** interpretation and validation claims in Correction 1 where stated below; Correction 1 and its evidence files remain preserved.
**Update rule:** Preserve numbered corrections; further evidence changes require another correction.

Correction 1's numerical findings remain: **3 natural completions, 117 output-ceiling truncations, zero endpoint failures**. Provider-reported completion tokens total **30,640**, comprising **28,563 reasoning tokens and 2,077 visible tokens**. The earlier agent response's denominator of 30,768 was a reporting error.

The follow-up fixes four guardrail gaps:

1. Malformed SSE data makes a response a protocol failure even if it later supplies `stop`. The parser records the malformed-frame count in trial notes.
2. `Trial.validate()` now requires consistent natural finish reason/class for success. Independent streaming and tool-continuation probes populate the same fields and reject non-natural completion.
3. Provider-token throughput and token counts remain observations of the delivered workload when the requested input target is missed. `workload_claims_supported` separately withholds requested-size claims. Client-tokenized throughput still requires independent agreement.
4. Censored latency has state `censored`, `statistic_scope: time_to_termination`, `percentiles_usable: false`, and `completion_mean_usable: false`. Increasing sample count cannot identify natural completion statistics from the unadjusted censored samples. TTFT remains separately usable.

Fresh report/correction pairs are under [`gate3-corrections-2/`](gate3-corrections-2/). Source artifact, config and trial hashes identify the original evidence; all source files were checked unchanged before and after reprocessing. Outputs record the reprocessor version. No paid experiment was executed.

Correction 1 reprocessed the six screening cells but not the live smoke run, and that published claim failed the same test. `runs/run-34efa7f73ac1` used a 32-token output ceiling; both responses ended `finish_reason: length`, spent all 32 completion tokens on reasoning, and emitted no visible answer. It is therefore `successful_trials: 0`, `truncated_trials: 2`, not the "both trials succeeded" recorded at the time. TTFT 575.5 ms stands; latency 699.0 ms is time-to-cap. Both sizing checks fail on that run, `provider_vs_target` included, because "128 tokens" meant 128 built-in-tokenizer tokens and the provider counted 160. Corrected evidence: [`gate3-corrections-2/run-34efa7f73ac1/`](gate3-corrections-2/run-34efa7f73ac1/). A second smoke directory from before raw capture existed carries no raw export and supports no claim.

That directory now holds seven runs: the six screening cells plus this smoke run, all with `workload_type: synthetic_single_turn`. Aggregate by `provenance.profile.name`; a naive sweep of the directory counts 122 completed trials instead of the screening matrix's 120.

These fixes do not recover independent client token counts, natural completion latency for truncated responses, or cache-experiment conclusions.

The six-window repeat plan is now explicitly exploratory, with ten-minute window start spacing and a first-window pilot. Its 1024-token ceiling is untested: three selected natural completions cannot characterize the other 117 censored responses. Stop before window 2 if the pilot is censored or evidence is invalid; do not automatically increase the cap or replace trials.

The draft SLO contract permits provenance-verified provider accounting for delivered-workload performance, without claiming tokenizer equivalence. Endpoint error rate is an unratified performance objective, not an automatic evidence-invalidity condition. All performance thresholds remain null; no qualification evaluator is implemented. This experiment cannot qualify an endpoint.
