# Session-dynamics results schema

**Version:** 1.2
**Datestamp:** 2026-11-03
**Schema versions:** cell `session-cell/1.0.0`; call `session-call/1.0.0`; session `session-record/1.0.0`; analysis `session-results/1.0.0`; state journal `session-state/1.0.0`; same-day run summary `session-same-day-run/1.0.0`.
**Source:** `src/endpoint_bench/session_engine.py`, `src/endpoint_bench/session_evidence.py`.
**Update rule:** Add fields compatibly; never reinterpret an existing denominator or rename an outcome without a schema-version bump.

## Cell (`cells/<operation_id>/cell.json`)

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | string | `session-cell/1.0.0` |
| `operation_id` | string | Exact manifest operation ID |
| `workload_type` | string | `live_turn_session_dynamics` (legacy temporal) or `live_turn_session_dynamics_same_day` (D0010 default); replay is not interchangeable |
| `scenario` | string | One of the nine scenarios or `context_sentinels` |
| `phase` | string | `session_screening`, `context_sentinels`, or legacy `temporal_repeats` |
| `window` | int/null | Temporal window 1 to 4; null for screening/context |
| `concurrency` | int | Declared C for this cell |
| `planned_sessions` | int | Five for session scenarios; zero for context |
| `planned_calls` | int | 20 for session cells; 6 for context cells |
| `status` | string | `completed`, `partial`, `not_measured`, `stopped_rate_limit`, `stopped_invalid_evidence`, `stopped_deadline`, `stopped_total_limit` |
| `stop_reason` | string/null | Observed reason; `completed` on normal completion |
| `evidence_complete` | bool | Every planned call slot has an attempt or an explicit skip. Separate from `status`; an all-ineligible cell can be complete in accounting terms and still `not_measured`. |
| `cache_prefix_audit` | object | Proves leading-prompt equality/inequality from sent bytes; `headers_used_as_proof` is always false |
| `elapsed_ms` | number/null | Cell wall-clock duration |
| `achieved_max_concurrent_sessions` | int | Observed simultaneous sessions |
| `achieved_max_inflight_calls` | int | Observed simultaneous model calls |
| `planned_turn_order` | array | Frozen order from the plan |
| `actual_turn_order` | array | Observed request start order |
| `counters` | object | See below |
| `sessions` | array | Session records |
| `context_checks` | array | Context stage only |

### Cell counters

All counters are counts of records, not estimates:

`planned_calls`, `attempted_calls`, `successful_calls`, `truncated_calls`, `failed_calls`, `endpoint_failed_calls`, `unsupported_tool_calls`, `invalid_tool_argument_calls`, `skipped_calls`, `successful_sessions`, `censored_sessions`, `failed_sessions`, `ineligible_sessions`, `context_demonstrated_effective`, `context_api_accepted`, `context_refused`, `context_truncated`, `context_unverified`, `context_malformed_response`, `http_attempts`.

`successful_calls + truncated_calls + failed_calls + skipped_calls` partitions the recorded calls. `failed_calls` includes endpoint failures, tool refusals, absent calls and budget exhaustion; `endpoint_failed_calls` is the endpoint-attributable subset. Neither is dropped from the denominator. `skipped_calls` includes `ineligible_input_bound` and `skipped`. A failed call is counted, never omitted.

## Session (`sessions.jsonl` / cell `sessions[]`)

| Field | Type | Meaning |
|---|---|---|
| `session_id` | string | `<operation_id>-sNN` or context `<operation_id>-<position>-rN` |
| `session_index` | int | Zero-based index |
| `template` | string | `coding`, `document`, `transcript`, or `context` |
| `group_id` / `prefix_group_id` | string/null | Explicit cache/group identity |
| `seed` | int | Deterministic session seed |
| `status` | string | `completed`, `censored`, `failed`, `ineligible_input_bound`, `unsupported_tool`, `budget_exhausted`, `rate_limited`, `deadline`, `interrupted` |
| `stop_reason` | string/null | Observed stop |
| `planned_calls` / `attempted_calls` | int | Four planned; attempted excludes skipped/ineligible |
| `calls` | array | Call records |
| `transcript.sha256` | string | Hash of the full turn log |
| `transcript.turns` | array | Messages sent, assistant reply, outcome per turn |
| `timing` | object | `started_at_utc`, `ended_at_utc`, `elapsed_ms`, `active_ms`, `idle_ms`, `queue_ms` |
| `compaction` | object/null | Deterministic compaction before/after sizes, facts, `no_inference` |
| `notes` | array | Free-form evidence notes |

## Call (`calls.jsonl`)

| Field | Type | Meaning |
|---|---|---|
| `request_id` | string | `<session_id>-tNN`; unique within a cell |
| `turn_index` | int | 1 to 4 (or 1 for context) |
| `identity` | object | scenario, template, group, session, turn, seed, operation, window, C |
| `connection` | object | `connection_id`, `reconnected` (requested policy), `reused` / `opened` / `prior_closed` / `reuse_error` (observed transport), `measured`, `addresses`, `resolve_ms`, `connect_ms`, `opened_total` |
| `timing` | object | `queue_ms`, `idle_before_ms`, `ttft_any_ms`, `ttft_reasoning_ms`, `ttft_visible_ms`, `active_ms`, UTC start/end |
| `input` | object | `request_bytes`, `messages_chars`, `request_body_sha256`, `messages_sha256`, `leading_content_sha256`, `leading_content_chars`, `leading_marker_sha256`, `namespace_label`, `namespace_marker_sha256`, `requested_input_tokens`, `client_estimated_input_tokens`, `provider_input_tokens`, `input_bound_limit`, `input_bound_exceeded`, `delivered_size_state`, `delivered_size_conformance` |
| `output` | object | provider output/reasoning/visible tokens, cache read/write, visible chars/hash, `visible_missing`, `usable_visible_response`, `truncated` |
| `transport` | object | HTTP status, error type/message, `sse_done_seen` |
| `completion` | object | `finish_reason`, `finish_class` |
| `outcome` / `outcome_detail` / `attribution` | string | Terminal outcome and responsibility |
| `headers` | object | Sent headers with credentials replaced by `<redacted>` |
| `cache_group` | string/null | Cache group value |
| `tools` | object | `tools_sent`, assembled `tool_calls`, deterministic `tool_results`, `unsupported_tool` |
| `evidence_gaps` | array | Named gaps such as `sse_done_marker_absent` |

The client token estimate is a declared sizing approximation (`chars/4`), never provider truth. Provider `usage` is recorded separately and is authoritative when present.

The `connection` object separates the requested treatment from what the wire did. `connection_id` and `reconnected` describe the plan (the client-reconnect scenario requests a new connection at turn 3). `reused`, `opened` and `prior_closed` are observations: `opened` means this call created a TCP transport, `reused` means it sent the request on a socket that was already live, and `prior_closed` means an idle socket was observed closed by the peer and replaced before any request bytes were written. `reuse_error` records a send failure on a reused socket; that request is never retried on a replacement transport. `measured` is false for calls that never reached transport. `addresses`, `resolve_ms` and `connect_ms` cover bounded name resolution and the TCP handshake; they are null on reused calls. `opened_total` is the session's cumulative count through this call. A server that closes each response therefore yields `opened` on every turn rather than a claim of reuse.

## Context check (`context_checks[]`)

`request_id`, `position` (`early`/`middle`/`late`), `replicate`, `requested_input_tokens`, `sentinel_code`, `prompt_sha256`, `messages_sha256`, `namespace_label`, `api_accepted`, `http_status`, `outcome`, `finish_class`, `finish_reason`, `demonstrated_effective`, `classification`, `answered_excerpt`, provider/client input tokens, `delivered_input_tokens`, `delivered_size_conformance`, `delivered_size_tolerance`, `delivered_size_state`.

Classifications: `demonstrated_effective`, `api_accepted_but_unverified`, `refused`, `output_truncated`, `malformed_response`, `failed`, `not_sent`. A failed exact check does not prove server-side truncation. Provider-versus-requested size conformance is a separate observation, not a sentinel result.

## Analysis (`analysis.json`)

| Field | Meaning |
|---|---|
| `schema_version` | `session-results/1.0.0` |
| `scenarios` | scenario → concurrency → group summary |
| `windows` | `scenario:Cn:Wm` → group summary |
| `templates` | `scenario:template` → group summary |
| `context_sentinels` | checks by requested size/position and classification counts |
| `claims` | `qualification: null`, null thresholds, `population_inference: false`, no universal ceiling claim |
| `missing_operations` | Declared cells without evidence (partial mode) |
| `small_sample_warning` | Finite-sample warning |

A group summary contains `endpoint_failed_calls`, `unsupported_tool_calls`, `invalid_tool_argument_calls`, `tool_absent_calls`, `tool_budget_calls`, `metric_eligibility` (`observed` / `lower_bound_only` / `not_computed_requires_clean_cache_baseline` / `requires_independent_sizing` / `not_applicable_thresholds_null`), `denominators` (planned, records, attempted, successful, truncated, failed, skipped/ineligible, observation counts per metric, `partition_consistent`), `achieved_max_inflight_calls`, `achieved_max_concurrent_sessions`, distributions for visible/any TTFT, natural active latency, censored active lower bound, session elapsed, queue and idle, token sums with reporting counts, and cache telemetry with `timing_inferred_benefit: null`.

Every distribution reports `n`, `p50`, `p95`, `mean`, `min`, `max`, estimator and a finite-sample note. An empty sample returns null values, not zeros.

## State journal (`program-state.json`)

`schema_version`, `program_id`, `plan_sha256`, `created_at_utc`, `updated_at_utc`, `operations` (status `started`/`completed`/`interrupted`/`invalid`, counters, cell path), and `attempts` (one record per request, `started`/`finished`/`interrupted`). A duplicate request ID is rejected. A journal with a different plan hash is rejected.

The journal holds an exclusive `flock` next to the state file (`program-state.json.lock`) for its lifetime. A second executor cannot open the same state, reconcile a live operation to `interrupted`, or start another cell. Reconciliation runs only after the lock is acquired, never from a `started` status alone. `--state` cannot point outside `<manifest directory>/program-state.json` for the same released manifest.

## Same-day run summary (`run-summary.json`)

Written by `endpoint-bench session same-day --execute` after the invocation stops, including a cancelled or partial invocation.

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | string | `session-same-day-run/1.0.0` |
| `program_id` / `directive_id` | string | `session-dynamics-sameday-v1` / `D0010` |
| `plan_sha256` / `manifest_sha256` / `released_manifest_sha256` | string | Content digests of the frozen plan, preflight manifest and released variant |
| `selection` | string | `all`, `screening` or `context`; execution-scoped only |
| `selected_operation_ids` | array | Exact cells this invocation was allowed to touch |
| `selected_max_inference_attempts` | int | Finite call cap for the selection |
| `cell_deadline_s` / `invocation_deadline_s` | number | Effective guards, never above the frozen 600 s / 6 h values |
| `status` | string | `COMPLETED`, `PARTIAL` or `CANCELLED` |
| `stop` | object/null | Reason, operation ID and UTC time when work stopped early |
| `cells` | array | One entry per selected cell with `outcome` (`executed`, `already_completed`, `preserved_*`, `skipped_deadline`, `skipped_after_stop`, `cancelled`, `failed`), attempts, cell status and evidence path |
| `totals` | object | Selected, executed, already completed, preserved partial, skipped by deadline, skipped after stop, cancelled, failed, attempted calls, completed cells total |
| `inference` | object | `executed`, `loopback`, `authorized_live`, `network_requests` |

`status=COMPLETED` means every selected cell is either executed this invocation or already completed; it does not mean the endpoint passed a threshold. `skipped_deadline` and `skipped_after_stop` cells stay unstarted and resumable. A `preserved_*` cell had prior partial evidence and is never replayed.
