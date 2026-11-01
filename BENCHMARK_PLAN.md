# Agent Endpoint Bench — Endpoint Readiness and Performance Plan

**Version:** 0.3.0  
**Datestamp:** 2026-11-01  
**Status:** Approved implementation specification; not evidence of completed benchmark runs.  
**Source:** `AGENTS.md`, `history/BENCHMARK_PLAN_v0.2.0.md`, Token Factory/OpenClaw evidence, repository inspection, AIPerf 0.13.0, and AA documentation.  
**Update rule:** Keep this canonical. Re-verify AIPerf, AA, endpoints, revisions, limits, pricing, reasoning, and cache behavior before execution. Preserve history separately.

## Mission and boundary

Determine whether public model-serving endpoints are usable for interactive coding assistants, autonomous coding agents, research, and document workflows. Measure delivered performance, reliability, capacity, caching, API correctness, and usable context, including approximately 1M input tokens where supported.

This is not a model-intelligence benchmark. Do not grade answer quality, publish a composite score, recommend a model for a workload, or join Artificial Analysis (AA) scores to endpoint measurements in this phase. AA data is external reference metadata only. OpenClaw remains a separately labeled validation layer, deferred until the direct-API core is complete.

Out of scope for v0.3: intelligence ranking, LLM-as-judge, broad answer-quality scoring, reward-hacking scores, framework architecture rankings, continuous unattended load generation, routing automation, and model-to-workload recommendations.

## Architecture

Use **AIPerf 0.13.0**, commit `794f8bb75f8582f22e412d7e650fc71ca2a3d21a`, as the primary load, traffic, and trace-replay engine. Run it in a Python 3.13 isolated environment because AIPerf 0.13.0 requires Python `<3.14`. Prefer the pinned external CLI over copying its implementation.

Use a thin orchestration layer for provider/endpoint registry, profiles, AIPerf invocation/parsing, direct OpenAI-compatible probes, AA import, and analysis/reporting. AIPerf's agentic-code workload is Mooncake-style trace replay, not live tool execution; use replay for traffic, cache pressure, and performance, and use small deterministic live-turn probes for tool and protocol correctness. Never describe replay as autonomous agent execution.

Report direct API and framework results separately. Do not merge client/SDK overhead or framework retries into direct endpoint measurements. GuideLLM 0.7.3 is optional only after safe environment-variable credential handling is proven; the current `run_guidellm.sh` is not an acceptable security pattern because it exposes the API key in argv.

Define an immutable `deployment_key` from provider, endpoint URL, non-secret account/region, exact served model ID, revision when known, API protocol, and serving/configuration flags. Same model across providers is an infrastructure comparison only when revision/settings are confirmed equivalent; otherwise label it a model-endpoint configuration comparison. `/models` is discovery evidence, not proof of streaming, tools, real context, or cache behavior.

## Registry and seed candidates

Use provider-neutral YAML configuration. Never hard-code Token Factory or Nebius assumptions into core orchestration. Store auth environment-variable names, never values. Registry fields include logical label, deployment key, URL, protocol, model ID/revision, region, credential env name, capabilities, advertised/observed context, output limit, tokenizer, reasoning controls, cache controls, rate-limit disclosure, price source/date, AA mapping, and last-verified timestamp. Distinguish `unknown`, `unsupported`, and `not verified`.

Seed Token Factory rows are candidates pending live verification:

| Label | Base URL | Exact API model ID |
|---|---|---|
| GLM Flash current | `https://api.tokenfactory.us-north1.nebius.com/v1` | `zai-org/GLM-5.3-Flash` |
| DeepSeek V4.1 Flash | `https://api.tokenfactory.nebius.com/v1` | `deepseek-ai/DeepSeek-V4.1-Flash-1017` |
| MiniMax M3.1 Flash | `https://api.tokenfactory.nebius.com/v1` | `MiniMaxAI/MiniMax-M3.1-Flash` |
| GLM 5.3 | `https://api.tokenfactory.nebius.com/v1` | `zai-org/GLM-5.3` |
| Muse Spark Open | `https://api.tokenfactory.nebius.com/v1` | `meta-models/muse-spark-open` |
| DeepSeek V4 Pro | `https://api.tokenfactory.nebius.com/v1` | `deepseek-ai/DeepSeek-V4-Pro-0813` |

Add a second provider as configuration and, if needed, an adapter. For offline development, prefer a local OpenAI-compatible mock rather than real credentials.

## Metrics and trial schema

Every planned and actual attempt records UTC timestamps, run/scenario/fixture versions, deployment fingerprint, client host/region/network, engine version, offered load, phase timestamps, requested/observed tokens, TTFT, TTFO/first-answer latency when observable, output-token rate with denominator, end-to-end duration, provider-native usage, cached/reasoning-token fields, finish reason, HTTP/SDK error, bytes, timeout/deadline, cost estimate and source, retry lineage, and sanitized request/response hashes.

Distinguish `planned`, `attempted`, `completed_transport`, `completed_protocol`, `timed_out`, `cancelled`, `rate_limited`, `server_error`, `client_error`, `unsupported`, `skipped_budget`, and `unknown`. A circuit breaker or unexecuted trial is not success. Missing billing, cache, reasoning, or AA fields are `null`/`unknown`, not zero.

Report latency distributions with sample size and uncertainty, sustained successful requests/second, interruption rate, limit onset, per-turn session latency, first-attempt protocol completion, retries, billed tokens/cost, cache usage versus controlled benefit, accepted versus demonstrated context, and time-windowed reliability. Quick samples are exploratory; standard comparisons require 20–30+ samples per cell across time. Discuss P95 only with adequate samples and P99 only with much larger samples. Never present observed limits as universal provider SLAs.

## Workload methodology

Use immutable versioned profiles for smoke, quick, standard, long-context, reliability, and feature checks. Smoke should cover metadata, 1K requests, streaming/non-streaming, and minimal tool/JSON/usage probes. Quick should cover 1K/8K/32K with C=1/2. Standard should cover 1K/10K/32K/100K with C=1/2/4/8 and agent-shaped traces. Long-context and reliability profiles are separately authorized.

Use license-clean synthetic fixtures or explicitly approved traces. Preserve deterministic fixture hashes and real/client-side think time where useful. For traffic-only replay, disclose that later requests do not depend on live prior answers.

For context, reserve output budget, system/tool overhead, and safety margin from the effective ceiling, then serialize and tokenize before sending. Measure active per-request context and cumulative session history independently. Mark rejected, truncated, budget-skipped, accepted-but-unverified, and genuinely demonstrated context separately. Start at 1K/8K/32K before escalating.

For cache, evaluate stable prefixes, growing transcripts, early-prefix invalidation, and interleaved sessions; optional retention windows are 5m/30m/2h. Cache accounting is confirmed only when returned usage and price schema align; performance benefit requires a controlled, repeatable latency/time/cost improvement. Without telemetry, cache state is inferred/unverified.

For reliability, run bounded closed-loop concurrency and, where supported, controlled offered-rate sweeps. Interleave providers and time windows when practical. A client restart or idle-first request does not prove a server cold start. Define readiness thresholds by profile in versioned YAML; until calibrated, thresholds are illustrative rather than SLAs.

Reasoning and streaming comparisons run with transport fixed within each comparison. Measure first reasoning/answer token, reasoning tokens/characters, and false-zero counters. Never merge thinking modes.

## Safety and artifacts

Every run stores an immutable manifest, raw evidence, normalized trials, sanitized logs, configuration and fixture hashes, engine versions, endpoint/config fingerprints, timeout/retry policy, client metadata, and summary/report. Preserve August `tf_bench.py` and November OpenClaw outputs as prior observations; do not pool them with new trials.

Paid inference is opt-in and fail-closed. A live run requires explicit user `--execute`, an allowlisted target, hard dollar/token/request ceilings, a validated dry-run manifest, concurrency/rate safeguards, and clear stop conditions. Dry-run must make zero inference calls. AA sync is separately authenticated metadata work and must not trigger paid inference. Unit tests are offline and contain no secrets or network calls.

Keep the controller safe for the headless Mac mini: use the existing dedicated project directory, isolated Python environments, known results paths, no launchd changes, and no public exposure of model APIs. Do not interfere with Ollama, Open WebUI, OpenClaw, or other local services.

## Gates and completion

- **Gate 0:** verify dependencies/design, pin AIPerf, inspect current evidence, document decisions; no benchmark API calls.
- **Gate 1:** offline registry, schemas, manifests, fixtures, dry-run, AA parser/mapper tests, and deterministic reports.
- **Gate 2:** pinned AIPerf wrapper plus direct API probes. Offline end-to-end first; live smoke only after explicit authorization and cost cap.
- **Gate 3:** standard performance profiles, cache studies, bounded concurrency, uncertainty, and provenance-safe reports.
- **Gate 4:** separately authorized long-context, cache-retention, and reliability/capacity work.
- **Gate 5:** AA reference snapshots with explicit mappings and missing/tier states. Live AA sync requires confirmation of access tier and data-use rights.

Complete v0.3 when a new provider/model/config requires only configuration plus an optional adapter/probe; a versioned profile produces reproducible evidence; smoke is safe and offline by default; AA data can be refreshed locally without fabricated scores; and reports always distinguish endpoint readiness from model intelligence.
