# Gate 3 preparation

**Version:** 0.1.0
**Datestamp:** 2026-11-01
**Source:** Gate 2 verified state, one authorized live smoke run, and the local-mock trace-replay demonstration.
**Status:** Preparation for Gate 3. No screening profile has been executed against a live endpoint.
**Update rule:** Replace plan values with measured evidence as each experiment runs. Do not restate unexecuted work as complete.

This document records the three Gate 3 priorities, what is implemented, what is only planned, and what remains blocked. Nothing here is a benchmark result.

## 1. Token-sizing attribution

### What was wrong

A single boolean withheld every token-derived conclusion at once. When provider-reported prompt tokens disagreed with the client tokenizer, the report suppressed latency evidence too, which hid usable data behind an unrelated problem.

The live smoke run showed why this matters: the provider reported 160 and 165 prompt tokens against a built-in-tokenizer count of 128 — 25.0% and 28.9% high. That is a genuine tokenizer mismatch, not a harness defect.

### What changed

Four token measures are recorded separately per trial and never collapsed:

| Measure | Source |
|---|---|
| `target_input_tokens` | the profile's requested workload size |
| `client_estimated_input_tokens` | the client tokenizer applied to the serialized request |
| `provider_input_tokens` | the provider's `usage` field |
| `observed_output_tokens` | the client's count of the response |

Sizing is `verified` when the client and provider counts agree within the profile's `token_tolerance_pct`, `false` when they do not, and `null` when one side is missing. No correction factor is ever applied.

Each metric now carries its own validity state, so one problem no longer erases unrelated evidence:

| State | Meaning |
|---|---|
| `valid` | directly measured, and safe to use including percentiles |
| `valid_mean_only` | sample count is below the percentile minimum; the mean is usable |
| `invalid_token_sizing` | a token-normalized metric whose denominator is unverified |
| `invalid_client_tokenization` | a client-tokenized metric that does not match the served model |
| `not_observed` | no successful trial produced the metric |

Observed on the local replay run (12 trials, 6 within tolerance, 6 outside):

```
time_to_first_token        valid_mean_only   (12 samples < 30)
request_latency            valid_mean_only
output_token_throughput    invalid_token_sizing
input_sequence_length      invalid_client_tokenization
usage_prompt_tokens        valid_mean_only
```

Latency remains usable while token-normalized throughput is withheld. That is the intended outcome.

### Remaining attribution gap

The root cause of the mismatch is known but not eliminated: the client tokenizer identity does not match the served model. Resolving it requires either a model-specific tokenizer or provider-reported token counts as the accounting source. Neither is implemented. Until then, token-normalized metrics stay `invalid_token_sizing` on any endpoint whose tokenizer differs from `builtin`.

## 2. Reproducible local benchmark

A fresh contributor can run a full AIPerf benchmark with agent-shaped trace replay and produce a normalized report, without credentials and without network access beyond loopback.

```bash
uv sync --locked
uv venv .venv-aiperf --python 3.13
VIRTUAL_ENV=.venv-aiperf uv pip install aiperf==0.13.0

# terminal 1
uv run endpoint-bench mock --port 18080

# terminal 2
uv run endpoint-bench run \
  --config examples/local-benchmark/registry.json \
  --target local-mock-agent-replay \
  --trace-file examples/local-benchmark/agent-trace.jsonl \
  --execute --max-requests 12
uv run endpoint-bench report --run runs/<run_id>
```

`examples/local-benchmark/agent-trace.jsonl` is a synthetic, deterministic Mooncake-format trace: four agent sessions, three turns each, input growing 512 → 1024 → 1536 tokens, 150 ms between turns. It is validated by AIPerf's own validator (`Validation passed: 12 rows are Mooncake-compatible`) and by a CI step.

Verified result of that workflow:

| | |
|---|---|
| Trials | 12/12 success |
| Planned vs actual | 12 planned = 12 HTTP attempts |
| Workload type | `recorded_multi_turn_replay` |
| Workload label | "Recorded trace replay. This is not live agent execution." |
| Termination | completed |
| Engine | AIPerf 0.13.0, summary schema 1.4, validated |

The report is normalized evidence, not a measurement of any real endpoint.

## 3. Prepared live experiments

**Nothing in this section has been executed.** Every manifest below was produced with `--dry-run` and made zero inference requests.

Pricing basis: `https://tokenfactory.nebius.com/models`, effective 2026-11-01, DeepSeek V4.1 Flash 1017 at $0.30 / 1M input and $1.20 / 1M output. Estimates apply a 1.5x safety multiplier. A client-side estimate is not a provider billing limit.

### 3a. Minimal real-endpoint tool-call continuation

One logical trial, two HTTP attempts: a tool call, then a continuation carrying a synthetic tool result.

```bash
uv run endpoint-bench probe \
  --config ~/.endpoint-bench/registry.live.json \
  --target tf-deepseek-v4.1-flash-1017 \
  --profile smoke --scenario tool-round-trip --requests 1 --dry-run
```

| Field | Value |
|---|---|
| Planned trials | 1 |
| Planned HTTP attempts | 2 |
| Planned input tokens | 256 |
| Planned output tokens | 64 |
| Estimated cost | **$0.000230** |

This is the smallest test that can establish whether the endpoint accepts a tool result keyed to a call id and returns a terminated continuation. It cannot establish tool quality, and it does not test multi-call or fragmented-argument behaviour on a live endpoint.

Blocking prerequisite: the `probe` command currently refuses non-loopback targets by design. Running this against a live endpoint requires either an explicit authorization path for probes or execution through the `run` workflow. That decision is not made.

### 3b. 1K / 10K / 32K screening at concurrency 1 and 2

Six runs, each 20 requests. Manifests are committed under [`docs/gate3-plans/`](gate3-plans/).

| Profile | C | Requests | Input tokens | Output tokens | Estimated cost |
|---|---|---|---|---|---|
| screening-1k | 1 | 20 | 20,480 | 5,120 | $0.018432 |
| screening-1k | 2 | 20 | 20,480 | 5,120 | $0.018432 |
| screening-10k | 1 | 20 | 204,800 | 5,120 | $0.101376 |
| screening-10k | 2 | 20 | 204,800 | 5,120 | $0.101376 |
| screening-32k | 1 | 20 | 655,360 | 5,120 | $0.304128 |
| screening-32k | 2 | 20 | 655,360 | 5,120 | $0.304128 |
| **Total** | | **120** | **901,120** | **30,720** | **$0.847872** |

Command template:

```bash
uv run endpoint-bench run \
  --config ~/.endpoint-bench/registry.live.json \
  --target tf-deepseek-v4.1-flash-1017 \
  --profile screening-10k --concurrency 2 \
  --execute --authorize-live --max-requests 20 --max-usd 0.15
```

Authorization requirements for each run: `NEBIUS_API_KEY` present, `--authorize-live`, verified pricing, `--max-requests` at or below 20, and `--max-usd` above the computed estimate. The guard refuses otherwise.

### What these runs would and would not establish

They would establish first-window screening latency and throughput at two concurrency levels for three context sizes, plus request accounting and token-sizing behaviour at each size.

They would not establish qualification, capacity, reliability, cache behaviour, or comparative rankings. Twenty samples per cell in one window is screening. Percentile claims remain marked `valid_mean_only`.

## Deferred milestones

Not started, by instruction: cache experiments, 100K–1M context expansion, extended multi-window reliability testing, and AA synchronization.

## Open blockers

1. **Tokenizer identity.** Token-normalized metrics stay invalid on any endpoint whose tokenizer differs from `builtin`. No model-specific tokenizer is wired in.
2. **Probe authorization path.** `probe` refuses non-loopback targets, so the prepared tool-call test has no approved execution route yet.
3. **Pricing provenance.** The recorded rate came from a rendered public catalog page, not a billing contract. Re-verify before any run materially larger than screening.
4. **Linux and CI verification.** The expanded CI workflow has been replayed locally on macOS only. A passing GitHub Actions run requires an owner-authorized push.
5. **Context claims.** 32K screening exercises real context sizes but establishes nothing about the advertised 1M window.
