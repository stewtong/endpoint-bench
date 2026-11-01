# Benchmark methodology

**Version:** 0.5.0 — **Datestamp:** 2026-11-01
**Status:** Design contract for planned measurements. The current repository implements only offline configuration validation and dry-run manifests.

## Measurement scope

Agent Endpoint Bench measures the operational behavior of a specific public model-serving endpoint under a declared workload. It records latency, throughput, stability, concurrency effects, protocol compatibility, prompt-cache evidence, error behavior, and context behavior.

It does not measure model intelligence, broad coding ability, answer quality, general knowledge, or subjective preference. Deterministic protocol probes establish whether a conversation/tool round trip is structurally valid, not whether the model produced a good answer.

## Experiment identity

A published comparison must identify:

- provider and endpoint URL;
- non-secret deployment/region identity;
- exact served model ID and revision when known;
- API protocol and endpoint path;
- reasoning configuration and output controls;
- serving/configuration flags when disclosed;
- client configuration, HTTP client version, timeouts, and retry policy;
- workload profile and immutable fixture version;
- AIPerf version/commit when used;
- benchmark source revision;
- configuration and fixture hashes;
- UTC start/end time;
- client host class and network context;
- account/authorization tier only at the granularity needed to interpret limits.

Deployment keys distinguish provider, endpoint, model revision, protocol, and relevant serving settings. A comparison across providers is an infrastructure comparison only when model revision and decisive settings are confirmed equivalent; otherwise it is a model-endpoint configuration comparison.

## Latency definitions

All metrics are client-observed unless explicitly labeled provider-native.

- **Time to first response byte/event:** request write completion to first received byte or server event.
- **Time to first generated token:** first token event identified by the response protocol.
- **Time to first answer token:** first answer token after reasoning/analysis content, when the API distinguishes them.
- **End-to-end request duration:** request initiation through complete response or terminal error.
- **Output generation throughput:** generated answer tokens divided by the explicitly defined emission interval.
- **End-to-end session duration:** all turns and tool executions in a session, including non-inference time.

Published throughput must state its numerator and denominator. Without server timing telemetry or controlled network decomposition, do not attribute latency precisely to queueing, serving, network, or client overhead.

## Workload fairness

Workloads record target and realized sizes. Providers do not universally generate an exact requested output length; therefore nominal output limits are not observed output lengths.

Required workload controls include:

- serialized input size and provider-native token count when available;
- maximum and realized output tokens;
- tokenizer identification and model-specific accounting;
- warmup versus measured requests;
- streaming and non-streaming modes;
- reasoning mode and token budget;
- sampling controls;
- timeout, retry, and cancellation policy;
- cache controls and session affinity;
- randomized or interleaved run ordering;
- concurrency/offered rate and client region/network.

## Load models

- **Closed-loop concurrency:** each worker starts another request after completion; the offered rate changes with service time.
- **Open-loop/rate-controlled arrivals:** arrivals follow a schedule or target rate; queueing appears when capacity is exceeded.
- **Offered throughput:** requests or tokens offered by the client.
- **Achieved throughput:** requests or tokens actually processed.
- **Successful throughput:** only successful trials, with failures separately reported.

Performance depends on traffic shape. Do not compare a short closed-loop burst with a sustained open-loop trace.

## Reliability accounting

The denominator is every planned and started trial according to the published analysis definition. Track planned, started, transport-completed, protocol-completed, cancelled, skipped, HTTP attempts, and retries separately.

Failures before first token, stream interruptions, malformed responses, protocol failures, timeouts, and post-response errors must remain visible. Percentiles computed only from successful requests must not imply a request-success rate.

Missing telemetry is `null` or `unknown`, never zero.

## Prompt caching

Cache analysis distinguishes:

- provider-reported cached tokens;
- timing consistent with reuse;
- measured latency/cost improvement against a controlled cold or non-cached condition;
- unknown cache state.

Low time-to-first-token alone does not prove cache correctness. A client restart is not proof of server cold start. Record cache headers/settings, session affinity, conversation serialization, time since prefix use, and competing sessions. Report cache availability, effectiveness, and retention separately.

## Long context

Separate:

- advertised context;
- API-accepted context;
- demonstrated effective context;
- active per-request context;
- cumulative session consumption;
- client compaction;
- unknown provider transformation.

Escalate only after serialized/tokenized size and output headroom are verified. Sentinel placement can reveal partial access, but a sentinel failure does not uniquely prove server truncation; model behavior and infrastructure behavior remain distinguishable. HTTP 200 does not qualify an advertised context.

## Statistics

Screening results require smaller samples and must be labeled provisional. Stronger qualification requires repeated observations across time windows, adequate sample counts, uncertainty reporting, and documented exclusion rules. Tail percentiles are meaningful only when sample size and stability support them. Interleave endpoint measurements where practical to reduce time-of-day and shared-capacity confounding.

Observed capacity boundaries apply to the tested account, region, time, traffic shape, and endpoint—not to a universal provider limit.

## Qualification

Each target/profile receives one of:

- **qualified:** all mandatory checks pass, performance/reliability objectives are met, sufficient observations exist, and constraints are satisfied.
- **failed:** measured evidence demonstrates that a mandatory requirement was not met.
- **inconclusive:** evidence is insufficient, conditions invalidate comparison, or failure attribution is ambiguous.

Thresholds are versioned and workload-specific. Preserve complete metrics independently of qualification status.

## Known limitations and threats to validity

Threats include shared public capacity, hidden serving/model revisions, undisclosed rate limits, transient incidents, client-network effects, tokenizer differences, missing usage telemetry, cache-accounting ambiguity, streaming semantics, and provider-specific response schemas. Results should not be presented as universal SLAs or provider endorsements.
