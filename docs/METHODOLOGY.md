# Benchmark methodology

**Version:** 0.5.5 ;  **Datestamp:** 2026-11-03
**Status:** Implemented measurement methods include AIPerf workloads, streaming/tool conformance, dependent session turns and a same-day foreground benchmark. The accepted results cover 648 session/context calls; see [published results](results/2026-11-session-dynamics/README.md) for scope and sizing limits. Qualification thresholds remain unset.

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

1. **Target** ;  the workload size the profile requested.
2. **Client-estimated** ;  what the client tokenizer counted in the serialized request.
3. **Provider-reported** ;  what the provider returned in `usage`.
4. **Observed output** ;  what the client counted in the response.

A purported workload size is not evidence of a delivered workload. No correction factor is ever applied to a reported count. If the numbers disagree, the run is reported as disagreeing.

Two comparisons answer two different questions, and a verdict must name which one it performed:

- **provider versus target** ;  did the provider see the workload size the profile requested? Available in both accounting modes, and authoritative when token counts come from the provider.
- **client versus provider** ;  does an independent client tokenizer agree with the provider? Available only when a client tokenizer actually ran.

Under provider token accounting, the engine derives its input and output length metrics *from the provider's own usage*. Comparing those against provider usage compares a number with itself and always agrees. An implementation must therefore leave the client measure absent in that mode rather than populate it from the provider, and must not report the agreement as a verification.

Missing telemetry is unverifiable, which is not a pass.

The built-in offline tokenizer is appropriate for functional and protocol tests. It is not authoritative for a hosted model, whose own accounting may differ.

## Completion classification

A successful HTTP exchange is not a completed response. The server may stop because it reached our output ceiling, because a content policy fired, or because the stream broke. Each of those is a different finding, and only a natural stop is a clean completion.

`finish_reason` is therefore read from the raw streamed response, not inferred from the absence of an error, and mapped to a terminal class: natural, ceiling, content filter, absent, or unknown.

- A response that stopped at our own `max_completion_tokens` is attributed to `harness`, counted as truncated, and excluded from the success count. Reporting it as a success hides that the measured latency is time-to-ceiling.
- A terminal reason outside the known vocabulary is reported as unknown, never assumed natural.
- An unrecognized reason or an absent one is not a success, because naturalness was not observed.

Truncation makes latency a **censored** observation: the measured duration is a lower bound on the time a natural completion would have taken. Time to first token is unaffected, because the first token precedes any ceiling. A run with a high truncation share can still report throughput and first-token latency; it cannot report completion latency.

## Provider cache and reasoning telemetry

Provider-reported cache-read, cache-write, and reasoning token counts are retained per trial with the source that produced each. Absent values stay absent and are never rendered as zero.

Reasoning tokens are a component of completion tokens for reasoning-capable models. When the entire completion budget is reasoning, the response contains no visible answer, and output throughput describes reasoning emission rather than answer generation. That condition is flagged rather than left for a reader to infer.

Cache telemetry is not a cache measurement. Observing cache counters in an unrelated run does not establish availability, effectiveness, or retention, which require a controlled cold-versus-warm design.

## Failure attribution

A failed exchange is attributed to exactly one of `provider`, `model`, `harness`, or `unknown`. A model that declines to call a required tool is a `model` outcome: a protocol-shaped failure caused by model choice, not infrastructure. A response cut by our own output ceiling is a `harness` outcome, not a provider defect. Attribution is recorded per trial so aggregate failure counts do not silently imply provider fault.

## Statistics

Screening results require smaller samples and must be labeled provisional. Stronger qualification requires repeated observations across time windows, adequate sample counts, uncertainty reporting, and documented exclusion rules. Tail percentiles are meaningful only when sample size and stability support them. Interleave endpoint measurements where practical to reduce time-of-day and shared-capacity confounding.

Observed capacity boundaries apply to the tested account, region, time, traffic shape, and endpoint; not to a universal provider limit.

## Qualification

Each target/profile receives one of:

- **qualified:** all mandatory checks pass, performance/reliability objectives are met, sufficient observations exist, and constraints are satisfied.
- **failed:** measured evidence demonstrates that a mandatory requirement was not met.
- **inconclusive:** evidence is insufficient, conditions invalidate comparison, or failure attribution is ambiguous.

Thresholds are versioned and workload-specific. Preserve complete metrics independently of qualification status.

## Known limitations and threats to validity

Threats include shared public capacity, hidden serving/model revisions, undisclosed rate limits, transient incidents, client-network effects, tokenizer differences, missing usage telemetry, cache-accounting ambiguity, streaming semantics, and provider-specific response schemas. Results should not be presented as universal SLAs or provider endorsements.

## Integrity follow-up (0.1.2)

Malformed stream frames cannot be rescued by a later natural stop. Successful normalized trials require a witnessed 2xx response and a consistent natural finish reason/class, including independent probes.

Workload conformance and metric provenance are separate. Provider-token throughput describes the workload actually delivered even when it differs from the target. Such a run cannot support a requested-size or long-context claim. Client-tokenized throughput needs independent tokenizer agreement.

Censored latency summaries describe time to termination. Neither their mean nor their percentiles represent natural completion latency without a bounded interpretation. An earlier version of this section said no censoring-adjusted estimator was implemented; the bounded extension below now implements one.

## Bounded completion latency

A cap-hit duration is a per-trial lower bound, not a missing value. If `L_i <= T_i` for every trial, every order statistic of the lower bounds is a lower bound on the corresponding order statistic of the true completion times. A sample p50 or p95 of `L` is therefore a lower bound on the true p50 or p95. `endpoint_bench.bounds` implements this for `p50`, `p95`, and `mean`.

The asymmetry is the point:

- A lower bound **above** an upper-bound objective (`p50 <= X`) proves failure: the true value is at least the bound, so it is also above `X`.
- A lower bound **below** the objective cannot prove success. The true value may be above `X`, so the result stays `inconclusive`.
- The bounded analysis therefore never returns `qualified` from a lower bound alone.

Censored trials are never dropped. Throughput and visible-answer statistics remain withheld or descriptive: a capped response's observed throughput is not a lower bound on answer throughput. Time to first token precedes the cap and remains exact.

This extension is outside the 1.1 contract's `dq.truncation_rate` gate, which still treats any censoring as invalid. It describes what a censored sample supports without changing that contract's verdict rules.

The six-window 10K plan is exploratory, with ten-minute start spacing and a first-window pilot. Its 1024-token cap is a hypothesis: the three naturally completed screening responses were selected from a heavily censored population. Stop and version the plan if the pilot is censored or its evidence is invalid. Qualification requires a later frozen contract and ratified objectives, including endpoint error rate; genuine endpoint errors are outcomes rather than automatic evidence invalidation. A contract evaluator exists in [`qualification.py`](../src/endpoint_bench/qualification.py), but it is exercised only against synthetic evidence and is not a live qualification pipeline. No endpoint has been qualified.

## Interpretation corrections (2026-11-02)

The bounded 8192 multiwindow is read with three corrections, recorded in full in [`docs/qualification/README.md`](qualification/README.md):

1. Its plan declares a 240-second request timeout, while every generated runtime configuration and report records 60 seconds. The runtime value governs the evidence; the plan value is a declared intent that must be asserted at the next release.
2. Window 1 C=1 is reused evidence from 07:41 UTC; the C=2 pair starts at 07:57 UTC and later windows use ten-minute offsets from it. The design is exploratory and reused, not six uniformly paired fresh windows.
3. The 8192 cap still censored 7 of 240 responses. That does not establish a universal fixed-cap infeasibility result; the 16384 pilot's 1/20 observation is also a finite sample.

The D0010 same-day session default adds descriptive multi-turn, prefix, reconnect, compaction, idle, tool and context measurements in one foreground invocation: 30 cells / at most 558 model calls, a 600 s cell deadline and a 6 h invocation deadline, with no calendar anchor or scheduled window. The operator contract is [`SESSION_DYNAMICS.md`](SESSION_DYNAMICS.md). The earlier 54-cell temporal program remains a separate, previously executed historical observation; its unstarted W2-W4 and context cells were cancelled rather than converted into same-day repeats. Neither program has thresholds or a qualification verdict, and one same-day observation does not establish day-to-day stability.
