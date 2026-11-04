# Decision 0004: session-dynamics adapter

**Version:** 1.0
**Datestamp:** 2026-11-02
**Status:** Accepted for the D0003 offline implementation. Not a live release.
**Source:** Installed AIPerf 0.13.0 source and `aiperf profile --help`, the local probe client, and `directives/0003-session-program.json`.

## Context

D0003 requires four dependent model calls per session, real assistant replies appended to later turns, a deterministic local compaction step, a client reconnect between turns, 60-second idle gaps, deterministic in-process tools, per-turn first-visible-content timing, and a per-session input bound. The program is a descriptive performance/reliability characterization, not a model-intelligence benchmark.

## What AIPerf 0.13.0 actually provides

Verified from the installed wheel and CLI:

- `multi_turn` is a **custom dataset shape**: JSONL rows carry pre-recorded conversation history. AIPerf selects conversations and replays their turns; it does not append the model's live reply to the next request.
- `mooncake_trace` and the other trace loaders are timestamped traffic replays. They preserve recorded sessions but are not live dependent turns.
- `--session-header`, `sticky-user-sessions`, and `--conversation-turn-delay-*` control connection/session routing and pacing. They do not provide per-turn first-visible telemetry, deterministic local tools, or compaction.
- AIPerf's metrics are aggregate/exported profiles; the split between first reasoning token, first visible content, queue time, and deliberate idle is not its per-turn contract.

## Decision

Use AIPerf where it owns the workload (single-turn throughput/latency, trace replay) and use the existing live-turn probe transport for dependent session turns. The new `session_engine.py`:

- reuses the probe client's HTTP/SSE handling, tool-call assembly, argument validation, and tool-history validation;
- adds a session-scoped connection, an explicit reconnect, real reply append, local compaction, idle scheduling, per-turn visible-content timing, and input-bound enforcement;
- records `workload_type: live_turn_session_dynamics` and never labels replay as live execution;
- sends no hidden warmup and no retry.

This is an adapter inside the existing package and CLI, not a second benchmark service. `endpoint-bench session generate|dry-run|execute|analyze` is the single user-facing entry point.

## Rejected alternatives

| Alternative | Why rejected |
|---|---|
| AIPerf `multi_turn` dataset | Pre-recorded history; cannot carry the live reply into the next turn. |
| Mooncake trace replay | Traffic replay; not live conversation or tool execution. |
| Reimplement transport from scratch | The probe client already has tested SSE, fragmented tool arguments, usage, timeout and failure classification. Reuse is smaller and testable. |
| Provider session/cache APIs | Provider support is unverified. The program records cache-affecting headers and provider telemetry, but claims no cache benefit. |

## Consequences and limits

- The live-turn mode is a separate workload identity from AIPerf replay. Results are never pooled across the two.
- A reconnect changes the client connection, not a provider cache; no server cache reset is asserted.
- Cache hit telemetry, timing-inferred benefit, and unknown state are reported separately. No benefit is inferred from this design.
- The program has no thresholds and no qualification verdict. The offline implementation and manifest are ready for manager review; `live_released_operation_ids` remains empty.
