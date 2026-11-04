# Correction 1 to the screening results

**Doc version:** 1.0
**Datestamp:** 2026-11-01
**Source:** reprocessing of the six original screening run directories under `~/.endpoint-bench/screening/` from their unmodified AIPerf artifacts; harness version 0.1.0 at commit `1dcd558` plus the correction patch described below.
**Supersedes:** specific fields of [`GATE3_RESULTS.md`](GATE3_RESULTS.md). That document is retained unmodified as the record of what was published at the time.
**Superseded in part by:** [`GATE3_RESULTS_CORRECTION_2.md`](GATE3_RESULTS_CORRECTION_2.md), which adds validation guardrails and promotes censored latency to its own validity state. Every numerical finding below still stands. This document is retained unmodified apart from this pointer and the one row marked below, because a correction that edits itself is not a record.
**Update rule:** This correction is closed. Any further change to these cells requires a new numbered correction, not an edit to this one.

## Why this exists

Reprocessing the six screening runs from their original raw artifacts found three harness defects. Two of them changed published numbers. The original document was not wrong about everything, and this document is explicit about which claims survive:

| Original claim | Status after correction |
|---|---|
| Provider-reported input averaged 1 to 4% above the profile target | **Holds.** Measured 1.16%, 1.34%, 3.99%. |
| Workload sizing was verified, so token-normalized metrics are valid | **Holds, for a different reason than stated.** The original check compared the provider count against itself and was therefore circular. Provider-versus-target conformance now verifies it independently. |
| 120 planned trials, 120 successful, 0 failures | **Wrong.** 3 trials completed naturally. **117 of 120 hit the 256-token output ceiling** and were recorded as clean successes. |
| Zero truncations | **Wrong.** 117 truncations. |
| All requests terminated `completed` | **Misleading.** HTTP and protocol completion were real; *response* completion was not. |
| Output throughput figures | **Narrower than published.** 87 to 100% of the completion tokens were provider-reported reasoning tokens. Those figures are reasoning-emission rates, not answer-generation rates. |
| The 10K TTFT inversion cannot be attributed | **Holds, and is unaffected** by this correction. TTFT is measured on the first token, before any ceiling applies. |

## Defect 1: token sizing under provider accounting was circular

Under `--token-accounting server`, AIPerf sets `input_sequence_length` from the provider's `usage.prompt_tokens`. The harness read that field as a *client estimate* and compared it with `usage_prompt_tokens`. Both sides were the same number, so `discrepancy_pct` was always exactly `0.0` and `sizing_verified` was always `true`, regardless of what the provider actually received.

Observed in all six cells:

```
client_estimated_input_tokens = 33180
provider_input_tokens         = 33180
discrepancy_pct               = 0.0
sizing_verified               = true
```

The conclusion happened to be true (the provider did see the requested workload) but it was true by construction, not by measurement. A run that delivered the wrong payload would have passed the same check.

**Correction.** Two independent verdicts are now computed and both are always published:

- `provider_vs_target` ;  did the provider see the input size the profile requested? Available in both accounting modes.
- `client_vs_provider` ;  does an independent client tokenizer agree with the provider? `not_observed` under provider accounting, because no client tokenizer ran. It is never back-filled from a provider-sourced metric.

The authoritative check is `client_vs_provider` under client accounting and `provider_vs_target` under provider accounting. `sizing_verified` is derived from that check only. `TokenAccounting` now raises at construction if a client estimate is attached under provider accounting, so the defect cannot be reintroduced by a caller.

## Defect 2: every protocol-completed response was scored `success`

The harness classified a trial by whether AIPerf emitted a record without an `error` object. It never read `finish_reason`, and `finish_reason` was `null` in every one of the 120 stored trial records. In the raw streams, 117 responses ended with `finish_reason: "length"` against our own `max_completion_tokens: 256`.

Those trials were not endpoint failures. They were capped by the benchmark's own configuration, and the published document reported them as clean completions with zero truncations.

**Correction.** `finish_reason` is now recovered from the raw SSE stream, joined by `x_request_id`, and mapped to a terminal class: `natural`, `ceiling`, `content_filter`, `absent`, or `unknown`.

| Condition | Outcome | Attribution |
|---|---|---|
| 2xx, natural finish reason (`stop`, `end_turn`, `tool_calls`, …) | `success` | ;  |
| 2xx, `length` / `max_tokens` | `output_truncated` | `harness` (our ceiling) |
| 2xx, content-policy stop | `protocol_failure` | `provider` |
| 2xx, stream ended with no terminal reason | `stream_interrupted` | `provider` |
| 2xx, unrecognized terminal reason | `unknown` | `provider` |
| Any non-2xx | rate-limit / context / server / client error by status | by status |
| No recoverable 2xx status | `unknown` | `harness` |

`success` now requires a witnessed 2xx status *and* a natural finish reason. `Trial.validate()` raises if either is violated, so a body that looks fine cannot rescue a failed status, and a truncated response cannot be recorded as a success. Ceiling truncation is counted in a new `truncated_trials` counter, separate from `failed_trials`, so our own configuration choice is not reported as an endpoint defect.

A run in which every response was ceiling-truncated is **not** a benchmark failure: time to first token and token rates were still measured. It is reported with `successful_trials: 0`, `truncated_trials: 20`, and a censoring limitation.

## Defect 3: cache and reasoning telemetry was discarded

AIPerf 0.13.0 normalizes `usage_prompt_cache_read_tokens` and `usage_reasoning_tokens`, and both were present in every run. Neither reached the report. Cache-write has no AIPerf metric at all; it exists only in the raw streamed `usage.prompt_tokens_details.created_cache_tokens`, and was never read.

**Correction.** Per-trial `provider_telemetry` now records `cache_read_tokens`, `cache_write_tokens`, and `reasoning_tokens`, each with the source that produced it:

- cache read and reasoning from AIPerf's normalized metrics (`"aiperf_metric"`);
- cache write from the raw response usage (`"raw_response_usage"`), because no metric exists;
- `visible_output_tokens` derived as `completion − reasoning`.

Absent values stay `null` and are never rendered as zero. A zero that the provider actually sent is preserved as a real observation. Aggregates publish `sum` alongside `trials_reporting` and `trials_total`, so a partial total cannot be mistaken for a complete one.

## Corrected results

Identical conditions to the original: one deployment (`deepseek-ai/DeepSeek-V4.1-Flash-1017` on `api.tokenfactory.nebius.com`), AIPerf 0.13.0, baseline client with session affinity disabled, provider token accounting, 20 requests per cell, synthetic single-turn workload, 256-token output ceiling.

| Profile | C | Naturally completed | Truncated | Failed | TTFT ms | Latency ms | Provider/target | Cache read tok | Cache write tok | Reasoning tok | Visible tok |
|---|---|---|---|---|---|---|---|---|---|---|---|
| screening-1k | 1 | 0 | 20 | 0 | 553.6 | 1,175.8 | 1,064.9 / 1,024 (+3.99%) | 0 | 20,480 | 5,120 (100%) | 0 |
| screening-1k | 2 | 0 | 20 | 0 | 661.7 | 1,550.4 | 1,064.9 / 1,024 (+3.99%) | 1,024 | 19,456 | 4,988 (97.4%) | 132 |
| screening-10k | 1 | 0 | 20 | 0 | 1,475.6 | 2,211.4 | 10,377.2 / 10,240 (+1.34%) | 13,312 | 193,024 | 4,794 (93.6%) | 326 |
| screening-10k | 2 | 0 | 20 | 0 | 970.8 | 1,654.3 | 10,377.2 / 10,240 (+1.34%) | 38,016 | 168,320 | 4,737 (92.5%) | 383 |
| screening-32k | 1 | 1 | 19 | 0 | 1,357.2 | 2,088.9 | 33,147.1 / 32,768 (+1.16%) | 66,816 | 594,816 | 4,508 (88.6%) | 578 |
| screening-32k | 2 | 2 | 18 | 0 | 1,509.3 | 2,119.9 | 33,147.1 / 32,768 (+1.16%) | 163,200 | 498,432 | 4,416 (87.0%) | 658 |
| **Total** | | **3** | **117** | **0** | | | | **282,368** | **1,494,528** | **28,563** | **2,077** |

Percentages in the reasoning column are the reasoning share of total completion tokens across the cell. Cache and reasoning totals are sums over the cell, not per-request values.

Corrections against the original table:

| Field | Original | Corrected |
|---|---|---|
| `successful_trials` | 20 in every cell (120 total) | 0, 0, 0, 0, 1, 2 (3 total) |
| `truncated_trials` | field did not exist | 20, 20, 20, 20, 19, 18 (117 total) |
| `failed_trials` | 0 | 0 ;  unchanged; truncation is not an endpoint failure |
| Per-trial `outcome` | `success` | `output_truncated` for 117 of 120 |
| Per-trial `attribution` | `unknown` | `harness` for the 117 truncated trials |
| Per-trial `finish_reason` | `null` | `length` (117), `stop` (3) |
| `client_estimated_input_tokens` | equal to the provider count | `null`, with the reason recorded |
| `discrepancy_pct` | `0.0` | `null` under provider accounting |
| `sizing_verified` | `true`, from a circular comparison | `true`, from provider-versus-target conformance |
| `request_latency` validity | `valid` / `valid_mean_only` | same state, now flagged `censored: true` ;  [Correction 2](GATE3_RESULTS_CORRECTION_2.md) later made `censored` the state itself, with `completion_mean_usable: false` |
| Cache read / write / reasoning | absent | present, with per-field provenance |
| TTFT, latency, throughput, input and output token means | ;  | **unchanged**; the summary metrics were always read correctly |

Latency and TTFT values are identical to the original because they come from AIPerf's summary export and were never mis-parsed. What changed is their interpretation: `request_latency` is now marked right-censored, because for 117 trials it is the time taken to reach the output ceiling, not the time a natural completion would have taken.

## What cannot be recovered from these artifacts

These are listed rather than computed, because the original runs never made the observation:

1. **An independent client token count.** No client tokenizer ran in any of the six cells. Nothing in the artifacts can reconstruct what one would have said. These runs can never support a tokenizer-mismatch claim in either direction, and re-measuring it requires a fresh run with `--token-accounting client`.
2. **Natural completion latency.** The ceiling cut 117 responses. Their true completion time is unknown and is not recoverable by reprocessing; only a run with a higher ceiling can measure it.
3. **Cache behaviour as a benchmark result.** Cache read and write are now recorded, but these cells were not designed as cache experiments: they use identical prompts within a cell, single-turn, with no cache-warming phase and no control for affinity. The values are telemetry, not a cache measurement, and no cache conclusion is drawn here.
4. **Percentile statistics.** Unchanged: 20 samples per cell remains below the 30-sample floor, so every timing metric stays `valid_mean_only`.

## What still stands from the original document

- Request accounting is exact: 120 planned trials, 120 HTTP attempts, no hidden retries or probes.
- Provider accounting makes the throughput denominator authoritative for the served model.
- The endpoint returned HTTP 200 on every request and produced no transport, protocol, or rate-limit failure.
- The 32K cells exercised real 32K contexts, and establish nothing about the advertised 1M window.
- No qualification verdict is supported; no threshold is calibrated; single window; 20 samples per cell.
- The 10K TTFT inversion remains unattributed. It cannot be explained by truncation, since TTFT precedes the ceiling.

## Consequences for the next experiment

The ceiling was not far above natural completion: the three trials that finished naturally used 217, 222, and 249 completion tokens against a 256-token cap. A 256 ceiling is therefore sitting right at the natural boundary for this model, which is why 117 responses were cut.

The multi-window 10K experiment prepared in [`gate3-plans/multiwindow-10k.json`](gate3-plans/multiwindow-10k.json) raises the ceiling to 1024 tokens and treats truncation rate as a data-quality gate rather than an outcome: a window whose truncation rate exceeds the contract's limit is reported `inconclusive` instead of contributing censored latency to a qualification verdict.

## Evidence

Reproduce any cell from its original artifacts, without re-executing anything:

```bash
uv run endpoint-bench reprocess \
  --run ~/.endpoint-bench/screening/run-bf8f644479e6 \
  --out ~/.endpoint-bench/corrections/run-bf8f644479e6
```

Each output directory contains a corrected `report.json` (schema 0.2.0) and a `correction.json` recording every changed field, up to twelve concrete before/after samples per field, the SHA-256 of each source artifact, and the unrecoverable list. Committed copies are under [`gate3-corrections/`](gate3-corrections/).

The source run directories are read-only inputs. `reprocess` refuses to write inside them.

| Cell | Run | Corrected evidence |
|---|---|---|
| 1K C=1 | `run-bf8f644479e6` | [`gate3-corrections/run-bf8f644479e6/`](gate3-corrections/run-bf8f644479e6/) |
| 1K C=2 | `run-4f0ac49fe01c` | [`gate3-corrections/run-4f0ac49fe01c/`](gate3-corrections/run-4f0ac49fe01c/) |
| 10K C=1 | `run-9b61e48f080e` | [`gate3-corrections/run-9b61e48f080e/`](gate3-corrections/run-9b61e48f080e/) |
| 10K C=2 | `run-7797893949bb` | [`gate3-corrections/run-7797893949bb/`](gate3-corrections/run-7797893949bb/) |
| 32K C=1 | `run-9b47f3c7387e` | [`gate3-corrections/run-9b47f3c7387e/`](gate3-corrections/run-9b47f3c7387e/) |
| 32K C=2 | `run-749ca85ae6c4` | [`gate3-corrections/run-749ca85ae6c4/`](gate3-corrections/run-749ca85ae6c4/) |

`docs/gate3-corrections/` is a state snapshot, written by the version in this repository. Regenerate rather than hand-edit.
