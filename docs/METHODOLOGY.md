# Benchmark methodology

**Version:** 0.5.1 — **Datestamp:** 2026-11-01
**Status:** Design contract. The repository implements offline configuration validation, dry-run manifests, normalized trial evidence with per-metric validity, deterministic conformance probes, a pinned AIPerf 0.13.0 wrapper, and agent-shaped trace replay. Six screening cells and a live tool-call continuation have run against one deployment. No endpoint has been qualified, and no capacity, reliability, cache, or long-context claim is supported.

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

## Client profiles and session affinity

Every run records an explicit client profile. A client profile is part of the experiment identity, not an incidental detail: changing it changes the measurement.

AIPerf 0.13.0 sends `X-Session-Affinity` carrying a stable per-session identifier on every request **by default**. A router or serving stack may use that header to pin a session to one replica, improving KV-cache locality. An endpoint measured that way can appear faster than the same endpoint measured by an ordinary client.

Therefore:

- **Baseline:** the affinity header is explicitly disabled, and its absence is verified at the receiving server rather than inferred from configuration.
- **Affinity experiment:** a separate configuration and a separate result identity, labeled as such.
- The two are never merged, averaged, or compared as if they were the same client.

`X-Correlation-ID` is always present and is recorded as part of the client profile, because a provider may treat it as a routing key.

Other recorded client effects include connection-reuse strategy, per-request timeout, tokenizer, request batching, prompt normalization, and whether readiness or warmup traffic was issued. Every actual HTTP request is accounted for, including probes and warmups when they are deliberately enabled. The harness does not retry inference requests implicitly, so one trial corresponds to one HTTP attempt.

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

## Token accounting and workload verification

Four token measures are retained separately and never collapsed:

1. **Target** — the workload size the profile requested.
2. **Client-estimated** — what the client tokenizer counted in the serialized request.
3. **Provider-reported** — what the provider returned in `usage`.
4. **Observed output** — what the client counted in the response.

A purported workload size is not evidence of a delivered workload. When the client estimate and the provider report differ beyond a profile-defined tolerance, workload sizing is unverified and token-normalized performance and long-context claims are withheld. Missing provider telemetry is unverifiable, which is not a pass.

No correction factor is ever applied to a reported count. If the numbers disagree, the run is reported as disagreeing.

The built-in offline tokenizer is appropriate for functional and protocol tests. It is not authoritative for a hosted model, whose own accounting may differ.

## Failure attribution

A failed exchange is attributed to exactly one of `provider`, `model`, `harness`, or `unknown`. A model that declines to call a required tool is a `model` outcome: a protocol-shaped failure caused by model choice, not infrastructure. Attribution is recorded per trial so aggregate failure counts do not silently imply provider fault.

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
