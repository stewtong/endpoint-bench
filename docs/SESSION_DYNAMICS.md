# Session-dynamics operator guide

**Version:** 1.4
**Datestamp:** 2026-11-04
**Status:** The same-day program is the default. Live execution requires explicit authorization. Published [session/context measurements](results/2026-11-session-dynamics/README.md) cover 648 calls; private run directories and journals are excluded from the public export.
**Update rule:** Version this guide with any change to scenarios, limits, argv or evidence fields. Do not describe a planned cell as measured.

## Same-day default

The default is `session-dynamics-sameday-v1`: one foreground invocation that generates the frozen plan, preflights it, executes the enumerated cells in order, analyzes and writes `REPORT.md`. It has no calendar anchor, not-before window, background monitor or scheduled wait.

The normal matrix is **30 cells / at most 558 model calls**:

| Phase | Cells | Calls per cell | Maximum calls |
|---|---:|---:|---:|
| Scenario screening (nine scenarios at C=1/2/4) | 27 | 20 | 540 |
| Context sentinels (32K/64K/128K, two replicates per sentinel position) | 3 | 6 | 18 |
| **Total** | **30** | | **558** |

The same nine scenarios and the same workload construction are used. The 24 temporal-repeat cells from the legacy program are not part of the default. Optional C8, sustained/offered-rate, inputs above 131,072, higher output caps, other deployments and AA remain separate future scopes.

| Guard | Same-day value |
|---|---:|
| New inference attempts | 558 maximum |
| Concurrency | One cell active; C simultaneous sessions; one outstanding call per session; ≤4 in-flight calls |
| Session input | 10,240 initial target; hard bound 32,768 |
| Context input | 131,072 maximum |
| Output cap | 8,192 completion tokens per call |
| Request timeout | 240 s |
| Cell deadline | 600 s |
| Foreground invocation deadline | 6 h |
| Journal lifetime guard | 24 h on the legacy `session execute` path only; **not** an enforced cross-resume guarantee for `session same-day` |
| Retries / replacements / hidden warmup | 0 |

A cell that hits its deadline records a partial observation; the invocation stops starting cells at its own deadline and records the remaining cells as skipped. Both remain resumable and are never replayed.

### Command

```bash
# default: generate + preflight only; zero requests, no credentials
endpoint-bench session same-day --out docs/session-dynamics-sameday-v1

# one foreground run against the bound target; explicit live authorization
NEBIUS_API_KEY=... endpoint-bench session same-day \
  --execute --authorize-live --max-usd 17

# finite context-only selection for the 18-call completion proposal
endpoint-bench session same-day --select context \
  --execute --authorize-live --max-usd 1
```

`--program` defaults to [`configs/session-programs/session-dynamics-sameday-v1.json`](../configs/session-programs/session-dynamics-sameday-v1.json). `--target-metadata` defaults to [`configs/session-programs/session-dynamics-v1.target.json`](../configs/session-programs/session-dynamics-v1.target.json), the same approved endpoint/model binding; `--config` plus `--target` can bind a registry deployment instead. `--cell-deadline` and `--deadline-hours` may only lower the frozen values. `--release-nonce` stays fixed by default so a rerun resumes the same plan; changing it creates a different plan identity and a different journal.

### Writes

`plan.json`, `manifest.json`, `PREFLIGHT.md`, the canonical `program-state.json` journal and lock, `cells/<operation_id>/`, `manifest.released.json`, `release-record.json`, `analysis.json`, `analysis.md`, `run-summary.json`, `REPORT.md`.

### No stability claim

A frozen preflight can be reused byte-exactly with `--manifest <run>/manifest.json` (the manifest must live in `--out`); this keeps the plan, source and manifest hashes fixed for a reviewed completion proposal. A same-day run is one finite observation. It does not establish day-to-day stability, an SLO, a capacity limit, endpoint qualification or a population tail. No immediate repeat is automatic; an explicit finite repeat would still not be a stability claim.

### Executed context measurement (D0011)

D0011 executed only the three context cells: 3 cells / 18 calls, `COMPLETED`, 18/18 HTTP 200, 18/18 natural stops, 0 refusals, 0 output truncations, 0 malformed and 0 unverified. All 18 sentinel checks returned the exact verification code (early/middle/late, two replicates per position, at each requested size). The same-day 27 screening cells remain unmeasured under this program identity.

Requested sizes are **client construction budgets** at 4 characters per token. Provider-reported input tokens were 24,234 to 24,269 (32K), 48,754 to 48,826 (64K) and 98,266 to 98,410 (128K), about 74 to 75% of the construction budget at every size; all delivered-size checks are `outside_tolerance` against the client-estimate check. The late-depth sentinel was returned exactly at every size, so this pattern indicates a tokenizer/estimate difference, not size-dependent truncation. It is not evidence of 131,072 provider-counted tokens or of a provider maximum window.

Published measurements: [per-call export and context checks](results/2026-11-session-dynamics/README.md), with the combined report [`docs/reports/sameday-context-final/REPORT.md`](reports/sameday-context-final/REPORT.md).

## Legacy temporal program (D0003/D0009 historical record)

The material below documents the original 54-cell temporal program. D0009 executed 27 screening cells and 6 W1 temporal cells (33 cells / 630 attempts); D0010 revoked the unstarted W2/W3/W4 and context cells and removed the multi-day schedule from the default. The original plan, released manifest, journal, cells, transcripts and raw evidence are preserved unchanged in [`docs/session-dynamics-v1/`](session-dynamics-v1/). Do not rebuild or overwrite that directory.

## Legacy workload (nine scenarios, 54 cells)

Nine scenarios, each with five sessions of four model calls, at C=1/2/4:

| Scenario | What changes | What is held matched |
|---|---|---|
| `independent` | Four unrelated prompts per session | Input length/template |
| `stable_prefix` | One stable synthetic prefix per group; matched suffixes | Prefix/suffix lengths; independent baseline |
| `prefix_invalidation` | Early prefix change on turns 3 to 4 | Prefix/suffix lengths; stable-prefix pair |
| `growing_transcript` | Real assistant replies retained; input grows | Initial prompt/template |
| `interleaved_sessions` | Round-robin turns across five sessions | Same dependent transcript as growing |
| `client_reconnect` | New client connection before turn 3 | Same transcript and turn schedule |
| `explicit_compaction` | Deterministic local compaction before turn 3 | Same transcript; designated facts preserved |
| `idle_resume` | 60-second pause before turn 3 | Same transcript; idle reported separately |
| `tool_roundtrip` | Fixed in-process tools and real tool-result continuation | Four-call budget; every call counted |

The temporal stage repeats `growing_transcript` and `interleaved_sessions` at all three concurrency levels in four windows at nominal offsets **0h, 6h, 24h, 30h**. The context stage makes six calls at each of **32,768 / 65,536 / 131,072** requested input tokens, with early/middle/late sentinels and two replicates per position.

The finite matrix is **54 cells and at most 1,038 new inference attempts**. Every cell is labelled `live_turn_session_dynamics`; replay and live-turn evidence are never pooled. It is defined in [`configs/session-programs/session-dynamics-v1.json`](../configs/session-programs/session-dynamics-v1.json). The target-bound review plan/manifest is in [`docs/session-dynamics-v1/`](session-dynamics-v1/); rebuild it with `scripts/build_session_preflight.py`, not by overwriting it with a target-unbound generate run.

## Legacy limits (54-cell temporal program)

| Guard | Value |
|---|---:|
| New inference attempts | 1,038 maximum, including failures, setup, tools and any warmup |
| Concurrency | One cell active; C simultaneous sessions; one outstanding call per session; ≤4 in-flight calls |
| Session input | 10,240 initial target; hard bound 32,768 |
| Context input | 131,072 maximum; no 1M request and no automatic escalation |
| Output cap | 8,192 completion tokens per call |
| Request timeout | 240 s |
| Cell process deadline | 2,400 s |
| Program active / elapsed | 36 h active / 48 h elapsed |
| Retries / replacements | 0 |
| Hidden warmup / readiness inference | 0 |

An input that exceeds its bound is marked **ineligible**; the harness does not trim it, switch profile, or issue a replacement call. A truncated dependent turn censors that session; it is not retried. Endpoint errors remain counted outcomes.

The request timeout and cell deadline are **absolute transport bounds**, not per-read socket timeouts. A peer that keeps sending one byte before each socket timeout can otherwise hold an incomplete header or SSE line open indefinitely. Each call therefore arms an independent guard that shuts down only its own connection at the earlier of the remaining request and cell deadlines; it follows the socket into the response after headers, covers send, header, streaming-line and non-streaming-body work, and is joined and closed before the next turn. Name resolution happens before any socket exists, so it runs in an owned fresh isolated interpreter (no Python work after a threaded fork). One absolute budget begins before helper startup and covers IPC/resolution; all exit paths kill and reap unfinished helper work, with a separate finite 0.2-second cleanup grace. OS process creation itself has no Python cancellation hook; its elapsed time consumes the budget and an exhausted startup cannot proceed to connect or send. A cleanup failure aborts as a harness error. The helper is included in runtime source hashes; the resolved addresses are then connected under that budget with the socket published before the handshake, so a blocked connect is cancellable too. Journal work is followed by a pre-send recheck, so a call whose budget expires during durable-intent recording is skipped with no request sent. Partial evidence from a cancelled call is preserved.

A turn connects only when its session has no live socket, and reuse is recorded from the wire rather than from the turn index: every call reports whether it opened or reused a transport, whether an idle socket had been closed by the peer, and the resolution/connect timings. A completed response is drained before its socket can carry the next request. A failed send on a reused socket is recorded and never retried on a replacement transport.

## Legacy commands

All commands are offline unless `execute` is invoked.

```bash
# 1. Freeze plan and target-unbound preflight. No credentials; zero requests.
endpoint-bench session generate \
  --program configs/session-programs/session-dynamics-v1.json \
  --out /tmp/session-plan

# 2. Bind a registry target and emit the preflight. Still zero requests.
endpoint-bench session dry-run \
  --program configs/session-programs/session-dynamics-v1.json \
  --config <registry.json> --target <deployment_key> \
  --out <run_dir> --max-usd 31

# 3. After a manager release, one cell at a time.
endpoint-bench session execute \
  --manifest <run_dir>/manifest.json \
  --operation <operation_id> \
  --execute --max-requests <exact cell attempts> \
  --max-usd <cell envelope> --authorize-live

# 4. Offline validation and aggregation.
endpoint-bench session analyze \
  --run-dir <run_dir> --manifest <run_dir>/manifest.json --allow-partial
```

`execute` fails closed when: the manifest target is unbound; a non-loopback operation is not in `live_released_operation_ids`; `--authorize-live` is absent; the request cap is not the exact cell budget; the dollar cap is below the cell envelope; the credential environment variable is missing; the release anchor is missing or the temporal window is not open/closed; the 36 h active or 48 h elapsed cap would be exceeded; the operation is already completed, started, interrupted or invalid; the program attempt cap would be exceeded; the plan content does not match its declared digest; a source, workload template, context fixture, program config or operation limit differs from the frozen manifest; or `--state` names a journal outside the manifest's canonical path. Missed windows are never backfilled. Loopback execution is allowed for offline tests and needs no credential.

The plan's exact argv for every cell is recorded in `manifest.json`. Do not invent a different command in a report.

## Cache controls

- **The leading prompt bytes are the cache namespace.** Every first message begins with a fixed-width `CACHE_NAMESPACE=<label>` marker, and the plan freezes each turn's label.
- `independent` uses a unique marker per request (baseline).
- `stable_prefix` shares one marker per session across its four turns (treatment).
- `prefix_invalidation` keeps turns 1 to 2 on the original marker and installs a new marker at turn 3; turn 4 keeps the new marker.
- Growing/reconnect/compaction/idle sessions share one marker per session. Compaction keeps the marker and appends its deterministic local summary.
- Context sentinels use a unique marker per request.
- Every cell gets a unique namespace derived from the release nonce and operation ID, so a prior cell cannot intentionally prewarm a supposed independent baseline.
- Paired conditions use the same role structure and the same prefix/task sizes. Evidence records the SHA-256 of the actual first message and its leading marker; `cache_prefix_audit` proves intended equality/inequality from those sent bytes.
- Headers `X-Cache-Group` and `X-Session-Id` are sent and recorded with credentials redacted, but header values are not proof of cache partitioning. `X-Session-Affinity` is never sent.
- The first turn is measured; there is no hidden warmup.
- Provider cache-read/write telemetry, timing-inferred benefit, and unknown state are separate fields. No cache benefit is claimed from this design.

## Context sizing and terminal status

Context fixtures are sized as **full serialized requests**, reserving the JSON wrapper overhead inside the declared token budget. The 131,072-token cell therefore sends its six intended calls under its frozen bound. Every call records the client estimate, the provider count when present, and a delivered-size state; context checks report provider-versus-requested conformance separately from the sentinel answer.

`status` and evidence completeness are distinct. A cell that made zero attempts is `not_measured`, never `completed`. `evidence_complete` records whether every planned call slot has an attempt or an explicit skip; an all-ineligible cell can be complete in accounting terms and still not be a measured result.

## Timing and completion vocabulary

- `ttft_any_ms`: first chunk carrying any content, reasoning or tool fragment.
- `ttft_reasoning_ms`: first reasoning delta.
- `ttft_visible_ms`: first visible `content` delta. It is never inferred from aggregate TTFT.
- `queue_ms`: time from turn-ready to request start, measured **after** any deliberate idle.
- `idle_before_ms`: deliberate idle before a turn (60 s on `idle_resume`). It is never included in `queue_ms`.
- `active_ms`: request start to request end.
- `session_elapsed_ms`: first call start to last call end.
- `finish_class`: `natural`, `ceiling`, `content_filter`, `absent`, `unknown`.
- Outcomes: `success`, `output_truncated`, `timeout`, `rate_limited`, `server_error`, `client_error`, `protocol_failure`, `stream_interrupted`, `context_rejected`, `unsupported_tool`, `invalid_tool_arguments`, `tool_call_absent`, `tool_budget_exhausted`, `ineligible_input_bound`, `skipped`, `cancelled`, `unknown`.

A truncated call's active time is a lower bound, not a completion time. A visible response is "usable" only when it has non-empty visible content and a natural finish; a tool-call turn is usable for protocol continuation without visible content.

## Evidence and restart

Each cell writes `cells/<operation_id>/cell.json`, `calls.jsonl`, and `sessions.jsonl` under the run directory. The journal `program-state.json` is written before each request and after it completes. The journal holds an exclusive `flock` for its lifetime: a second executor cannot open the same state, reconcile a live operation to `interrupted`, or start another cell. Reconciliation happens only after that lock is acquired, never from a `started` status alone. `--state` cannot point at a different journal for the same released manifest; the canonical path is `<manifest directory>/program-state.json`.

On restart:

- unstarted operations may run;
- an operation that was `started` when the lock was reacquired becomes `interrupted` and is **never replayed**;
- a partial cell is preserved and analyzed, not regenerated;
- a completed cell returns `already_completed` with zero requests.

Duplicate operation, session and request identities are rejected by validation. Evidence files are immutable once written.

## Analysis

`session analyze` produces `analysis.json` (`session-results/1.0.0`) and `analysis.md`. It reports each scenario × concurrency, each window, each template, and the context stage. Every table carries denominators: planned, attempted, successful, truncated, failed, skipped/ineligible, and observation counts for each metric. Missing telemetry is `null`, never zero.

Thresholds are null. The analysis makes no qualification verdict, no population claim, and no universal ceiling-tail claim. Twenty calls per cell and five sessions per scenario are small samples; p95 is unstable and descriptive.

## What the legacy program does not cover

C=8, offered-rate/sustained capacity, inputs above 131,072, higher output caps, other deployments, affinity-on experiments, cache-benefit attribution, endpoint qualification, and ~1M context remain separate later plans. A finite design sketch is in [`SESSION_FOLLOWON_SCOPE.md`](SESSION_FOLLOWON_SCOPE.md); it is not part of the 54 cells and is not authorized. This subset is not the entire benchmark.

## Related

- [Session results schema](SESSION_RESULTS_SCHEMA.md)
- [Report template](SESSION_REPORT_TEMPLATE.md)
- [Adapter decision](decisions/0004-session-dynamics-adapter.md)
- [Historical inventory](HISTORICAL_RUN_INVENTORY.md)
- [Bounded analysis correction](GATE3_MULTIWINDOW_8192_BOUNDED_RESULTS.md)
