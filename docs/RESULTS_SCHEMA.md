# Result schema contract

**Schema version:** `0.1.0` — early contract for dry-run manifests. Trial, probe, AIPerf, cache, context, and AA schemas will be added before live measurements are accepted.

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

`planned_exposure.estimated_cost` is `null` when pricing is unverified or cost estimation is not implemented; `estimated_cost_reason` explains which state applies. Unknown pricing is valid for registry validation and dry-run, but unbounded/unverifiable pricing must fail closed before unattended live execution.

A dry-run manifest is not benchmark evidence, a trial result, or a capability claim.

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
