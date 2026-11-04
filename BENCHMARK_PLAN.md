# Agent Endpoint Bench: Endpoint Readiness and Performance Plan

**Version:** 0.5.5
**Datestamp:** 2026-11-03
**Status:** Approved v0.5 specification. The software is v0.1.3 and implements offline registry validation, dry-run manifests, normalized trial evidence with per-metric validity, deterministic streaming and tool-call conformance probes, a pinned AIPerf 0.13.0 wrapper, agent-shaped trace replay, reporting, and two versioned session-dynamics programs. The D0010 default is `session-dynamics-sameday-v1`: one foreground invocation, 30 cells / at most 558 model calls, nine live-turn scenarios at C=1/2/4 plus three context sentinels, with no calendar anchor or scheduled window. The D0003/D0009 54-cell temporal program is preserved as an executed historical observation. Neither program is live-released in the repository and neither has thresholds. Six screening cells and a live tool-call continuation have been executed against one deployment; that evidence is single-window screening. No endpoint is qualified, and no qualification threshold is calibrated.
**Source:** `AGENTS.md`, historical Token Factory/OpenClaw evidence, repository inspection, AIPerf 0.13.0, AA documentation, and the v0.5 public-readiness audit.
**Update rule:** Keep this canonical. Re-verify AIPerf, AA, endpoints, revisions, limits, pricing, reasoning, and cache behavior before execution. Preserve history separately.

## 1. Mission and boundary

Determine whether public model-serving endpoints are usable for interactive coding assistants, autonomous coding agents, research, and document workflows. Measure delivered latency, throughput, stability, capacity, prompt caching, API correctness, tool-call protocol behavior, error handling, and usable context, including approximately 1M input tokens where supported.

The system must be reusable across providers and adaptable to new models, serving features, protocols, and deployment configurations.

This is not a model-intelligence benchmark. Do not grade broad reasoning, coding intelligence, general knowledge, or subjective answer quality. Artificial Analysis (AA) is a separate external reference dataset. Do not join AA scores to endpoint measurements, produce a composite ranking, recommend a model for a workload, or build routing logic in this phase. Lightweight deterministic probes verify protocol and feature compatibility; they do not establish intelligence.

Out of scope: intelligence ranking, LLM-as-judge, broad answer-quality scoring, reward-hacking scores, framework architecture rankings, continuous unattended load generation, routing automation, model-to-workload recommendations, and a combined AA/performance leaderboard. OpenClaw remains a separately labeled validation layer, deferred until the direct-API core is complete.

## 2. Architecture

Use **AIPerf 0.13.0**, commit `794f8bb75f8582f22e412d7e650fc71ca2a3d21a`, as the primary load, traffic, and trace-replay engine. Run it in a Python 3.13 isolated environment because AIPerf 0.13.0 requires Python `<3.14`. Prefer the pinned external CLI over copying its implementation.

Keep a thin orchestration layer:

- `registry`: providers, endpoints, model IDs/revisions, regions, auth env names, capabilities, pricing metadata, protocol/feature flags, and AA mappings.
- `profiles`: workload distributions, fixture versions, context targets, parallelism, sample budgets, safety limits, qualification thresholds, and success criteria.
- `runners`: AIPerf command generation, invocation, process/timeout capture, raw-output preservation, and result parsing.
- `probes`: independent OpenAI-compatible conformance tests for streaming, termination, usage, tools, JSON/schema output, reasoning, long observations, and error handling.
- `aa_import`: authorized AA REST import into immutable local snapshots.
- `analysis`: unified trial schema, reliability summaries, qualification classification, comparisons, and regression/readiness reports.

AIPerf's agentic-code workload is Mooncake-style trace replay, not live tool execution. Use replay for traffic, cache pressure, and performance; use live-turn protocol probes for tool and conversation correctness. Never describe replay as autonomous agent execution.

Report direct API, custom client, AIPerf, and framework results separately. Do not merge client/SDK overhead, framework retries, or session-affinity optimizations into direct endpoint measurements. GuideLLM 0.7.3 is optional only after safe environment-only credential handling is proven; the current historical `run_guidellm.sh` exposes an API key in argv and is not an acceptable security pattern.

### Deployment identity

Define an immutable `deployment_key` from provider, endpoint URL, non-secret account/region, exact served model ID, revision when known, API protocol, serving/configuration flags, and relevant feature flags. Same model across providers is an infrastructure comparison only when revision/settings are confirmed equivalent; otherwise label it a model-endpoint configuration comparison. `/models` is discovery evidence, not proof of streaming, tools, real context, or cache behavior.

## 3. Registry and seed candidates

Use provider-neutral, format-neutral declarative configuration. The current implementation uses JSON to avoid an unnecessary parser dependency. Serialization may change only when schema validation, immutable hashing, and versioning remain intact. Never hard-code Token Factory or Nebius assumptions into core orchestration. Store auth environment-variable names, never values.

Registry fields include logical label, deployment key, URL, protocol, model ID/revision, region, credential env name, capabilities, advertised/observed context, output limit, tokenizer, reasoning controls, cache controls, rate-limit disclosure, price source/date, AA mapping, and last-verified timestamp. Distinguish `unknown`, `unsupported`, `unverified`, `confirmed`, and `not applicable`.

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

## 4. Operational qualification

Each target/profile/configuration receives one qualification result:

- `qualified`;
- `failed`;
- `inconclusive`.

An endpoint is `qualified` only when:

1. mandatory protocol and compatibility checks pass;
2. required latency and throughput objectives are met;
3. reliability and error-rate objectives are met;
4. the workload executes within context, timeout, and budget constraints;
5. sample count and temporal coverage are sufficient for the profile.

An endpoint is `failed` when measured evidence demonstrates that a mandatory requirement was not met. A result is `inconclusive` when observations are insufficient, environmental conditions invalidate comparison, or the failure cannot reasonably be attributed to the endpoint.

Store qualification thresholds in versioned, format-neutral configuration documents, scoped by profiles such as interactive coding, autonomous coding, concurrent agent sessions, research/document processing, and long context. Do not invent universal thresholds in Python. Begin with documented provisional thresholds and require calibration before treating them as production-readiness standards. Preserve full metrics independently of qualification status.

## 5. Trial lifecycle and error-state model

Separate request lifecycle from outcome. Use explicit collections or related records, not one mutually exclusive status field.

Lifecycle states:

- `planned`
- `started`
- `transport_completed`
- `protocol_completed`
- `cancelled`
- `skipped`

Outcome states:

- `success`
- `timeout`
- `rate_limited`
- `server_error`
- `client_error`
- `protocol_failure`
- `stream_interrupted`
- `context_rejected`
- `output_truncated`
- `budget_exceeded`
- `unknown`

Track planned trials, started trials, successfully completed trials, and underlying HTTP attempts separately. Retries may create multiple HTTP attempts per trial. Do not count skipped, circuit-broken, interrupted, or invalid responses as successes. Preserve provider-native errors and usage telemetry in sanitized evidence. Missing telemetry remains `unknown`, not zero.

## 6. Live-turn protocol validation

Gate 2 must include a deterministic live-turn conformance probe. The required sequence is:

1. send a fixed request with a declared tool schema;
2. receive a valid tool call, including streamed arguments when streaming is supported;
3. assemble fragmented tool-call arguments correctly;
4. validate tool-call identifiers, argument serialization, and schema compatibility;
5. return a synthetic tool result associated with the correct call ID;
6. resubmit the complete conversation history;
7. receive a complete and correctly terminated continuation.

Add supported variants:

- multiple tool calls;
- long tool observations;
- invalid tool arguments;
- interrupted tool-call streams;
- streaming and non-streaming responses;
- structured JSON output;
- reasoning-enabled responses;
- requests approaching context limits.

Where supported, use deterministic tool-choice controls to reduce model-selection variability. Distinguish provider/protocol failures from model unwillingness or semantic behavior. Do not automatically blame infrastructure when a capable model chooses a different action. These probes establish compatibility and protocol functionality, not coding intelligence.

## 7. Client effects and cost control

Inspect and record client-side effects before comparisons:

- session-affinity headers;
- default HTTP headers;
- automatic retries;
- connection reuse;
- timeout policy;
- request batching;
- prompt normalization;
- tokenization behavior;
- conversation serialization;
- cache-control parameters;
- hidden request transformations.

Pay particular attention to AIPerf 0.13.0 session-affinity behavior. A session-affinity header can influence routing and cache locality, making one endpoint appear faster than it would for an ordinary client. Where technically possible, establish a normal-client baseline without affinity optimization and a separate affinity-enabled experiment only when the provider supports it. Record which headers and controls were actually sent, excluding credentials or sensitive values. Never silently mix client configurations. Separate direct endpoint latency from client-side and framework overhead.

Distinguish client-side cost protection from guaranteed provider billing limits. A client generally cannot guarantee an exact final dollar charge from estimated token consumption alone.

Before paid execution:

- obtain pricing from a dated source;
- estimate an upper-bound cost using request size, output limits, reasoning behavior, and maximum retries;
- enforce request-count ceilings;
- enforce generated-token ceilings where supported;
- enforce concurrency and offered-rate ceilings;
- enforce duration and timeout limits;
- stop when the configured local budget threshold is reached;
- reject unattended execution when cost cannot be conservatively bounded.

Unknown pricing is acceptable for registry validation, offline tests, dry-run manifests, synthetic fixtures, and configuration inspection when represented explicitly as `null`/unknown rather than zero. It blocks unattended live execution unless a dated provider-specific price source and a conservative exposure estimate support a bound covering output limits, reasoning behavior, retries, and request count. Unknown pricing must not silently become zero. A true hard monetary ceiling may be claimed only when provider-side enforcement supports it. Maintain default-off execution posture; no paid inference starts without explicit authorization.

## 8. Measurement and statistical validity

For every run, retain:

- exact model/deployment identity;
- provider and region;
- API protocol;
- request/response configuration;
- actual serialized input-token count;
- actual observed output-token count;
- target workload size;
- reasoning mode and output budget;
- concurrency and offered request rate;
- request/response timing;
- TTFT and first-answer latency where observable;
- output throughput and its denominator;
- end-to-end latency;
- cache accounting;
- native provider usage and estimated billing;
- error category and retry lineage;
- client host/network metadata;
- engine and fixture versions.

Use provider-native token accounting where available. Verify that nominal workload sizes correspond to actual serialized payload sizes; a purported 100K-token workload must not silently contain only 80K tokens. Preserve tokenizer and token-accounting differences across models. Keep token throughput, request throughput, and successful-workload throughput distinct.

Report sample counts and uncertainty. Do not make strong P95/P99 claims from inadequate samples. For standard comparisons, use repeated observations across time windows, and distinguish screening from reliability qualification. Interleave providers when practical to reduce time-of-day and shared-capacity confounding. Do not imply that an observed capacity boundary is a universal provider limit.

## 9. Context qualification

Separate:

- advertised context;
- API-accepted context;
- demonstrated effective context;
- active per-request context;
- cumulative session token consumption;
- client-side context compaction;
- unknown provider-side transformations.

Increase request sizes toward the verified safe maximum, potentially near 1M input tokens. Reserve space for system messages, tool definitions, response tokens, and safety margin. Verify serialized/tokenized payload size before execution. Use minimal deterministic sentinels at the beginning, middle, and end of large contexts to investigate whether relevant content remains accessible.

A failed sentinel response alone does not prove server-side truncation; model behavior and infrastructure behavior must remain distinguishable. Record `accepted_but_unverified` when the API accepts a request but effective context use cannot be established. Do not claim 1M-context qualification solely because a request returned HTTP 200.

## 10. Prompt-cache experiments

Evaluate:

- stable repeated prefix;
- growing multi-turn transcript;
- early-prefix invalidation;
- interleaved concurrent sessions;
- cache retention over time;
- controlled warm-versus-cold comparisons where possible.

Distinguish provider-reported cached tokens, cache-hit inference from timing, measured latency improvement, measured cost improvement, and unknown cache state. Do not infer cache correctness from low TTFT alone. A client restart does not prove server cold start. Record cache-affecting headers, provider settings, timing, and session-affinity behavior. Preserve separate findings for cache availability, effectiveness, and retention.

### Session-dynamics-v1 adapter

D0010 implements this workload family as the default finite program: nine scenarios, C=1/2/4, five sessions of four calls per cell, and 32K/64K/128K context sentinels in one foreground invocation (30 cells / 558 maximum calls). The D0003/D0009 temporal variant added four windows and is preserved as a historical observation. The live-turn path is used because AIPerf 0.13.0's `multi_turn` dataset carries pre-recorded history and trace replay carries timestamps; neither appends a live assistant reply. The adapter decision and verified AIPerf capabilities are recorded in [`docs/decisions/0004-session-dynamics-adapter.md`](docs/decisions/0004-session-dynamics-adapter.md). The suite has no thresholds, no qualification verdict, and no live release. Client reconnect changes a connection, not a proven provider cache state.

## 11. Artificial Analysis reference integration

AA remains a separate external reference dataset. Retrieve and locally store available model capability data subject to access rights and API availability.

Support available Free API intelligence/capability indices, available Pro API individual evaluation scores, coding-related results, AA-Briefcase and knowledge-work results, agentic/tool-use results, and additional published evaluations as fields evolve. Do not assume every field is available for every model or account.

Implement:

- authenticated access through environment variables;
- pagination;
- bounded retries and backoff;
- explicit 401/403/429 handling;
- null/missing-score handling;
- API version and methodology provenance;
- immutable raw snapshots;
- normalized local records;
- snapshot manifests and hashes;
- model identity mappings;
- access-tier labels;
- attribution and licensing metadata.

Do not silently equate different model revisions, reasoning tiers, quantizations, or deployment variants. Use explicit mapping states such as `unmatched`, `ambiguous`, `unavailable`, `not_entitled`, and `not_measured`. Preserve unknown fields and add fixture-based parser tests when schemas evolve. Store historical snapshots without overwriting previous data. A validated latest-snapshot pointer may be updated only after successful complete ingestion. Do not commit restricted AA records or redistribute them without appropriate rights.

AA scores and endpoint performance results remain independent datasets. Do not build a joint leaderboard, ranking, recommendation formula, or router. Design stable identifiers so a future project can join the datasets without requiring this phase to implement that logic.

## 12. Safety, artifacts, and local environment

The primary development controller is a headless Mac mini used as an existing AI operations node, but the repository must remain portable. Do not modify launchd services, restart Ollama/Open WebUI/OpenClaw unnecessarily, change networking/firewall configuration, install global dependencies unnecessarily, expose local APIs publicly, or send private repositories/sensitive files to public endpoints without approval. A clean clone must not require that controller or external historical evidence.

Every run stores an immutable manifest, raw evidence, normalized trials, sanitized logs, configuration and fixture hashes, engine versions, endpoint/config fingerprints, timeout/retry policy, client metadata, and summary/report. Preserve historical Token Factory and OpenClaw evidence separately; do not pool it with new trials.

Paid inference is opt-in and fail-closed. A live run requires explicit user `--execute`, an allowlisted target, hard local ceilings, a validated dry-run manifest, concurrency/rate safeguards, and clear stop conditions. Dry-run makes zero inference calls. AA sync is separately authenticated metadata work and must not trigger paid inference. Offline unit tests require neither network nor credentials.

Use the existing dedicated project directory, isolated Python environments, known results paths, SSH-friendly commands, and reproducible local execution. Do not introduce a heavyweight database, service architecture, or dashboard before demonstrated need. Structured files such as JSONL and Parquet are acceptable starting points.

## 13. CLI design targets

Command names may change only when implementation evidence supports it:

```text
endpoint-bench registry validate
endpoint-bench aa sync --tier auto --scope tracked
endpoint-bench aa validate --snapshot <path> --offline
endpoint-bench probe --target <deployment_key> --profile smoke --dry-run
endpoint-bench run --target <deployment_key> --profile quick --dry-run
endpoint-bench run --target <deployment_key> --profile quick --execute --max-usd <cap> --max-requests <cap>
endpoint-bench report --run <run_id>
endpoint-bench compare --baseline <run_id> --candidate <run_id>
```

These are interface targets, not claims that the commands exist. Example dollar/request values are syntax examples, not spending authorization. Paid execution must fail closed if prerequisites or authorization are missing.

## 14. Gates

Gate order and objective exits are specified in `IMPLEMENTATION_TASKS.md`. The intended sequence is:

1. repository survey and verified architecture;
2. offline foundation;
3. AIPerf and protocol integration;
4. standard performance profiles;
5. long-context/reliability qualification;
6. AA synchronization and reference dataset.

## 15. Release milestones

### Pre-alpha source repository (v0.0.x)

The source repository may be published as a clearly labeled pre-alpha when it has an approved license, accurate README, reproducible installation, working offline example, passing offline tests and CI, safe configuration and credential handling, honest implementation-status labels, and known limitations. Live inference benchmarking is not required for this milestone.

### Usable benchmark MVP (target v0.1.0)

The benchmark MVP requires functional AIPerf integration, basic endpoint performance measurement, streaming and tool-call conformance probes, repeatable benchmark profiles, normalized metrics and evidence, working reports, and at least one reproducible, authorized live endpoint experiment.

### Expanded operational qualification (later)

Detailed caching comparisons, context testing up to approximately 1M tokens, multi-window reliability and capacity studies, advanced agent-shaped traffic, AA synchronization, and broader provider coverage are later milestones. They are not prerequisites for a pre-alpha source release.

Pre-alpha publication follows the source-repository criteria above. A benchmark MVP follows the stricter v0.1.0 criteria; expanded qualification follows later. Specification completeness is not software readiness. Do not publish a release, leaderboard, benchmark result, or stable maturity label before the corresponding milestone criteria are met.

Implement offline AA parsing foundations earlier when convenient, but keep authenticated synchronization separately controlled.

## 16. Testing and engineering standards

Offline tests must assert outcomes, not merely exercise code paths. Cover provider/configuration errors, protocol and streaming behavior, measurement validity, AIPerf drift, AA integration, and safety guards. At minimum, ensure:

- dry-run produces no paid traffic;
- secrets are sanitized;
- invalid targets fail closed;
- unknown pricing blocks unattended live execution while remaining explicitly unknown offline;
- invalid budget/concurrency settings are rejected;
- large-context runs require explicit authorization.

Prefer verified behavior over documentation assumptions. Keep provider-specific logic out of the core. Preserve raw evidence, normalized metrics, and provenance. Version workload fixtures, qualification rules, and methodology. Distinguish unknown, unsupported, unverified, failed, and not measured. Do not fabricate results or capability declarations. Record limitations instead of silently applying workarounds. Never conflate model intelligence with provider implementation quality.

For experimental comparisons, preserve enough information to reproduce or explain changed measurement conditions. Ensure historical comparisons remain interpretable after code or methodology changes.

## 17. Success criteria

The implementation is on the right path when:

- a new provider/model deployment can be added primarily through configuration;
- offline smoke and dry-run workflows are reproducible;
- agent-shaped workloads can be generated or replayed without confusing replay with live execution;
- a real tool-call conversation can eventually be tested end-to-end;
- AIPerf metrics can be normalized without losing provenance;
- context, caching, concurrency, and reliability testing are properly specified;
- qualification rules are objective and profile-specific;
- AA scores can eventually be refreshed independently as external reference data;
- paid traffic cannot be triggered accidentally;
- the project remains maintainable and avoids rebuilding AIPerf functionality.
