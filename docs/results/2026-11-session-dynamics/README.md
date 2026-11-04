# Session and context results

Measured November 3 and 4, 2026, at `https://api.tokenfactory.nebius.com` using `deepseek-ai/DeepSeek-V4.1-Flash-1017`. The configured region was `us-north1`; the provider did not independently expose the serving region or model revision. Streaming was enabled, session affinity disabled, and the output cap was 8,192 tokens.

## Executed scope

| Program | Cells | Actual calls | Planned call slots in executed cells |
| --- | ---: | ---: | ---: |
| Historical session screening | 27 | 510 | 540 |
| Later session observation | 6 | 120 | 120 |
| Same-day context selection | 3 | 18 | 18 |
| Total | 36 | 648 | 678 |

All 648 calls returned HTTP 200. There were 633 natural `stop` finishes and 15 `tool_calls` finishes, with no output truncations or endpoint failures. These counts describe this finite sample.

The tool workload asked each session to read a fixture and summarize it. All 15 sessions made one tool call and then answered in prose, ending after two model calls. The remaining 30 planned slots were unused. Those cells retain their original incomplete-coverage flag; they do not demonstrate four-call tool sequences.

The later observation used growing and interleaved sessions at C=1/2/4, with 20 calls per cell. It remains separate from screening. Three further scheduled windows were cancelled when the benchmark default changed to a single foreground run.

## Context retrieval

Each size used six requests: early, middle and late sentinels, with two replicates per position. All 18 returned the exact expected code and finished naturally. The context invocation took 59.943 seconds.

| Client construction budget | Calls | Provider input tokens per call | Exact checks passed | Request bytes |
| ---: | ---: | ---: | ---: | ---: |
| 32,768 | 6 | 24,234 to 24,269 | 6/6 | 131,222 |
| 65,536 | 6 | 48,754 to 48,826 | 6/6 | 262,294 |
| 131,072 | 6 | 98,266 to 98,410 | 6/6 | 524,438 |

The client constructs inputs with a four-characters-per-token heuristic. Every provider count falls outside the configured 10% sizing tolerance. The roughly constant ratio and successful late-sentinel retrieval are consistent with an estimate/tokenizer difference; provider-internal processing was not observed.

The largest measured request contained 98,410 provider-reported input tokens. Successful retrieval supports that delivered workload. It does not establish handling of 131,072 provider-counted tokens or a maximum context window.

## Data and provenance

- [observations.jsonl](observations.jsonl): one row per call, with program identity, outcome, finish reason, timings, request sizes and provider token counts.
- [context-checks.json](context-checks.json): all 18 sentinel outcomes by requested size, position and replicate.
- [provenance.json](provenance.json): export hashes and the original accepted report hash.

The export omits prompts, responses, credentials, operator configuration and local paths. Original evidence remains preserved separately. Measurement values are copied unchanged. The two program identities stay distinct and their metrics should be grouped before comparison.

The new same-day program's full matrix is 30 cells and at most 558 calls. Its context selection was executed live; its 27 screening cells were exercised with loopback tests, while the published live scenario observations come from the historical program. No screening rerun is implied.

## Limits

Qualification thresholds remain unset. This sample does not establish a population error rate, sustained capacity, day-to-day stability, a cache benefit or a provider billing total. C8, higher output caps, larger contexts and other deployments were outside the executed scope.
