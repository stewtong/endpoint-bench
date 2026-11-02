# Result schema contract

**Schema version:** `0.1.0` — dry-run manifests, probe manifests, normalized trial records, and run reports. Trial records and reports carry their own document versions so each can evolve independently.

## Principles

1. Unknown is not zero. Use `null` plus a reason when available.
2. Distinguish observed, derived, inferred, estimated, planned, unverified, unsupported, and failed values.
3. Preserve normalized fields without destroying raw evidence or provenance.
4. Lifecycle and outcome are separate fields, not one mutually exclusive status.
5. Configuration, fixture, methodology, code, and engine versions are retained.
6. Raw provider responses are sanitized before export; AA snapshots remain restricted unless rights permit publication.

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
| `finish_reason` | Provider finish reason, `null` when absent. |
| `attribution` | Who is responsible for a failed outcome: `provider`, `model`, `harness`, or `unknown`. A model that answers instead of calling a required tool is `model`, not a broken endpoint. |
| `token_accounting` | Four distinct token measures; see below. |
| `notes` | Explicit caveats, such as a client-vs-provider token count discrepancy. |

### Token accounting

Four measures are retained separately and never collapsed:

| Field | Meaning |
|---|---|
| `target_input_tokens` | The workload size the profile asked for. |
| `client_estimated_input_tokens` | What the client tokenizer counted in the serialized request. |
| `provider_input_tokens` | What the provider reported in `usage`. |
| `observed_output_tokens` | What the client counted in the response. |
| `discrepancy_pct` | Relative difference between client estimate and provider report. |
| `sizing_verified` | `true` within `tolerance_pct`, `false` outside it, `null` when unverifiable. |

`null` is never a pass. A single unverified or out-of-tolerance trial withholds support for token-normalized claims. No correction factor is ever applied to a reported count; the provider's number is preserved verbatim.

Invariants enforced before any trial is counted:

- `successful_trials <= protocol_completed_trials <= started_trials <= planned_trials`;
- a `success` outcome requires `protocol_completed`;
- a `skipped` trial may not record HTTP attempts;
- a `planned`-only trial may not record HTTP attempts;
- `started` and `transport_completed` require at least one HTTP attempt.

`failed_trials` counts every started trial that neither succeeded nor was cancelled. It is broader than endpoint-attributable failure on purpose: a transport error fails the trial regardless of cause. Per-outcome attribution is reported separately under `outcomes`.

## Run report

Written to `<run-dir>/report.json` by `endpoint-bench run`, and comparable by `endpoint-bench compare`.

| Field | Semantics |
|---|---|
| `client_profile` | Explicit client configuration plus a stable `identity` hash. Baseline and affinity-enabled profiles are distinct identities. `token_accounting_source` is `client_tokenizer` or `provider_usage`. |
| `engine` | AIPerf version, summary schema version, and whether the schema was validated before parsing. |
| `benchmark_failed` | True when the run produced no successful responses. Distinct from an integration failure. |
| `counters`, `outcomes` | Accounting as above. |
| `metrics` | Selected AIPerf metrics with `unit` preserved. Absent metrics stay absent. |
| `token_accounting` | Aggregate sizing verdict. `claims_supported` is false unless every evaluated trial is within tolerance. |
| `provenance` | Engine, deployment, profile, client, affinity mode, timeout and retry policy, planned-versus-actual request reconciliation, termination, and run-relative artifact locations. |
| `metric_validity` | Per-metric usability: `valid`, `valid_mean_only`, `invalid_token_sizing`, `invalid_client_tokenization`, `not_observed`. A token-sizing problem does not invalidate latency evidence. |
| `limitations` | Interpretation restrictions, including unvalidated schema. |

### Workload types

Every run declares exactly one workload type, because they are not interchangeable evidence:

| Value | Meaning |
|---|---|
| `synthetic_single_turn` | Generated from a fixed seed. |
| `recorded_multi_turn_replay` | Recorded trace replay. This is not live agent execution. |
| `live_turn_conformance` | Deterministic protocol checks, not an intelligence evaluation. |
| `framework_agent_execution` | Reserved; not implemented in this phase. |

`compare` refuses to produce deltas when deployment, profile, client profile identity, engine version, or schema validation differ. A delta is never a qualification verdict.

## Planned trial schema

Live trial records will retain:

### Identity and configuration

- `run_id`, `trial_id`, `attempt_id`;
- deployment key, provider, URL, region, model ID, model revision;
- protocol, request settings, reasoning controls, cache/session controls;
- profile ID/version, fixture ID/version/hash, engine version/code revision;
- client host/network metadata and UTC timestamps.

### Lifecycle

- `planned`;
- `started`;
- `transport_completed`;
- `protocol_completed`;
- `cancelled`;
- `skipped`.

These fields may be independent state observations. Invariants such as `successful_trials <= started_trials <= planned_trials` are validated separately from HTTP attempts.

### Outcomes

- `success`;
- `timeout`;
- `rate_limited`;
- `server_error`;
- `client_error`;
- `protocol_failure`;
- `stream_interrupted`;
- `context_rejected`;
- `output_truncated`;
- `budget_exceeded`;
- `unknown`.

A retry produces multiple HTTP attempts under one trial. Unexecuted, circuit-broken, interrupted, or malformed responses are not successes.

### Timing and usage

- request/response phase timestamps;
- first byte/event, first generated token, first answer token;
- end-to-end duration;
- generated-token throughput and denominator;
- requested target size, serialized size, provider-native input/output/reasoning/cache tokens;
- estimated cost and pricing provenance;
- provider-native usage and finish reason.

Missing telemetry remains `null`/`unknown`. Nominal token counts are target values, never observed counts.

### Evidence

- sanitized request/response hashes;
- raw response reference;
- provider error identifiers and safe fields;
- retry lineage;
- configuration hashes;
- engine output reference;
- qualification classification and rule version.

## Compatibility policy

Patch versions may clarify documentation without changing field semantics. Minor versions may add nullable fields or enums while preserving old readers' ability to reject unknown data safely. Major versions are required for semantic breaks and must include a migration note. Never overwrite an old raw artifact with a new normalized interpretation.
