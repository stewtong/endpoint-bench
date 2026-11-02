# Result schema contract

**Schema version:** `0.2.0` — dry-run manifests, probe manifests, normalized trial records, and run reports. Trial records and reports carry their own document versions so each can evolve independently.

Version history:

| Version | Change |
|---|---|
| `0.1.0` | Initial contract. |
| `0.2.0` | Split token sizing into provider-versus-target conformance and client-versus-provider agreement; added completion classification (`finish_class`, `truncated_trials`); added `provider_telemetry`; added `evidence_gaps`; added reprocess artefacts. Schema `0.1.0` reports are readable but marked `legacy_schema: true`, because their `sizing_verified` verdict came from a comparison that was circular under provider accounting. |

## Principles

1. Unknown is not zero. Use `null` plus a reason when available.
2. Distinguish observed, derived, inferred, estimated, planned, unverified, unsupported, and failed values.
3. Preserve normalized fields without destroying raw evidence or provenance.
4. Lifecycle and outcome are separate fields, not one mutually exclusive status.
5. Configuration, fixture, methodology, code, and engine versions are retained.
6. Raw provider responses are sanitized before export; AA snapshots remain restricted unless rights permit publication.
7. A comparison must name both sides. A verdict that does not say which two quantities were compared is not evidence.

## Dry-run manifest

Implemented fields:

| Field | Type | Semantics |
|---|---:|---|
| `schema_version` | string | Result-contract version. |
| `manifest_type` | enum | Always `dry_run` for this schema. |
| `run_id` | string | Deterministic identifier derived from registry/profile hashes and target. |
| `generated_at` | string | UTC RFC 3339 creation time. |
| `software` | object | Benchmark software name/version. |
| `inference.executed` | boolean | Always `false` for dry-run. |
| `inference.network_requests` | integer | Planned/sent inference requests. Always `0` for dry-run. |
| `inference.credentials_sent` | boolean | Whether credentials were sent. Always `false` for dry-run. |
| `inference.reason` | string | Why no request was sent. |
| `deployment` | object | Resolved, non-secret endpoint/model identity. |
| `profile` | object | Resolved workload and concurrency configuration. |
| `planned_exposure` | object | Upper-bound request/token ceilings and cost state. |
| `source_hashes` | object | SHA-256 of registry and profile source files. |
| `limitations` | array | Explicit interpretation restrictions. |

`planned_exposure.estimated_cost` is a conservative upper bound computed from verified per-million rates with a 1.5x safety multiplier, or `null` when pricing is unverified or rates are missing. `estimated_cost_reason` is `conservative_upper_bound` or `not_estimated_pricing_unverified`. Unknown pricing is valid for registry validation and dry-run, but it fails closed before unattended non-loopback execution. A client-side estimate is a bound, not a provider billing limit.

A dry-run manifest is not benchmark evidence, a trial result, or a capability claim.

## Probe dry-run manifest

`probe --dry-run` emits `manifest_type: probe_dry_run` with `planned_trials`, `planned_http_attempts` (2 per trial for `tool-round-trip`, 1 for `streaming`), `inference.network_requests: 0`, and a `cost_preflight` block. It sends nothing.

## Probe manifest

Written to `<run-dir>/manifest.json` by `endpoint-bench probe`.

| Field | Semantics |
|---|---|
| `manifest_type` | `probe`. |
| `deployment_key`, `profile` | Resolved target and profile. |
| `fixture` | Fixture name, version, and SHA-256. |
| `counters` | Lifecycle and outcome counts, including `http_attempts`. |
| `trials_path`, `trials_sha256` | Normalized trial JSONL and its hash. |
| `warnings` | Non-blocking conditions, such as a missing credential variable. |
| `authorization` | `live_authorized`, the credential variable name, and the cost preflight. Present only when a non-loopback target was exercised. |

## Normalized trial records

Written to `<run-dir>/trials.jsonl`, one JSON object per line, carrying `schema_version` per record.

| Field | Semantics |
|---|---:|
| `trial_id` | Stable identifier within the run. |
| `deployment_key`, `profile_name` | Target identity. |
| `lifecycle` | Furthest observed state: `planned`, `started`, `transport_completed`, `protocol_completed`, `cancelled`, `skipped`. |
| `lifecycle_events` | UTC timestamps per observed state. First observation wins. |
| `outcome` | Terminal outcome, independent of lifecycle. |
| `outcome_detail` | Machine-readable reason, for example `http_429` or `timeout_after_first_token`. |
| `http_attempts` | Integer count of wire attempts, preserved separately from trials. |
| `attempts` | Per-attempt records with status and error type. |
| `timing` | `ttft_ms` and `duration_ms`, `null` when unobservable. |
| `usage` | `input_tokens`, `output_tokens`, `total_tokens`, `null` when the provider reported none. |
| `completion` | `finish_reason`, `finish_class`, and `requested_max_completion_tokens`. See below. |
| `attribution` | Who is responsible for a failed outcome: `provider`, `model`, `harness`, or `unknown`. A model that answers instead of calling a required tool is `model`, not a broken endpoint; a response cut by our own output ceiling is `harness`, not a provider defect. |
| `token_accounting` | Two independent sizing comparisons; see below. |
| `provider_telemetry` | Provider cache and reasoning token counts; see below. |
| `evidence_gaps` | Enumerated missing observations: `http_status_unobserved`, `finish_reason_unobserved`, `usage_unobserved`. Enumerated so a report can count them instead of grepping prose. |
| `notes` | Explicit caveats, such as a client-vs-provider token count discrepancy. |

### Completion classification

`finish_reason` is recovered from the raw streamed response, joined to the normalized record by `x_request_id`. It is mapped to a terminal class:

| `finish_class` | Meaning | Outcome |
|---|---|---|
| `natural` | Server stopped on its own: `stop`, `end_turn`, `tool_calls`, … | `success` |
| `ceiling` | Server stopped at our `max_completion_tokens`: `length`, `max_tokens` | `output_truncated`, attribution `harness` |
| `content_filter` | Server stopped on a content policy | `protocol_failure`, attribution `provider` |
| `absent` | No terminal reason in the stream | `stream_interrupted` when the stream also ended without its done sentinel, else `protocol_failure` |
| `unknown` | A terminal reason outside the known vocabulary | `unknown`, attribution `provider` |

A terminal `finish_reason` outside the known vocabulary is reported rather than assumed natural. `requested_max_completion_tokens` records the ceiling the request actually carried, which is what makes a ceiling truncation attributable to our own configuration rather than to the endpoint.

### Token accounting

The measures are retained separately and never collapsed:

| Field | Meaning |
|---|---|
| `accounting_source` | `client_tokenizer` or `provider_usage`. Which side produced the engine's token metrics. |
| `target_input_tokens` | The workload size the profile asked for. |
| `client_estimated_input_tokens` | What the client tokenizer counted in the serialized request. `null` under provider accounting. |
| `provider_input_tokens` | What the provider reported in `usage.prompt_tokens`. |
| `provider_output_tokens` | The provider's `usage.completion_tokens`. |
| `observed_output_tokens` | What the client counted in the response. `null` under provider accounting. |
| `client_estimate_note` | Why a client measure is absent, when it is. |
| `provider_vs_target` | `{discrepancy_pct, verdict}` — did the provider see the requested workload size? |
| `client_vs_provider` | `{discrepancy_pct, verdict}` — does an independent client tokenizer agree with the provider? |
| `discrepancy_pct` | Retained name for the client-versus-provider difference. |
| `tolerance_pct` | Profile tolerance applied to both comparisons. |
| `primary_check` | Which comparison governs the verdict: `client_vs_provider` under client accounting, `provider_vs_target` under provider accounting. |
| `sizing_verified` | Boolean form of `primary_check`'s verdict: `true` verified, `false` failed, `null` unverifiable. |

Verdict states are `verified`, `failed`, `unverifiable`, and `not_observed`.

The anti-circularity rule is enforced at construction: under provider accounting, AIPerf derives `input_sequence_length` and `output_sequence_length` from provider usage, so attaching a client estimate raises `TrialSchemaError`. Comparing those fields to `usage_prompt_tokens` would compare a number with itself, which is how schema `0.1.0` reported `discrepancy_pct: 0.0` and `sizing_verified: true` on every trial regardless of what the provider received.

`null` is never a pass. A single unverified or out-of-tolerance trial withholds support for token-normalized claims. No correction factor is ever applied to a reported count; the provider's number is preserved verbatim.

### Provider telemetry

| Field | Meaning | Source |
|---|---|---|
| `cache_read_tokens` | Prompt tokens served from cache. | AIPerf `usage_prompt_cache_read_tokens`, else raw `cached_tokens`. |
| `cache_write_tokens` | Prompt tokens written to cache. | Raw `usage.prompt_tokens_details.created_cache_tokens` only — AIPerf 0.13.0 exposes no metric for this. |
| `reasoning_tokens` | Provider-reported reasoning tokens. | AIPerf `usage_reasoning_tokens`, else raw. |
| `visible_output_tokens` | `provider_output_tokens − reasoning_tokens`. | Derived. `0` means the entire budget was reasoning. |
| `sources` | Per-field provenance: `aiperf_metric`, `raw_response_usage`, or the derivation used. | — |

Absent stays `null` and is never rendered as zero. A zero the provider actually sent is preserved as a real observation. Run-level aggregates publish `sum` next to `trials_reporting` and `trials_total` so a partial total cannot be mistaken for a complete one.

Invariants enforced before any trial is counted:

- `successful_trials <= protocol_completed_trials <= started_trials <= planned_trials`;
- `truncated_trials <= protocol_completed_trials`;
- `successful_trials + truncated_trials + failed_trials == started_trials`;
- a `success` outcome requires `protocol_completed`;
- a `success` outcome requires an observed 2xx HTTP status; body content alone cannot establish success;
- a `success` outcome may not carry a `ceiling` finish class;
- a `skipped` trial may not record HTTP attempts;
- a `planned`-only trial may not record HTTP attempts;
- `started` and `transport_completed` require at least one HTTP attempt.

`failed_trials` counts every started trial that neither succeeded, was ceiling-truncated, nor was cancelled. It is broader than endpoint-attributable failure on purpose: a transport error fails the trial regardless of cause. Ceiling truncation is excluded because it is caused by our own `max_completion_tokens`, not by the endpoint, and folding it into `failed` would report a harness configuration choice as a provider defect.

## Run report

Written to `<run-dir>/report.json` by `endpoint-bench run`, and comparable by `endpoint-bench compare`.

| Field | Semantics |
|---|---|
| `client_profile` | Explicit client configuration plus a stable `identity` hash. Baseline and affinity-enabled profiles are distinct identities. `token_accounting_source` is `client_tokenizer` or `provider_usage`. |
| `engine` | AIPerf version, summary schema version, and whether the schema was validated before parsing. |
| `benchmark_failed` | True when the endpoint produced nothing scoreable: no successful responses *and* no ceiling-truncated ones. A run in which every response was cut by our own output ceiling is not a benchmark failure — it measured real time-to-first-token and token rates — and is reported as truncated with censoring limitations instead. |
| `counters`, `outcomes` | Accounting as above, including `truncated_trials`. |
| `metrics` | Selected AIPerf metrics with `unit` preserved. Absent metrics stay absent. Includes provider cache-read and reasoning metrics where AIPerf emitted them. |
| `token_accounting` | Aggregate sizing verdict over both comparisons. `claims_supported` is false unless every trial's authoritative check is verified. |
| `completion` | `natural_completions`, `ceiling_truncated`, `content_filtered`, `finish_reason_absent`, `finish_reason_unknown`, `finish_reason_counts`, and `latency_censored` with `latency_censored_fraction`. |
| `provider_telemetry` | Aggregate cache-read, cache-write, reasoning, and visible-output totals with per-field reporting counts. |
| `provenance` | Engine, deployment, profile, client, affinity mode, timeout and retry policy, planned-versus-actual request reconciliation, completion summary, termination, and run-relative artifact locations. |
| `metric_validity` | Per-metric usability: `valid`, `valid_mean_only`, `censored`, `invalid_token_sizing`, `invalid_client_tokenization`, `not_observed`, plus a `censored` flag. A token-sizing problem does not invalidate latency evidence. |
| `limitations` | Interpretation restrictions, including unvalidated schema, ceiling censoring, reasoning-only completions, and cache-write provenance. |
| `legacy_schema` | Present after loading: `true` for schema `0.1.0` reports, with a `legacy_reason`. A legacy report is never silently upgraded. |

### Censoring

A trial that stopped at the configured output ceiling did not finish. Its `request_latency` is the time it took to reach the ceiling, which is a lower bound on the time a natural completion would have taken. `metric_validity.request_latency.censored` is `true` whenever any completed trial was ceiling-truncated, and the reason string states the share.

`time_to_first_token` is not censored: the first token arrives long before any ceiling applies.

### Workload types

Every run declares exactly one workload type, because they are not interchangeable evidence:

| Value | Meaning |
|---|---|
| `synthetic_single_turn` | Generated from a fixed seed. |
| `recorded_multi_turn_replay` | Recorded trace replay. This is not live agent execution. |
| `live_turn_conformance` | Deterministic protocol checks, not an intelligence evaluation. |
| `framework_agent_execution` | Reserved; not implemented in this phase. |

`compare` refuses to produce deltas when deployment, profile, client profile identity, engine version, or schema validation differ. It also refuses when the token accounting source differs, because one report's denominators are provider-authoritative and the other's are client-tokenized. A delta is never a qualification verdict.

## Reprocess correction artifact

Written to `<out-dir>/correction.json` by `endpoint-bench reprocess`, alongside a corrected `report.json`. Schema `1.0`.

| Field | Semantics |
|---|---|
| `correction_type` | `artifact_reprocess`. |
| `source_run_id`, `source_run_dir_name` | The run this correction re-derives. The source directory is read-only and the command refuses to write inside it. |
| `source_report_schema_version`, `corrected_report_schema_version` | What was superseded and what replaced it. |
| `accounting_source` | Recovered from the source report, because it determines which sizing check applies. |
| `source_artifact_sha256` | SHA-256 of every source artifact read, so the correction is bound to exact inputs. |
| `counters_before`, `counters_after` | Aggregate accounting, before and after. |
| `aggregate_field_changes` | Run-level fields that changed. |
| `per_trial_field_changes` | Change counts per field across all trials. |
| `per_trial_samples` | Up to twelve concrete before/after examples, for auditability. |
| `completion`, `token_accounting`, `provider_telemetry` | Recovered aggregates. |
| `unrecoverable` | Fields the original evidence cannot support, each with the reason. Listed rather than computed. |

A reprocess never re-executes a request and never claims a field the original artifacts do not contain. An observation the original run never made — an absent client tokenization, an absent provider counter — is reported as unrecoverable instead of reconstructed.

## Compatibility policy

Patch versions may clarify documentation without changing field semantics. Minor versions may add nullable fields or enums while preserving old readers' ability to reject unknown data safely. Major versions are required for semantic breaks and must include a migration note. Never overwrite an old raw artifact with a new normalized interpretation; a correction is a new artifact that names its source.


## Roadmap

The following are declared in methodology but not yet represented in the schema. They are listed here so an absence is visible rather than assumed implemented:

- per-phase timestamps inside a request (first byte, first event, first answer token);
- retry lineage for clients that retry, which the harness does not do today;
- request and response body hashes for sanitized evidence;
- provider error identifiers beyond the type and message;
- qualification classification with a rule version.

Missing telemetry remains `null` or `unknown`. Nominal token counts are target values, never observed counts.

### Integrity follow-up (software 0.1.2)

Malformed SSE data makes a trial a protocol failure even if a later frame contains a natural finish reason. Validation requires a success to have witnessed 2xx status and consistent natural finish reason/class; independent probes populate the same fields.

Each metric validity entry now includes `statistic_scope`, `completion_mean_usable`, and `workload_claims_supported`. A censored request duration has state `censored`, scope `time_to_termination`, `percentiles_usable: false`, and `completion_mean_usable: false`, regardless of sample count. Its retained numeric summaries describe termination, not natural completion. TTFT is unaffected by the output ceiling.

Provider-derived token counts and throughput remain observations of the delivered workload even when target sizing fails; `workload_claims_supported: false` prevents treating them as requested-size evidence. Client-tokenized throughput still requires independent client/provider agreement. These are additive fields in the unreleased 0.2.0 contract.
