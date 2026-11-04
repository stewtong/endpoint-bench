# Screening results: 2026-11-02

> **Corrected 2026-11-01.** Two claims below are wrong and one is narrower than stated.
> The harness scored every protocol-completed response as `success` without reading
> `finish_reason`; in fact **117 of 120 responses stopped at the 256-token output
> ceiling**, not 0. The token-sizing verdict was also reached through a circular
> comparison that happened to give the right answer. See
> [`GATE3_RESULTS_CORRECTION_1.md`](GATE3_RESULTS_CORRECTION_1.md) for the corrected
> table, the fields that changed, and the observations that cannot be recovered.
> [Correction 2](GATE3_RESULTS_CORRECTION_2.md) adds the current validation and censoring guardrails.
> The historical results below are preserved as originally published.

**Status:** Screening evidence only. Single window, 20 samples per cell, one deployment, one client profile. This is **not** qualification and supports no capacity, reliability, cache, or comparative claim.

## Conditions

| | |
|---|---|
| Deployment | `deepseek-ai/DeepSeek-V4.1-Flash-1017` on `api.tokenfactory.nebius.com`, region `us-north1` |
| Engine | AIPerf 0.13.0, summary schema 1.4, validated |
| Client | `baseline-normal-client-server-accounting` ;  session affinity disabled, no retries, pooled connections, `use_server_token_count` |
| Token accounting | Provider `usage`. Client tokenizer was not used. |
| Workload | Synthetic single-turn, 256-token output ceiling |
| Pricing basis | $0.30 / 1M input, $1.20 / 1M output (catalog, 2026-11-01) |

## Results

| Profile | C | Trials | HTTP attempts | TTFT ms | Latency ms | Output tok/s | Input tok | Output tok |
|---|---|---|---|---|---|---|---|---|
| screening-1k | 1 | 20/20 | 20 | 553.6 | 1,175.8 | 217.2 | 1,065 | 256 |
| screening-1k | 2 | 20/20 | 20 | 661.7 | 1,550.4 | 293.3 | 1,065 | 256 |
| screening-10k | 1 | 20/20 | 20 | 1,475.6 | 2,211.4 | 115.6 | 10,377 | 256 |
| screening-10k | 2 | 20/20 | 20 | 970.8 | 1,654.3 | 304.2 | 10,377 | 256 |
| screening-32k | 1 | 20/20 | 20 | 1,357.2 | 2,088.9 | 121.6 | 33,147 | 254 |
| screening-32k | 2 | 20/20 | 20 | 1,509.3 | 2,119.9 | 233.6 | 33,147 | 254 |

Totals: 120 planned trials, 120 successful, 120 HTTP attempts, 0 failures, 0 retries, all terminated `completed`.

## What this establishes

1. **Request accounting is exact.** Planned trials equal HTTP attempts in every cell. No hidden retries, no readiness probes, no warmup traffic.
2. **Provider token accounting verifies delivered size.** Reported input averaged within roughly 1 to 4% of the profile target at 1K, 10K, and 32K, so `sizing_verified` was true in every cell and token-normalized metrics were marked `valid` rather than withheld.
3. **The endpoint handled all six cells without error.** Zero failures, zero timeouts, zero truncations at a 256-token output ceiling.
4. **The 32K cells completed**, which exercises real context sizes but establishes nothing about the advertised 1M window.

## What this does not establish

- **No qualification verdict.** No calibrated thresholds exist and 20 samples in one window cannot support one.
- **Percentile claims.** Every timing metric is `valid_mean_only`: 20 samples is below the 30-sample minimum. The averages above are usable; P95/P99 from these cells are not.
- **Concurrency comparisons.** The 10K TTFT reading is *lower* at C=2 than C=1, which is counter-intuitive for a closed-loop load test. With one window and no interleaving, that difference cannot be attributed to concurrency, warm-cache effects, or scheduling noise. Do not read a trend into it.
- **Capacity.** Closed-loop C=2 does not approach a capacity boundary.
- **Delivered-vs-target size.** Provider accounting makes the denominator authoritative; it does not independently confirm the client sent the intended payload.
- **Cost.** Estimates were conservative bounds, not invoices.

## Reproduction

```bash
uv run endpoint-bench run \
  --config ~/.endpoint-bench/registry.live.json \
  --target tf-deepseek-v4.1-flash-1017 \
  --profile screening-32k --concurrency 2 \
  --token-accounting server \
  --execute --authorize-live --max-requests 20 --max-usd 0.40
```

Artifacts are under `~/.endpoint-bench/screening/<run_id>/` and are local by default; they contain raw request and response bodies.

## Next

Multi-window repeats with interleaved ordering are required before any comparison between cells. Long-context escalation, cache experiments, and reliability windows remain subsequent milestones.
