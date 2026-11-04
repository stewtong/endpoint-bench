"""Live-turn session engine for session-dynamics-v1.

This is the smallest tested adapter that can do what AIPerf 0.13.0 cannot do for
this program: append the *actual* assistant reply to a dependent transcript,
measure first-visible-content separately from first-any-token, reconnect a
client between turns, run deterministic in-process tools, and enforce a
per-session input bound before a request is sent.

AIPerf's `multi_turn` dataset is a JSONL of pre-recorded conversation history
and trace replay replays timestamps; neither produces a dependent live turn.
The existing conformance probe client is reused for transport, SSE assembly and
tool-call validation.

Everything here is deterministic and offline-testable. No request is sent
unless the caller invokes `execute`, supplies authorization, and the journal
accepts the operation.
"""

from __future__ import annotations

import copy
import hashlib
import http.client
import json
import math
import random
import select
import socket
import ssl
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .probes import (
    LIST_DIR_TOOL,
    READ_FILE_TOOL,
    AssembledToolCall,
    _assemble_from_delta,
    validate_tool_arguments,
    validate_tool_history,
)
from .session_program import (
    CACHE_NAMESPACE_WIDTH,
    WORKLOAD_LIVE_TURN_SESSION,
    build_templates,
    cache_namespace_marker,
    context_fixture,
    estimate_tokens,
    prefix_with_namespace,
    sha256_text,
    utc_now,
)
from .trials import (
    FINISH_ABSENT,
    FINISH_CEILING,
    FINISH_CONTENT_FILTER,
    FINISH_NATURAL,
    classify_finish_reason,
)

# -- outcomes -----------------------------------------------------------------

OUTCOME_SUCCESS = "success"
OUTCOME_OUTPUT_TRUNCATED = "output_truncated"
OUTCOME_TIMEOUT = "timeout"
OUTCOME_RATE_LIMITED = "rate_limited"
OUTCOME_SERVER_ERROR = "server_error"
OUTCOME_CLIENT_ERROR = "client_error"
OUTCOME_PROTOCOL_FAILURE = "protocol_failure"
OUTCOME_STREAM_INTERRUPTED = "stream_interrupted"
OUTCOME_CONTEXT_REJECTED = "context_rejected"
OUTCOME_UNSUPPORTED_TOOL = "unsupported_tool"
OUTCOME_INVALID_TOOL_ARGUMENTS = "invalid_tool_arguments"
OUTCOME_TOOL_ABSENT = "tool_call_absent"
OUTCOME_TOOL_BUDGET = "tool_budget_exhausted"
OUTCOME_INELIGIBLE_INPUT_BOUND = "ineligible_input_bound"
OUTCOME_SKIPPED = "skipped"
OUTCOME_CANCELLED = "cancelled"
OUTCOME_UNKNOWN = "unknown"

ENDPOINT_FAILURE_OUTCOMES = frozenset(
    {
        OUTCOME_TIMEOUT,
        OUTCOME_RATE_LIMITED,
        OUTCOME_SERVER_ERROR,
        OUTCOME_CLIENT_ERROR,
        OUTCOME_PROTOCOL_FAILURE,
        OUTCOME_STREAM_INTERRUPTED,
        OUTCOME_CONTEXT_REJECTED,
    }
)

SESSION_STATUS_COMPLETED = "completed"
SESSION_STATUS_CENSORED = "censored"
SESSION_STATUS_FAILED = "failed"
SESSION_STATUS_INELIGIBLE_INPUT_BOUND = "ineligible_input_bound"
SESSION_STATUS_UNSUPPORTED_TOOL = "unsupported_tool"
SESSION_STATUS_BUDGET_EXHAUSTED = "budget_exhausted"
SESSION_STATUS_RATE_LIMITED = "rate_limited"
SESSION_STATUS_DEADLINE = "deadline"
SESSION_STATUS_INTERRUPTED = "interrupted"

CELL_STATUS_COMPLETED = "completed"
CELL_STATUS_PARTIAL = "partial"
CELL_STATUS_NOT_MEASURED = "not_measured"
CELL_STATUS_STOPPED_RATE_LIMIT = "stopped_rate_limit"
CELL_STATUS_STOPPED_INVALID_EVIDENCE = "stopped_invalid_evidence"
CELL_STATUS_STOPPED_DEADLINE = "stopped_deadline"
CELL_STATUS_STOPPED_TOTAL_LIMIT = "stopped_total_limit"

_ABORT_OUTCOMES = frozenset(
    {
        OUTCOME_RATE_LIMITED,
        OUTCOME_TIMEOUT,
        OUTCOME_SERVER_ERROR,
        OUTCOME_PROTOCOL_FAILURE,
        OUTCOME_STREAM_INTERRUPTED,
        OUTCOME_CLIENT_ERROR,
        OUTCOME_CONTEXT_REJECTED,
    }
)

#: Outcomes that came from a complete response and must not be overwritten by a
#: deadline that fires while the connection is being cleaned up.
_COMPLETED_OUTCOMES = frozenset(
    {
        OUTCOME_SUCCESS,
        OUTCOME_OUTPUT_TRUNCATED,
        OUTCOME_RATE_LIMITED,
        OUTCOME_SERVER_ERROR,
        OUTCOME_CLIENT_ERROR,
        OUTCOME_CONTEXT_REJECTED,
        OUTCOME_UNSUPPORTED_TOOL,
        OUTCOME_INVALID_TOOL_ARGUMENTS,
        OUTCOME_TOOL_ABSENT,
        OUTCOME_TOOL_BUDGET,
    }
)

#: Deterministic in-process tool fixtures. No path is read from the host
#: filesystem; model-supplied paths are looked up in this map only.
TOOL_FILES: dict[str, str] = {
    "/tmp/example.txt": "fixture alpha beta gamma\n",
    "/fixtures/data.csv": "id,value\n1,alpha\n2,beta\n3,gamma\n4,delta\n",
    "/fixtures/notes.md": (
        "# Session notes\n"
        "BUILD_ID=fixture-build\n"
        "MIGRATION_DEADLINE=2031-04-06\n"
        "The rollback code is RB-fixture.\n"
    ),
    "/fixtures/src/app.py": "def run() -> int:\n    return 42\n",
}
TOOL_DIRECTORIES: dict[str, tuple[str, ...]] = {
    "/fixtures": ("data.csv", "notes.md", "src"),
    "/fixtures/src": ("app.py",),
}
TOOL_DEFINITIONS = (READ_FILE_TOOL, LIST_DIR_TOOL)


class SessionEngineError(ValueError):
    """Raised for invalid engine configuration, not for endpoint behavior."""


# -- evidence records ---------------------------------------------------------


def _finite_or_none(value: object, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SessionEngineError(f"{name} must be a finite number or null")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise SessionEngineError(f"{name} must be finite and non-negative")
    return number


def _int_or_none(value: object, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SessionEngineError(f"{name} must be a non-negative integer or null")
    return value


@dataclass
class CallEvidence:
    """One model call, including transport, timing, usage and reply content."""

    operation_id: str
    scenario: str
    phase: str
    window: int | None
    concurrent_sessions: int
    session_id: str
    session_index: int
    template: str
    group_id: str
    prefix_group_id: str | None
    turn_group_id: str | None
    turn_index: int
    request_id: str
    seed: int

    connection_id: str | None = None
    reconnected: bool = False
    connection_reused: bool = False
    connection_measured: bool = False
    connection_opened: bool = False
    connection_prior_closed: bool = False
    connection_reuse_error: str | None = None
    connection_addresses: int | None = None
    connection_resolve_ms: float | None = None
    connection_connect_ms: float | None = None
    connections_opened_total: int = 0
    queue_ms: float | None = None
    idle_before_ms: float | None = None
    ttft_any_ms: float | None = None
    ttft_reasoning_ms: float | None = None
    ttft_visible_ms: float | None = None
    active_ms: float | None = None
    started_at_utc: str | None = None
    ended_at_utc: str | None = None

    request_bytes: int | None = None
    messages_chars: int | None = None
    request_body_sha256: str | None = None
    messages_sha256: str | None = None
    leading_content_sha256: str | None = None
    leading_content_chars: int | None = None
    leading_marker_sha256: str | None = None
    namespace_label: str | None = None
    namespace_marker_sha256: str | None = None
    requested_input_tokens: int | None = None
    client_estimated_input_tokens: int | None = None
    provider_input_tokens: int | None = None
    provider_output_tokens: int | None = None
    provider_reasoning_tokens: int | None = None
    provider_visible_output_tokens: int | None = None
    delivered_size_state: str = "unknown"
    delivered_size_conformance: bool | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None

    http_status: int | None = None
    error_type: str | None = None
    error_message: str | None = None
    finish_reason: str | None = None
    finish_class: str = FINISH_ABSENT
    outcome: str = OUTCOME_UNKNOWN
    outcome_detail: str | None = None
    attribution: str = "unknown"

    visible_content: str = ""
    visible_content_sha256: str | None = None
    visible_chars: int = 0
    reasoning_chars: int = 0
    visible_missing: bool = True
    truncated: bool = False
    usable_visible_response: bool = False
    input_bound_exceeded: bool = False
    input_bound_limit: int | None = None
    sse_done_seen: bool | None = None

    request_headers: dict[str, str] = field(default_factory=dict)
    cache_group: str | None = None
    tools_sent: bool = False
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    unsupported_tool: str | None = None
    evidence_gaps: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "session-call/1.0.0",
            "operation_id": self.operation_id,
            "scenario": self.scenario,
            "phase": self.phase,
            "window": self.window,
            "concurrent_sessions": self.concurrent_sessions,
            "session_id": self.session_id,
            "session_index": self.session_index,
            "template": self.template,
            "group_id": self.group_id,
            "prefix_group_id": self.prefix_group_id,
            "turn_group_id": self.turn_group_id,
            "turn_index": self.turn_index,
            "request_id": self.request_id,
            "seed": self.seed,
            "connection": {
                "connection_id": self.connection_id,
                "reconnected": self.reconnected,
                "reused": self.connection_reused,
                "measured": self.connection_measured,
                "opened": self.connection_opened,
                "prior_closed": self.connection_prior_closed,
                "reuse_error": self.connection_reuse_error,
                "addresses": self.connection_addresses,
                "resolve_ms": self.connection_resolve_ms,
                "connect_ms": self.connection_connect_ms,
                "opened_total": self.connections_opened_total,
            },
            "timing": {
                "clock": "time.perf_counter_monotonic",
                "queue_ms": self.queue_ms,
                "idle_before_ms": self.idle_before_ms,
                "ttft_any_ms": self.ttft_any_ms,
                "ttft_reasoning_ms": self.ttft_reasoning_ms,
                "ttft_visible_ms": self.ttft_visible_ms,
                "active_ms": self.active_ms,
                "started_at_utc": self.started_at_utc,
                "ended_at_utc": self.ended_at_utc,
            },
            "input": {
                "request_bytes": self.request_bytes,
                "messages_chars": self.messages_chars,
                "request_body_sha256": self.request_body_sha256,
                "messages_sha256": self.messages_sha256,
                "leading_content_sha256": self.leading_content_sha256,
                "leading_content_chars": self.leading_content_chars,
                "leading_marker_sha256": self.leading_marker_sha256,
                "namespace_label": self.namespace_label,
                "namespace_marker_sha256": self.namespace_marker_sha256,
                "requested_input_tokens": self.requested_input_tokens,
                "client_estimated_input_tokens": self.client_estimated_input_tokens,
                "provider_input_tokens": self.provider_input_tokens,
                "input_bound_limit": self.input_bound_limit,
                "input_bound_exceeded": self.input_bound_exceeded,
                "delivered_size_state": self.delivered_size_state,
                "delivered_size_conformance": self.delivered_size_conformance,
            },
            "output": {
                "provider_output_tokens": self.provider_output_tokens,
                "provider_reasoning_tokens": self.provider_reasoning_tokens,
                "provider_visible_output_tokens": self.provider_visible_output_tokens,
                "cache_read_tokens": self.cache_read_tokens,
                "cache_write_tokens": self.cache_write_tokens,
                "visible_chars": self.visible_chars,
                "visible_content_sha256": self.visible_content_sha256,
                "reasoning_chars": self.reasoning_chars,
                "visible_missing": self.visible_missing,
                "usable_visible_response": self.usable_visible_response,
                "truncated": self.truncated,
            },
            "transport": {
                "http_status": self.http_status,
                "error_type": self.error_type,
                "error_message": self.error_message,
                "sse_done_seen": self.sse_done_seen,
            },
            "completion": {
                "finish_reason": self.finish_reason,
                "finish_class": self.finish_class,
            },
            "outcome": self.outcome,
            "outcome_detail": self.outcome_detail,
            "attribution": self.attribution,
            "headers": dict(sorted(self.request_headers.items())),
            "cache_group": self.cache_group,
            "tools": {
                "tools_sent": self.tools_sent,
                "tool_calls": self.tool_calls,
                "tool_results": self.tool_results,
                "unsupported_tool": self.unsupported_tool,
            },
            "evidence_gaps": list(self.evidence_gaps),
            "notes": list(self.notes),
        }

    def validate(self) -> None:
        for name in (
            "queue_ms",
            "idle_before_ms",
            "ttft_any_ms",
            "ttft_reasoning_ms",
            "ttft_visible_ms",
            "active_ms",
        ):
            _finite_or_none(getattr(self, name), name)
        for name in (
            "request_bytes",
            "messages_chars",
            "leading_content_chars",
            "requested_input_tokens",
            "client_estimated_input_tokens",
            "provider_input_tokens",
            "provider_output_tokens",
            "provider_reasoning_tokens",
            "provider_visible_output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
        ):
            _int_or_none(getattr(self, name), name)
        for name in (
            "request_body_sha256",
            "messages_sha256",
            "leading_content_sha256",
            "leading_marker_sha256",
            "namespace_marker_sha256",
        ):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, str)
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise SessionEngineError(f"{self.request_id}: {name} must be a SHA-256 hex digest")
        if self.delivered_size_conformance is not None and not isinstance(
            self.delivered_size_conformance, bool
        ):
            raise SessionEngineError(
                f"{self.request_id}: delivered_size_conformance must be boolean or null"
            )
        if self.finish_class != classify_finish_reason(self.finish_reason):
            raise SessionEngineError(
                f"{self.request_id}: finish_class contradicts finish_reason"
            )
        if self.outcome == OUTCOME_SUCCESS:
            if self.http_status is None or not 200 <= self.http_status < 300:
                raise SessionEngineError(
                    f"{self.request_id}: success requires an observed 2xx status"
                )
            if self.finish_class != FINISH_NATURAL:
                raise SessionEngineError(
                    f"{self.request_id}: success requires a natural finish"
                )
            if (self.visible_missing or not self.visible_content.strip()) and not self.tool_calls:
                raise SessionEngineError(
                    f"{self.request_id}: success without visible content requires a tool call"
                )
            if self.truncated:
                raise SessionEngineError(
                    f"{self.request_id}: truncated call cannot be a success"
                )
        if self.outcome == OUTCOME_OUTPUT_TRUNCATED:
            if self.finish_class != FINISH_CEILING:
                raise SessionEngineError(
                    f"{self.request_id}: output_truncated requires finish_class ceiling"
                )
            if self.http_status is None or not 200 <= self.http_status < 300:
                raise SessionEngineError(
                    f"{self.request_id}: output_truncated requires an observed 2xx"
                )
        if self.outcome in (OUTCOME_SKIPPED, OUTCOME_INELIGIBLE_INPUT_BOUND):
            if self.http_status is not None:
                raise SessionEngineError(
                    f"{self.request_id}: {self.outcome} must not record an HTTP status"
                )
        if self.visible_missing != (not self.visible_content.strip()):
            raise SessionEngineError(
                f"{self.request_id}: visible_missing contradicts visible content"
            )
        if self.visible_content and self.visible_content_sha256 != hashlib.sha256(
            self.visible_content.encode("utf-8")
        ).hexdigest():
            raise SessionEngineError(
                f"{self.request_id}: visible content hash does not match content"
            )
        if self.input_bound_exceeded and self.input_bound_limit is None:
            raise SessionEngineError(
                f"{self.request_id}: input bound exceeded without a limit"
            )


@dataclass
class SessionEvidence:
    session_id: str
    session_index: int
    template: str
    group_id: str
    seed: int
    scenario: str
    operation_id: str
    status: str = SESSION_STATUS_FAILED
    stop_reason: str | None = None
    planned_calls: int = 4
    attempted_calls: int = 0
    calls: list[CallEvidence] = field(default_factory=list)
    transcript_sha256: str | None = None
    transcript_messages: int = 0
    session_started_at_utc: str | None = None
    session_ended_at_utc: str | None = None
    elapsed_ms: float | None = None
    active_ms: float | None = None
    idle_ms: float | None = None
    queue_ms: float | None = None
    compaction: dict[str, Any] | None = None
    notes: list[str] = field(default_factory=list)
    transcript: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "session-record/1.0.0",
            "session_id": self.session_id,
            "session_index": self.session_index,
            "template": self.template,
            "group_id": self.group_id,
            "seed": self.seed,
            "scenario": self.scenario,
            "operation_id": self.operation_id,
            "status": self.status,
            "stop_reason": self.stop_reason,
            "planned_calls": self.planned_calls,
            "attempted_calls": self.attempted_calls,
            "calls": [call.to_dict() for call in self.calls],
            "transcript": {
                "sha256": self.transcript_sha256,
                "message_count": self.transcript_messages,
                "turns": list(self.transcript),
            },
            "timing": {
                "started_at_utc": self.session_started_at_utc,
                "ended_at_utc": self.session_ended_at_utc,
                "elapsed_ms": self.elapsed_ms,
                "active_ms": self.active_ms,
                "idle_ms": self.idle_ms,
                "queue_ms": self.queue_ms,
            },
            "compaction": self.compaction,
            "notes": list(self.notes),
        }


@dataclass
class CellEvidence:
    operation_id: str
    scenario: str
    phase: str
    window: int | None
    concurrency: int
    planned_sessions: int
    planned_calls: int
    workload_type: str = WORKLOAD_LIVE_TURN_SESSION
    attempted_calls: int = 0
    status: str = CELL_STATUS_PARTIAL
    stop_reason: str | None = None
    started_at_utc: str | None = None
    ended_at_utc: str | None = None
    elapsed_ms: float | None = None
    achieved_max_concurrent_sessions: int = 0
    achieved_max_inflight_calls: int = 0
    planned_turn_order: list[str] = field(default_factory=list)
    actual_turn_order: list[str] = field(default_factory=list)
    sessions: list[SessionEvidence] = field(default_factory=list)
    context_checks: list[dict[str, Any]] = field(default_factory=list)
    cache_prefix_audit: dict[str, Any] | None = None
    evidence_complete: bool = False
    notes: list[str] = field(default_factory=list)

    def counters(self) -> dict[str, int]:
        calls = [call for session in self.sessions for call in session.calls]
        outcomes: dict[str, int] = {}
        for call in calls:
            outcomes[call.outcome] = outcomes.get(call.outcome, 0) + 1
        attempted = sum(
            1
            for call in calls
            if call.outcome not in (OUTCOME_SKIPPED, OUTCOME_INELIGIBLE_INPUT_BOUND)
        )
        non_success = {
            outcome: count
            for outcome, count in outcomes.items()
            if outcome
            not in (
                OUTCOME_SUCCESS,
                OUTCOME_OUTPUT_TRUNCATED,
                OUTCOME_SKIPPED,
                OUTCOME_INELIGIBLE_INPUT_BOUND,
            )
        }
        return {
            "planned_calls": self.planned_calls,
            "attempted_calls": attempted,
            "successful_calls": outcomes.get(OUTCOME_SUCCESS, 0),
            "truncated_calls": outcomes.get(OUTCOME_OUTPUT_TRUNCATED, 0),
            "failed_calls": sum(non_success.values()),
            "endpoint_failed_calls": sum(
                count for outcome, count in outcomes.items() if outcome in ENDPOINT_FAILURE_OUTCOMES
            ),
            "unsupported_tool_calls": outcomes.get(OUTCOME_UNSUPPORTED_TOOL, 0),
            "invalid_tool_argument_calls": outcomes.get(OUTCOME_INVALID_TOOL_ARGUMENTS, 0),
            "skipped_calls": outcomes.get(OUTCOME_SKIPPED, 0)
            + outcomes.get(OUTCOME_INELIGIBLE_INPUT_BOUND, 0),
            "successful_sessions": sum(
                1 for session in self.sessions if session.status == SESSION_STATUS_COMPLETED
            ),
            "censored_sessions": sum(
                1 for session in self.sessions if session.status == SESSION_STATUS_CENSORED
            ),
            "failed_sessions": sum(
                1
                for session in self.sessions
                if session.status
                in (
                    SESSION_STATUS_FAILED,
                    SESSION_STATUS_RATE_LIMITED,
                    SESSION_STATUS_UNSUPPORTED_TOOL,
                    SESSION_STATUS_BUDGET_EXHAUSTED,
                )
            ),
            "ineligible_sessions": sum(
                1
                for session in self.sessions
                if session.status == SESSION_STATUS_INELIGIBLE_INPUT_BOUND
            ),
            "context_demonstrated_effective": sum(
                1 for check in self.context_checks if check["demonstrated_effective"]
            ),
            "context_api_accepted": sum(
                1 for check in self.context_checks if check["api_accepted"]
            ),
            "context_refused": sum(
                1 for check in self.context_checks if check["classification"] == "refused"
            ),
            "context_truncated": sum(
                1 for check in self.context_checks if check["classification"] == "output_truncated"
            ),
            "context_unverified": sum(
                1
                for check in self.context_checks
                if check["classification"] == "api_accepted_but_unverified"
            ),
            "context_malformed_response": sum(
                1
                for check in self.context_checks
                if check["classification"] == "malformed_response"
            ),
            "http_attempts": sum(
                1 for call in calls if call.http_status is not None or call.error_type is not None
            ),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "session-cell/1.0.0",
            "operation_id": self.operation_id,
            "workload_type": self.workload_type,
            "scenario": self.scenario,
            "phase": self.phase,
            "window": self.window,
            "concurrency": self.concurrency,
            "planned_sessions": self.planned_sessions,
            "planned_calls": self.planned_calls,
            "status": self.status,
            "stop_reason": self.stop_reason,
            "started_at_utc": self.started_at_utc,
            "ended_at_utc": self.ended_at_utc,
            "elapsed_ms": self.elapsed_ms,
            "achieved_max_concurrent_sessions": self.achieved_max_concurrent_sessions,
            "achieved_max_inflight_calls": self.achieved_max_inflight_calls,
            "planned_turn_order": list(self.planned_turn_order),
            "actual_turn_order": list(self.actual_turn_order),
            "evidence_complete": self.evidence_complete,
            "cache_prefix_audit": self.cache_prefix_audit,
            "counters": self.counters(),
            "sessions": [session.to_dict() for session in self.sessions],
            "context_checks": list(self.context_checks),
            "notes": list(self.notes),
        }


# -- journal ------------------------------------------------------------------


class NullJournal:
    """Journal used by offline tests and dry runs."""

    def attempt_started(self, request_id: str, metadata: dict[str, Any]) -> None:
        return None

    def attempt_finished(self, call: CallEvidence) -> None:
        return None


# -- transport ----------------------------------------------------------------


@dataclass(frozen=True)
class SessionTarget:
    base_url: str
    model_id: str
    api_key: str | None = None
    timeout_s: float = 240.0
    max_output_tokens: int = 8192
    streaming: bool = True
    cache_group_header: str = "X-Cache-Group"
    session_header: str = "X-Session-Id"
    extra_headers: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.timeout_s <= 0:
            raise SessionEngineError("timeout_s must be positive")
        if self.max_output_tokens <= 0:
            raise SessionEngineError("max_output_tokens must be positive")
        if self.cache_group_header.lower() == "x-session-affinity":
            raise SessionEngineError("this program must keep session affinity disabled")
        parsed = urlparse(self.base_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise SessionEngineError(f"invalid base_url `{self.base_url}`")


def _open_connection(
    target: SessionTarget, *, timeout_s: float | None = None
) -> http.client.HTTPConnection:
    parsed = urlparse(target.base_url)
    connection_class = (
        http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    )
    return connection_class(
        parsed.hostname,
        parsed.port,
        timeout=target.timeout_s if timeout_s is None else timeout_s,
    )


# A resolver is a fresh interpreter, never Python code after a threaded fork.
# Process creation is an OS operation without a Python cancellation hook. Its
# elapsed time consumes this same deadline; it never receives a fresh budget.
_RESOLVER_REAP_GRACE_S = 0.2


def _start_resolver(host: str, port: int) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, "-I", str(Path(__file__).with_name("resolver_worker.py")),
         host, str(port)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


def _close_resolver(process: subprocess.Popen[bytes]) -> None:
    """Terminate and reap only this call's helper, on every exit path."""
    try:
        if process.poll() is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=_RESOLVER_REAP_GRACE_S)
        except subprocess.TimeoutExpired as error:
            raise SessionEngineError(
                f"resolver_cleanup_failed:pid={process.pid}"
            ) from error
    finally:
        if process.stdout is not None:
            process.stdout.close()


def _resolve_addresses_bounded(
    host: str,
    port: int,
    *,
    timeout_s: float,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[list[tuple[int, int, int, tuple[Any, ...]]], float]:
    """Use one absolute budget for helper startup, IPC and resolution.

    Timeout, cancellation, EOF, abnormal exit and IPC errors all terminate/reap
    the owned helper. Kill/reap has a separate finite 0.2-second cleanup grace;
    a cleanup failure aborts as a harness error, never an endpoint result.
    """
    if timeout_s <= 0:
        raise TimeoutError("resolution_budget_exhausted")
    started = clock()
    deadline = started + timeout_s
    process = _start_resolver(host, port)
    try:
        remaining = deadline - clock()
        if remaining <= 0:
            raise TimeoutError("resolution_budget_exhausted_during_startup")
        try:
            payload, _ = process.communicate(timeout=remaining)
        except subprocess.TimeoutExpired as error:
            raise TimeoutError("resolution_deadline") from error
        if deadline - clock() <= 0:
            raise TimeoutError("resolution_deadline")
        if process.returncode != 0:
            raise ConnectionError(f"resolver exited with status {process.returncode}")
    finally:
        _close_resolver(process)
    if not payload:
        raise ConnectionError("resolution returned no result")
    try:
        document = json.loads(payload)
        if not isinstance(document, dict):
            raise ValueError("result is not an object")
        if "error" in document:
            raise ConnectionError(f"resolution failed: {document['error']}")
        raw_addresses = document.get("addresses")
        if not isinstance(raw_addresses, list) or not raw_addresses:
            raise ValueError("result has no address list")
        addresses = []
        for item in raw_addresses:
            if not isinstance(item, list) or len(item) != 4:
                raise ValueError("malformed address record")
            family, socktype, protocol, sockaddr = item
            if any(not isinstance(v, int) or isinstance(v, bool)
                   for v in (family, socktype, protocol)):
                raise ValueError("malformed address type")
            if (family not in (socket.AF_INET, socket.AF_INET6)
                    or socktype != socket.SOCK_STREAM
                    or protocol not in (0, socket.IPPROTO_TCP)):
                raise ValueError("unsupported address type")
            if (not isinstance(sockaddr, list)
                    or len(sockaddr) != (2 if family == socket.AF_INET else 4)
                    or not isinstance(sockaddr[0], str)
                    or sockaddr[1] != port):
                raise ValueError("malformed socket address")
            # getaddrinfo must have produced a numeric address; never resolve a
            # hostname again inside socket.connect outside the helper budget.
            socket.inet_pton(family, sockaddr[0].split("%", 1)[0])
            addresses.append((family, socktype, protocol, tuple(sockaddr)))
    except (ValueError, TypeError, UnicodeError, OSError) as error:
        raise ConnectionError(f"resolution returned malformed output: {error}") from error
    return addresses, (clock() - started) * 1000.0


def _bounded_socket_factory(
    connection: http.client.HTTPConnection,
    addresses: list[tuple[int, int, int, tuple[Any, ...]]],
    *,
    clock: Callable[[], float],
    deadline: float,
) -> Callable[..., socket.socket]:
    """Build a `socket.create_connection` stand-in from resolved addresses.

    The socket is published on `connection.sock` before the TCP handshake so the
    cancellation guard can shut it down mid-connect. Every attempt shares the
    call's single remaining budget; there is no retry beyond the address list the
    resolver returned.
    """

    def create(address: Any, timeout: Any = None, source_address: Any = None) -> socket.socket:
        errors: list[OSError] = []
        for family, socktype, protocol, sockaddr in addresses:
            remaining = deadline - clock()
            if remaining <= 0:
                raise TimeoutError("connect_deadline_budget_exhausted")
            if isinstance(timeout, (int, float)) and not isinstance(timeout, bool):
                remaining = min(remaining, float(timeout))
            sock = socket.socket(family, socktype, protocol)
            connection.sock = sock
            try:
                sock.settimeout(max(0.001, remaining))
                if source_address is not None:
                    sock.bind(source_address)
                sock.connect(sockaddr)
                return sock
            except TimeoutError:
                sock.close()
                connection.sock = None
                raise
            except OSError as error:
                sock.close()
                connection.sock = None
                errors.append(error)
                if deadline - clock() <= 0:
                    raise
        if errors:
            raise errors[-1]
        raise OSError(f"no usable address for `{connection.host}`")

    return create


def _peer_closed(sock: Any) -> bool:
    """Report whether an idle keep-alive socket was closed by the peer.

    A zero-timeout peek costs no network round trip and consumes nothing. TLS
    sockets reject `MSG_PEEK`, so their liveness is established by the send
    result instead: a failed send on a reused TLS socket is recorded, never
    retried.
    """
    if not isinstance(sock, socket.socket) or isinstance(sock, ssl.SSLSocket):
        return False
    try:
        readable, _, _ = select.select([sock], [], [], 0)
    except (OSError, ValueError):
        return True
    if not readable:
        return False
    try:
        return sock.recv(1, socket.MSG_PEEK) == b""
    except (BlockingIOError, InterruptedError):
        return False
    except (OSError, ValueError):
        return True


def _sanitized_headers(headers: dict[str, str]) -> dict[str, str]:
    sanitized: dict[str, str] = {}
    for name, value in headers.items():
        lowered = name.lower()
        if any(
            marker in lowered
            for marker in ("authorization", "api-key", "apikey", "token", "secret", "cookie")
        ):
            sanitized[name] = "<redacted>"
        else:
            sanitized[name] = value
    return sanitized


@dataclass
class _TransportTrace:
    """What one call observed about its transport, not what the plan intended."""

    measured: bool = False
    opened: bool = False
    reused: bool = False
    prior_closed: bool = False
    reuse_error: str | None = None
    addresses: int | None = None
    resolve_ms: float | None = None
    connect_ms: float | None = None
    opened_total: int = 0


@dataclass
class _StreamState:
    visible_parts: list[str] = field(default_factory=list)
    reasoning_parts: list[str] = field(default_factory=list)
    tool_slots: dict[int, AssembledToolCall] = field(default_factory=dict)
    finish_reason: str | None = None
    saw_done: bool = False
    saw_event: bool = False
    first_any: float | None = None
    first_reasoning: float | None = None
    first_visible: float | None = None
    usage_seen: bool = False
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None


def _apply_usage(state: _StreamState, document: dict[str, Any]) -> None:
    usage = document.get("usage")
    if not isinstance(usage, dict):
        return
    state.usage_seen = True
    if isinstance(usage.get("prompt_tokens"), int) and not isinstance(
        usage.get("prompt_tokens"), bool
    ):
        state.prompt_tokens = usage["prompt_tokens"]
    if isinstance(usage.get("completion_tokens"), int) and not isinstance(
        usage.get("completion_tokens"), bool
    ):
        state.completion_tokens = usage["completion_tokens"]
    details = usage.get("completion_tokens_details")
    if isinstance(details, dict) and isinstance(details.get("reasoning_tokens"), int):
        state.reasoning_tokens = details["reasoning_tokens"]
    prompt_details = usage.get("prompt_tokens_details")
    if isinstance(prompt_details, dict):
        cached = prompt_details.get("cached_tokens")
        if isinstance(cached, int) and not isinstance(cached, bool):
            state.cache_read_tokens = cached
        created = prompt_details.get("cache_creation_tokens")
        if isinstance(created, int) and not isinstance(created, bool):
            state.cache_write_tokens = created
    for key in ("cache_read_input_tokens", "cache_read_tokens"):
        if isinstance(usage.get(key), int) and not isinstance(usage.get(key), bool):
            state.cache_read_tokens = usage[key]
    for key in ("cache_creation_input_tokens", "cache_write_tokens"):
        if isinstance(usage.get(key), int) and not isinstance(usage.get(key), bool):
            state.cache_write_tokens = usage[key]


class _SSEReader:
    """Incremental SSE reader with per-event timing and fragmented-line support.

    `readline()` may split a JSON object across TCP packets; buffering until a
    newline is what makes the parse independent of packetization. Multiple
    `data:` lines in one event are joined with a newline, per the SSE grammar.
    """

    def __init__(
        self,
        response: Any,
        *,
        started: float,
        clock: Callable[[], float],
        timeout_s: float,
        absolute_deadline: float | None = None,
    ):
        self.response = response
        self.started = started
        self.clock = clock
        self.timeout_s = timeout_s
        self.absolute_deadline = absolute_deadline
        self.buffer = b""
        self.data_lines: list[str] = []

    def _remaining(self) -> float:
        remaining = self.timeout_s - (self.clock() - self.started)
        if self.absolute_deadline is not None:
            remaining = min(remaining, self.absolute_deadline - self.clock())
        return remaining

    def events(self):
        while True:
            remaining = self._remaining()
            if remaining <= 0:
                raise TimeoutError("request_timeout_or_cell_deadline")
            self._set_timeout(max(0.001, remaining))
            try:
                line = self.response.readline()
            except TimeoutError:
                raise
            except (OSError, http.client.HTTPException) as error:
                raise ConnectionError(type(error).__name__) from error
            if not line:
                if self.data_lines:
                    yield "\n".join(self.data_lines)
                    self.data_lines = []
                return
            text = line.decode("utf-8", errors="replace").rstrip("\r\n")
            if text == "":
                if self.data_lines:
                    yield "\n".join(self.data_lines)
                    self.data_lines = []
                continue
            if text.startswith(":"):
                continue
            if text.startswith("data:"):
                self.data_lines.append(text[5:].lstrip(" "))
            # Other SSE fields (event:, id:, retry:) carry no model content here.

    def _set_timeout(self, seconds: float) -> None:
        try:
            self.response.fp.raw._sock.settimeout(seconds)  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            return


class _TransportDeadline:
    """Absolute cancellation bound for one call's owned connection.

    A socket timeout bounds one blocking read, not the total time a peer can keep
    a header or SSE line incomplete by sending one byte before each timeout. This
    timer shuts down only the connection owned by the current call at the earlier
    of the remaining request and cell deadlines, which interrupts connect, send,
    header and body work even while bytes keep arriving.

    `cancel()` joins the timer so a fired callback cannot touch a connection that
    a later turn has reused. If the callback is still alive after the join, the
    caller closes the connection rather than reusing it.
    """

    def __init__(
        self,
        connection: http.client.HTTPConnection,
        *,
        seconds: float,
        cell_bounded: bool,
    ) -> None:
        self.connection = connection
        self.cell_bounded = cell_bounded
        self.fired = threading.Event()
        self._lock = threading.Lock()
        self._armed = True
        self._started = False
        self._socket: Any | None = None
        self._timer = threading.Timer(max(0.0, seconds), self._fire)
        self._timer.daemon = True

    def start(self) -> None:
        self._timer.start()
        self._started = True

    def retarget(self, sock: Any | None) -> None:
        """Follow the socket into the response after headers are parsed.

        `HTTPConnection.getresponse()` sets `connection.sock = None` when the
        response declares `Connection: close`, even though the response's buffered
        reader still owns the live socket. The guard must follow that handoff or
        a dribbling body would escape the absolute bound.
        """
        if sock is None:
            return
        with self._lock:
            self._socket = sock
            fired = self.fired.is_set()
        if fired:
            self._shutdown(sock)

    def watch(self, connection: http.client.HTTPConnection) -> None:
        """Follow a replacement connection object created later in the same call."""
        with self._lock:
            self.connection = connection
            self._socket = None
        self.retarget(getattr(connection, "sock", None))

    def _fire(self) -> None:
        with self._lock:
            if not self._armed:
                return
            self._armed = False
            self.fired.set()
            sock = self._socket or getattr(self.connection, "sock", None)
        if sock is None:
            return
        self._shutdown(sock)

    @staticmethod
    def _shutdown(sock: Any) -> None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def cancel(self) -> None:
        with self._lock:
            self._armed = False
        self._timer.cancel()
        if self._started:
            self._timer.join(timeout=2.0)

    @property
    def join_timed_out(self) -> bool:
        return self._started and self._timer.is_alive()


def _now_ms(started: float, clock: Callable[[], float]) -> float:
    return (clock() - started) * 1000.0


def _response_socket(response: Any) -> Any | None:
    """Return the live socket behind a parsed response, if it is reachable."""
    return getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)


def _response_reusable(response: Any, *, timeout_s: float) -> bool:
    """Drain unread body bytes so the same socket can carry another request.

    An SSE read stops at `[DONE]`; the remaining frame terminator is still on the
    socket. Reusing without draining it would misparse the next response. The
    drain is bounded, and any failure disqualifies the socket instead of
    retrying the request.
    """
    if response.isclosed():
        return True
    sock = _response_socket(response)
    if sock is None:
        return False
    try:
        sock.settimeout(max(0.001, timeout_s))
        response.read()
    except (OSError, ValueError, http.client.HTTPException):
        return False
    return response.isclosed()


def _content_of(delta: dict[str, Any]) -> str:
    content = delta.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # Multimodal deltas are serialized deterministically; only text parts
        # count as visible content.
        return "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    return ""


def _reasoning_of(delta: dict[str, Any]) -> str:
    for key in ("reasoning_content", "reasoning"):
        value = delta.get(key)
        if isinstance(value, str):
            return value
    return ""


# -- session state ------------------------------------------------------------


@dataclass
class _SessionState:
    spec: dict[str, Any]
    template: Any
    messages: list[dict[str, Any]]
    transcript_log: list[dict[str, Any]]
    evidence: SessionEvidence
    connection: http.client.HTTPConnection | None = None
    connection_id: str = "c1"
    connection_serial: int = 1
    connections_opened: int = 0
    next_turn: int = 1
    started_monotonic: float | None = None
    last_call_end: float | None = None
    active_total_ms: float = 0.0
    idle_total_ms: float = 0.0
    ready_at: float | None = None
    finished: bool = False
    stop: bool = False
    compaction: dict[str, Any] | None = None


def _request_tag(request_id: str) -> str:
    return f"Request tag: {sha256_text(request_id)[:12]}."


def _turn_namespace_label(spec: dict[str, Any], turn: int) -> str:
    labels = spec.get("turn_namespace_labels") or []
    if len(labels) < turn:
        raise SessionEngineError(
            f"{spec.get('session_id')}: no frozen namespace label for turn {turn}; "
            "rebuild the plan before executing"
        )
    label = labels[turn - 1]
    if not isinstance(label, str) or not label:
        raise SessionEngineError(
            f"{spec.get('session_id')}: invalid frozen namespace label"
        )
    return label


def _initial_messages(
    state: _SessionState, scenario: str, request_id: str, namespace_label: str
) -> list[dict[str, Any]]:
    """Build the first request with the frozen leading cache namespace.

    Every paired condition uses the same role structure and the same prefix/task
    sizes. The only deliberate difference is the leading namespace marker:
    unique per request for the independent baseline, shared per session for
    stable-prefix, and explicitly replaced at the invalidation turn.
    """
    template = state.template
    tag = _request_tag(request_id)
    system = {
        "role": "system",
        "content": prefix_with_namespace(template.prefix, namespace_label),
    }
    if scenario == "tool_roundtrip":
        user_content = (
            "Use the provided tools to inspect the fixture. Call read_file on "
            "/fixtures/data.csv, then summarize what you found in one sentence. "
            "Do not use any other tool. " + tag
        )
    else:
        user_content = template.tasks[0] + "\n\n" + tag
    return [system, {"role": "user", "content": user_content}]


def _next_user_task(state: _SessionState, scenario: str, turn: int, request_id: str) -> dict[str, Any]:
    template = state.template
    task = template.tasks[min(turn - 1, len(template.tasks) - 1)]
    return {
        "role": "user",
        "content": task + "\n\n" + _request_tag(request_id),
    }


def _compact_messages(
    state: _SessionState,
    *,
    before_turn: int,
) -> dict[str, Any]:
    """Deterministic local compaction; no inference and no fact loss.

    Keeps the system prefix with an appended summary, the latest assistant reply,
    and the designated facts. Drops older user/assistant pairs. The result is
    verified against every designated fact before it can be sent.
    """
    original = list(state.messages)
    before_chars = len(json.dumps(original))
    facts = state.template.sentinel_facts
    system = original[0] if original and original[0].get("role") == "system" else None
    latest_assistant = next(
        (message for message in reversed(original) if message.get("role") == "assistant"),
        None,
    )
    summary = (
        "Compacted transcript. The earlier turns were replaced by this deterministic "
        "summary; no inference produced it. Designated facts: " + "; ".join(facts) + "."
    )
    compacted: list[dict[str, Any]] = []
    compacted.append(
        {
            "role": "system",
            "content": (system["content"] if system else "") + "\n\n" + summary,
        }
    )
    if latest_assistant is not None:
        compacted.append(
            {"role": "assistant", "content": latest_assistant.get("content") or ""}
        )
    compacted.append(
        {
            "role": "user",
            "content": state.template.tasks[before_turn - 1]
            + "\n\n"
            + _request_tag(state.evidence.session_id + f"-t{before_turn:02d}"),
        }
    )
    state.messages = compacted
    serialized = json.dumps(compacted)
    missing = [fact for fact in facts if fact not in serialized]
    if missing:
        raise SessionEngineError(
            f"{state.evidence.session_id}: compaction lost designated facts {missing}"
        )
    return {
        "kind": "deterministic_local",
        "before_turn": before_turn,
        "before_chars": before_chars,
        "after_chars": len(serialized),
        "before_tokens_estimated": estimate_tokens(json.dumps(original)),
        "after_tokens_estimated": estimate_tokens(serialized),
        "dropped_messages": max(0, len(original) - len(compacted)),
        "preserved_facts": list(facts),
        "verified": True,
        "no_inference": True,
    }


def _execute_tool(name: str, arguments: str) -> tuple[dict[str, Any] | None, str | None]:
    """Run one deterministic in-process tool. No shell, filesystem or network."""
    problem = validate_tool_arguments(name, arguments)
    if problem is not None:
        return None, problem
    parsed = json.loads(arguments)
    if name == "read_file":
        path = parsed["path"]
        if path not in TOOL_FILES:
            return {"ok": False, "error": "not_found", "path": path}, None
        content = TOOL_FILES[path]
        max_bytes = max(0, int(parsed["max_bytes"]))
        encoded = content.encode("utf-8")[:max_bytes]
        return (
            {
                "ok": True,
                "path": path,
                "bytes_read": len(encoded),
                "content": encoded.decode("utf-8", errors="replace"),
            },
            None,
        )
    if name == "list_dir":
        path = parsed["path"]
        entries = TOOL_DIRECTORIES.get(path)
        if entries is None:
            return {"ok": False, "error": "not_found", "path": path}, None
        return {"ok": True, "path": path, "entries": list(entries)}, None
    return None, f"tool_call_unknown_name:{name}"


# -- runner -------------------------------------------------------------------


class SessionCellRunner:
    """Run one cell of the finite plan. No retries and no hidden calls."""

    def __init__(
        self,
        *,
        cell: dict[str, Any],
        target: SessionTarget,
        journal: Any | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        wall_clock: Callable[[], str] = utc_now,
        templates: dict[str, Any] | None = None,
        idle_seconds: float = 60.0,
        max_cell_seconds: float | None = None,
        max_total_attempts: int | None = None,
    ) -> None:
        self.cell = cell
        self.target = target
        self.journal = journal or NullJournal()
        self.clock = clock
        self.sleep = sleep
        self.wall_clock = wall_clock
        self.templates = templates or build_templates()
        self.idle_seconds = idle_seconds
        self.max_cell_seconds = (
            2400.0 if max_cell_seconds is None else max_cell_seconds
        )
        self.max_total_attempts = max_total_attempts
        self._lock = threading.Lock()
        self._active_sessions: set[str] = set()
        self._active_calls = 0
        self._max_sessions = 0
        self._max_calls = 0
        self._attempts = 0
        self._abort = False
        self._abort_reason: str | None = None
        self._cell_start = 0.0
        self._cell_deadline: float | None = None
        self._invalid = False
        self._actual_order: list[str] = []
        self._evidence: CellEvidence | None = None
        self._states: list[_SessionState] = []

    # -- public ------------------------------------------------------------

    def run(self) -> CellEvidence:
        scenario = self.cell["scenario"]
        if scenario == "context_sentinels":
            return self._run_context()
        self._cell_start = self.clock()
        self._cell_deadline = self._cell_start + float(self.max_cell_seconds)
        evidence = CellEvidence(
            operation_id=self.cell["operation_id"],
            scenario=scenario,
            phase=self.cell["phase"],
            window=self.cell.get("window"),
            concurrency=int(self.cell["concurrent_sessions"]),
            workload_type=str(
                self.cell.get("workload_type") or WORKLOAD_LIVE_TURN_SESSION
            ),
            planned_sessions=int(
                self.cell.get("session_count")
                or len(self.cell.get("sessions") or [])
            ),
            planned_calls=int(self.cell["max_inference_attempts"]),
            started_at_utc=self.wall_clock(),
        )
        states = self._build_states(evidence)
        self._evidence = evidence
        self._states = states
        evidence.planned_turn_order = self._planned_turn_order(states)
        try:
            if scenario == "interleaved_sessions":
                self._run_interleaved(states, evidence)
            else:
                self._run_batched(states, evidence)
        finally:
            for state in states:
                self._close(state)
        evidence.ended_at_utc = self.wall_clock()
        evidence.elapsed_ms = _now_ms(self._cell_start, self.clock)
        evidence.attempted_calls = self._attempts
        evidence.achieved_max_concurrent_sessions = self._max_sessions
        evidence.achieved_max_inflight_calls = self._max_calls
        evidence.actual_turn_order = list(self._actual_order)
        evidence.sessions = [state.evidence for state in states]
        evidence.cache_prefix_audit = self._cache_prefix_audit(evidence)
        if self._abort:
            evidence.stop_reason = self._abort_reason
        if self._invalid:
            evidence.status = CELL_STATUS_STOPPED_INVALID_EVIDENCE
        elif self._abort_reason == "rate_limit_exhausted":
            evidence.status = CELL_STATUS_STOPPED_RATE_LIMIT
        elif self._abort_reason == "cell_deadline":
            evidence.status = CELL_STATUS_STOPPED_DEADLINE
        elif self._abort_reason == "total_attempt_limit":
            evidence.status = CELL_STATUS_STOPPED_TOTAL_LIMIT
        elif self._attempts == 0:
            evidence.status = CELL_STATUS_NOT_MEASURED
        elif all(
            session.status
            in (
                SESSION_STATUS_COMPLETED,
                SESSION_STATUS_CENSORED,
                SESSION_STATUS_INELIGIBLE_INPUT_BOUND,
                SESSION_STATUS_UNSUPPORTED_TOOL,
                SESSION_STATUS_BUDGET_EXHAUSTED,
            )
            for session in evidence.sessions
        ):
            evidence.status = CELL_STATUS_COMPLETED
        else:
            evidence.status = CELL_STATUS_PARTIAL
        for session in evidence.sessions:
            for call in session.calls:
                call.validate()
        skipped = evidence.counters()["skipped_calls"]
        evidence.evidence_complete = (
            evidence.attempted_calls + skipped == evidence.planned_calls
        )
        if evidence.status == CELL_STATUS_COMPLETED and evidence.stop_reason is None:
            evidence.stop_reason = "completed"
        elif evidence.status == CELL_STATUS_NOT_MEASURED and evidence.stop_reason is None:
            evidence.stop_reason = "all_calls_ineligible_input_bound"
        return evidence

    def _cache_prefix_audit(self, evidence: CellEvidence) -> dict[str, Any]:
        """Prove equality/inequality from the bytes actually sent.

        Grouping headers are recorded for diagnosis only. The checks below use
        the SHA-256 of the first message content and of its leading marker.
        """
        calls = [
            call
            for session in evidence.sessions
            for call in session.calls
            if call.leading_content_sha256 is not None and call.http_status is not None
        ]
        checks: list[dict[str, Any]] = []
        marker_matches = all(
            call.leading_marker_sha256 == call.namespace_marker_sha256
            for call in calls
            if call.namespace_marker_sha256 is not None
        )
        checks.append(
            {
                "check": "frozen_marker_matches_sent_bytes",
                "passed": bool(calls) and marker_matches,
                "calls": len(calls),
            }
        )
        scenario = evidence.scenario
        if scenario in ("independent", "context_sentinels"):
            unique = len({call.leading_content_sha256 for call in calls}) == len(calls)
            checks.append(
                {
                    "check": "unique_leading_content_per_request",
                    "passed": unique,
                    "unique_hashes": len({call.leading_content_sha256 for call in calls}),
                    "calls": len(calls),
                }
            )
        else:
            by_session: dict[str, list[CallEvidence]] = {}
            for call in calls:
                by_session.setdefault(call.session_id, []).append(call)
            for session_id, session_calls in sorted(by_session.items()):
                ordered = sorted(session_calls, key=lambda call: call.turn_index)
                if scenario in ("prefix_invalidation", "explicit_compaction"):
                    before = {
                        call.leading_content_sha256
                        for call in ordered
                        if call.turn_index <= 2
                    }
                    after = {
                        call.leading_content_sha256
                        for call in ordered
                        if call.turn_index >= 3
                    }
                    passed = (
                        len(before) == 1
                        and len(after) == 1
                        and before != after
                    )
                    checks.append(
                        {
                            "check": "explicit_early_change_at_turn_3",
                            "session_id": session_id,
                            "passed": passed,
                            "before_turns_1_2_hashes": sorted(before),
                            "after_turns_3_4_hashes": sorted(after),
                        }
                    )
                else:
                    unique = {
                        call.leading_content_sha256 for call in ordered
                    }
                    checks.append(
                        {
                            "check": "one_leading_content_per_session",
                            "session_id": session_id,
                            "passed": len(unique) == 1,
                            "hashes": sorted(unique),
                        }
                    )
            first_hashes = [
                min(session_calls, key=lambda call: call.turn_index)
                .leading_content_sha256
                for session_calls in by_session.values()
            ]
            checks.append(
                {
                    "check": "sessions_have_distinct_leading_content",
                    "passed": len(set(first_hashes)) == len(first_hashes),
                    "sessions": len(first_hashes),
                }
            )
        return {
            "consistent": bool(calls) and all(check["passed"] for check in checks),
            "calls_audited": len(calls),
            "headers_used_as_proof": False,
            "checks": checks,
        }

    def snapshot(self) -> CellEvidence | None:
        """Return the in-progress cell evidence for a catchable interruption.

        The journal already preserves attempt-level evidence. This snapshot adds
        the session records written so far so a partial cell can be inspected and
        reconciled without replaying any prior inference.
        """
        if self._evidence is None:
            return None
        evidence = self._evidence
        if not evidence.sessions:
            evidence.sessions = [state.evidence for state in self._states]
        if evidence.status == CELL_STATUS_PARTIAL:
            evidence.stop_reason = evidence.stop_reason or "interrupted"
        evidence.ended_at_utc = self.wall_clock()
        evidence.elapsed_ms = _now_ms(self._cell_start, self.clock)
        evidence.attempted_calls = self._attempts
        evidence.achieved_max_concurrent_sessions = self._max_sessions
        evidence.achieved_max_inflight_calls = self._max_calls
        return evidence

    # -- state -------------------------------------------------------------

    def _build_states(self, evidence: CellEvidence) -> list[_SessionState]:
        scenario = self.cell["scenario"]
        sessions = list(self.cell.get("sessions") or [])
        order = list(self.cell.get("session_order") or [])
        if not order:
            order = [session["session_id"] for session in sessions]
            random.Random(int(self.cell["order_seed"])).shuffle(order)
        by_id = {session["session_id"]: session for session in sessions}
        states: list[_SessionState] = []
        for session_id in order:
            spec = by_id[session_id]
            template = self.templates[spec["template"]]
            session_evidence = SessionEvidence(
                session_id=session_id,
                session_index=int(spec["session_index"]),
                template=spec["template"],
                group_id=spec["group_id"],
                seed=int(spec["seed"]),
                scenario=scenario,
                operation_id=self.cell["operation_id"],
                planned_calls=int(self.cell["model_calls_per_session"]),
            )
            state = _SessionState(
                spec=spec,
                template=template,
                messages=[],
                transcript_log=[],
                evidence=session_evidence,
            )
            state.messages = _initial_messages(
                state,
                scenario,
                f"{session_id}-t01",
                _turn_namespace_label(spec, 1),
            )
            states.append(state)
        return states

    def _planned_turn_order(self, states: list[_SessionState]) -> list[str]:
        declared = list(self.cell.get("turn_order") or [])
        if declared:
            return declared
        if self.cell["scenario"] == "interleaved_sessions":
            return [
                f"{state.spec['session_id']}-t{turn:02d}"
                for turn in range(1, 5)
                for state in states
            ]
        return [
            f"{state.spec['session_id']}-t{turn:02d}"
            for state in states
            for turn in range(1, 5)
        ]

    # -- scheduling --------------------------------------------------------

    def _run_batched(self, states: list[_SessionState], evidence: CellEvidence) -> None:
        concurrency = int(self.cell["concurrent_sessions"])
        pending = list(states)
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures: dict[Future[None], _SessionState] = {}
            while pending and not self._abort:
                while pending and len(futures) < concurrency:
                    state = pending.pop(0)
                    self._session_enter(state)
                    futures[pool.submit(self._run_session, state)] = state
                if not futures:
                    break
                done, _ = wait(futures, return_when=FIRST_COMPLETED)
                for future in done:
                    state = futures.pop(future)
                    try:
                        future.result()
                    except Exception as error:  # keep partial evidence, stop the cell
                        state.evidence.status = SESSION_STATUS_INTERRUPTED
                        state.evidence.stop_reason = f"harness_error:{type(error).__name__}"
                        self._abort = True
                        self._abort_reason = "harness_error"
                        state.evidence.notes.append(str(error))
                    finally:
                        self._session_exit(state)
            for future, state in list(futures.items()):
                future.result()
                self._session_exit(state)
            self._skip_pending(pending, "cell_aborted")

    def _run_interleaved(self, states: list[_SessionState], evidence: CellEvidence) -> None:
        concurrency = int(self.cell["concurrent_sessions"])
        pending = list(states)
        ready: list[_SessionState] = []
        active: set[str] = set()
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures: dict[Future[Any], tuple[_SessionState, int]] = {}
            for state in pending[:concurrency]:
                pending.pop(0)
                self._session_enter(state)
                active.add(state.evidence.session_id)
                ready.append(state)
            while ready or futures:
                while ready and len(futures) < concurrency and not self._abort:
                    state = ready.pop(0)
                    turn = state.next_turn
                    state.ready_at = self.clock()
                    futures[pool.submit(self._run_turn_task, state, turn)] = (state, turn)
                if not futures:
                    break
                done, _ = wait(futures, return_when=FIRST_COMPLETED)
                for future in done:
                    state, turn = futures.pop(future)
                    try:
                        should_continue = future.result()
                    except Exception as error:
                        state.evidence.status = SESSION_STATUS_INTERRUPTED
                        state.evidence.stop_reason = f"harness_error:{type(error).__name__}"
                        state.evidence.notes.append(str(error))
                        self._abort = True
                        self._abort_reason = "harness_error"
                        should_continue = False
                    if (
                        should_continue
                        and not self._abort
                        and state.next_turn <= state.evidence.planned_calls
                    ):
                        ready.append(state)
                    else:
                        if should_continue and not self._abort:
                            state.evidence.status = SESSION_STATUS_COMPLETED
                            state.evidence.stop_reason = "completed"
                        self._session_exit(state)
                        active.discard(state.evidence.session_id)
                        if pending and not self._abort:
                            next_state = pending.pop(0)
                            self._session_enter(next_state)
                            active.add(next_state.evidence.session_id)
                            ready.append(next_state)
            self._skip_pending(pending, "cell_aborted")
        evidence.notes.append(f"interleaved_active_sessions={len(active)}")

    def _skip_pending(self, pending: list[_SessionState], reason: str) -> None:
        for state in pending:
            state.evidence.status = SESSION_STATUS_INTERRUPTED
            state.evidence.stop_reason = reason
            state.evidence.session_started_at_utc = self.wall_clock()
            state.evidence.session_ended_at_utc = state.evidence.session_started_at_utc
            state.evidence.elapsed_ms = 0.0
            state.evidence.active_ms = 0.0
            state.evidence.idle_ms = 0.0
            for turn in range(state.next_turn, 5):
                call = self._blank_call(state, turn, OUTCOME_SKIPPED, reason)
                state.evidence.calls.append(call)
            state.evidence.transcript_sha256 = sha256_text(
                json.dumps(state.transcript_log, sort_keys=True)
            )
            state.evidence.transcript_messages = len(state.transcript_log)
            state.evidence.attempted_calls = 0

    def _session_enter(self, state: _SessionState) -> None:
        with self._lock:
            self._active_sessions.add(state.evidence.session_id)
            self._max_sessions = max(self._max_sessions, len(self._active_sessions))
        state.started_monotonic = self.clock()
        state.evidence.session_started_at_utc = self.wall_clock()

    def _session_exit(self, state: _SessionState) -> None:
        with self._lock:
            self._active_sessions.discard(state.evidence.session_id)
        if state.started_monotonic is not None:
            state.evidence.elapsed_ms = _now_ms(state.started_monotonic, self.clock)
        state.evidence.session_ended_at_utc = self.wall_clock()
        state.evidence.active_ms = state.active_total_ms
        state.evidence.idle_ms = state.idle_total_ms
        state.evidence.attempted_calls = sum(
            1
            for call in state.evidence.calls
            if call.outcome not in (OUTCOME_SKIPPED, OUTCOME_INELIGIBLE_INPUT_BOUND)
        )
        state.evidence.transcript_sha256 = sha256_text(
            json.dumps(state.transcript_log, sort_keys=True)
        )
        state.evidence.transcript_messages = len(state.transcript_log)
        state.evidence.transcript = copy.deepcopy(state.transcript_log)
        state.finished = True

    # -- one session -------------------------------------------------------

    def _run_session(self, state: _SessionState) -> None:
        completed_normally = True
        for turn in range(1, 5):
            if self._stop_for_session(state):
                completed_normally = False
                break
            if not self._run_turn_task(state, turn):
                completed_normally = False
                break
        if completed_normally and not self._abort:
            state.evidence.status = SESSION_STATUS_COMPLETED
            state.evidence.stop_reason = "completed"

    def _run_turn_task(self, state: _SessionState, turn: int) -> bool:
        if turn > state.evidence.planned_calls:
            state.evidence.status = SESSION_STATUS_COMPLETED
            state.evidence.stop_reason = "completed"
            return False
        if self._stop_for_session(state):
            return False
        if self._deadline_reached():
            return self._deadline_skip(state, turn)
        scenario = self.cell["scenario"]
        request_id = f"{state.evidence.session_id}-t{turn:02d}"
        with self._lock:
            self._actual_order.append(request_id)
        ready_at = state.ready_at if state.ready_at is not None else self.clock()
        state.ready_at = None

        if scenario == "idle_resume" and turn == 3:
            idle_started = self.clock()
            remaining = self._remaining_seconds()
            planned_sleep = float(self.idle_seconds)
            actual_sleep = max(0.0, min(planned_sleep, remaining))
            if actual_sleep > 0:
                self.sleep(actual_sleep)
            idle_ms = (self.clock() - idle_started) * 1000.0
            state.idle_total_ms += idle_ms
            state.evidence.notes.append(f"idle_before_turn_3_ms={idle_ms:.3f}")
            if actual_sleep < planned_sleep:
                state.evidence.notes.append(
                    "idle_truncated_by_cell_deadline:"
                    f"planned_ms={planned_sleep * 1000.0:.3f}"
                )
            if self._deadline_reached():
                return self._deadline_skip(state, turn)
            # Queue time begins after the deliberate idle. Idle is measured
            # separately; including it in queue_ms would double-label the same wait.
            queue_start = self.clock()
        else:
            idle_ms = 0.0
            queue_start = ready_at

        if scenario == "explicit_compaction" and turn == 3 and state.compaction is None:
            state.compaction = _compact_messages(state, before_turn=turn)
            state.evidence.compaction = state.compaction

        if scenario == "client_reconnect" and turn == 3:
            self._reconnect(state)

        if scenario == "tool_roundtrip":
            return self._run_tool_turn(state, turn, request_id, queue_start, idle_ms)

        if turn > 1:
            if scenario == "explicit_compaction" and turn == 3:
                # Compaction already inserted the turn-3 user task; appending a
                # second copy would change the measured suffix length.
                pass
            elif scenario in ("growing_transcript", "interleaved_sessions", "client_reconnect", "explicit_compaction", "idle_resume"):
                state.messages.append(
                    _next_user_task(state, scenario, turn, request_id)
                )
            elif scenario == "independent":
                state.messages = _initial_messages(
                    state,
                    scenario,
                    request_id,
                    _turn_namespace_label(state.spec, turn),
                )
            elif scenario == "prefix_invalidation" and turn == 3:
                state.messages = [
                    {
                        "role": "system",
                        "content": prefix_with_namespace(
                            state.template.prefix,
                            _turn_namespace_label(state.spec, turn),
                        ),
                    },
                    {
                        "role": "user",
                        "content": state.template.tasks[turn - 1]
                        + "\n\n"
                        + _request_tag(request_id),
                    },
                ]
            elif scenario == "stable_prefix" or (
                scenario == "prefix_invalidation" and turn != 3
            ):
                state.messages = [
                    state.messages[0],
                    {
                        "role": "user",
                        "content": state.template.tasks[turn - 1]
                        + "\n\n"
                        + _request_tag(request_id),
                    },
                ]

        call = self._call(
            state,
            turn=turn,
            request_id=request_id,
            messages=state.messages,
            ready_at=queue_start,
            idle_before_ms=idle_ms,
            tools=None,
            cache_group=state.spec["turn_group_ids"][turn - 1],
        )
        state.evidence.calls.append(call)
        state.active_total_ms += call.active_ms or 0.0
        state.last_call_end = self.clock()
        state.transcript_log.append(
            {
                "session_id": state.evidence.session_id,
                "turn": turn,
                "request_id": request_id,
                "messages_sent": copy.deepcopy(state.messages),
                "assistant_reply": call.visible_content,
                "finish_class": call.finish_class,
                "outcome": call.outcome,
            }
        )
        if self._stop_after_call(state, call):
            return False
        if scenario in (
            "growing_transcript",
            "interleaved_sessions",
            "client_reconnect",
            "explicit_compaction",
            "idle_resume",
        ):
            state.messages.append(
                {"role": "assistant", "content": call.visible_content}
            )
        state.next_turn = turn + 1
        return True

    # -- tool scenario -----------------------------------------------------

    def _run_tool_turn(
        self,
        state: _SessionState,
        turn: int,
        request_id: str,
        ready_at: float,
        idle_ms: float,
    ) -> bool:
        call = self._call(
            state,
            turn=turn,
            request_id=request_id,
            messages=state.messages,
            ready_at=ready_at,
            idle_before_ms=idle_ms,
            tools=TOOL_DEFINITIONS,
            cache_group=state.spec["turn_group_ids"][turn - 1],
        )
        state.evidence.calls.append(call)
        state.active_total_ms += call.active_ms or 0.0
        state.last_call_end = self.clock()
        state.transcript_log.append(
            {
                "session_id": state.evidence.session_id,
                "turn": turn,
                "request_id": request_id,
                "messages_sent": copy.deepcopy(state.messages),
                "assistant_reply": call.visible_content,
                "tool_calls": call.tool_calls,
                "finish_class": call.finish_class,
                "outcome": call.outcome,
            }
        )
        if self._stop_after_call(state, call):
            return False
        if not call.tool_calls:
            state.evidence.status = (
                SESSION_STATUS_COMPLETED
                if call.usable_visible_response
                else SESSION_STATUS_FAILED
            )
            state.evidence.stop_reason = OUTCOME_TOOL_ABSENT
            state.next_turn = turn + 1
            return False
        for entry in call.tool_calls:
            if entry.get("identifier_conflict"):
                call.outcome = OUTCOME_PROTOCOL_FAILURE
                call.outcome_detail = "tool_call_id_inconsistent"
                call.attribution = "provider"
                state.evidence.status = SESSION_STATUS_FAILED
                state.evidence.stop_reason = call.outcome_detail
                return False
            if not entry.get("call_id"):
                call.outcome = OUTCOME_PROTOCOL_FAILURE
                call.outcome_detail = "tool_call_id_missing"
                call.attribution = "provider"
                state.evidence.status = SESSION_STATUS_FAILED
                state.evidence.stop_reason = call.outcome_detail
                return False
            problem = validate_tool_arguments(entry.get("name"), entry.get("arguments") or "")
            if problem is not None:
                call.outcome = (
                    OUTCOME_UNSUPPORTED_TOOL
                    if problem.startswith("tool_call_unknown_name")
                    else OUTCOME_INVALID_TOOL_ARGUMENTS
                )
                call.outcome_detail = problem
                call.attribution = "provider"
                call.unsupported_tool = entry.get("name")
                state.evidence.status = (
                    SESSION_STATUS_UNSUPPORTED_TOOL
                    if call.outcome == OUTCOME_UNSUPPORTED_TOOL
                    else SESSION_STATUS_FAILED
                )
                state.evidence.stop_reason = problem
                return False
        # Execute deterministic in-process tools in order. No shell, network or
        # host filesystem access is reachable from a model-supplied path.
        assistant_message: dict[str, Any] = {
            "role": "assistant",
            "content": call.visible_content or None,
            "tool_calls": [
                {
                    "id": entry["call_id"],
                    "type": "function",
                    "function": {
                        "name": entry["name"],
                        "arguments": entry["arguments"],
                    },
                }
                for entry in call.tool_calls
            ],
        }
        state.messages.append(assistant_message)
        if turn >= 4:
            state.evidence.status = SESSION_STATUS_BUDGET_EXHAUSTED
            state.evidence.stop_reason = OUTCOME_TOOL_BUDGET
            return False
        results: list[dict[str, Any]] = []
        for entry in call.tool_calls:
            result, problem = _execute_tool(entry["name"], entry["arguments"])
            if problem is not None:
                state.evidence.status = (
                    SESSION_STATUS_UNSUPPORTED_TOOL
                    if problem.startswith("tool_call_unknown_name")
                    else SESSION_STATUS_FAILED
                )
                state.evidence.stop_reason = problem
                call.unsupported_tool = entry["name"]
                return False
            if result is None:
                state.evidence.status = SESSION_STATUS_FAILED
                state.evidence.stop_reason = "tool_result_missing"
                return False
            payload = json.dumps(result, sort_keys=True)
            results.append(
                {"tool_call_id": entry["call_id"], "name": entry["name"], "result": result}
            )
            state.messages.append(
                {
                    "role": "tool",
                    "tool_call_id": entry["call_id"],
                    "content": payload,
                }
            )
        call.tool_results = results
        history_problem = validate_tool_history(state.messages)
        if history_problem is not None:
            state.evidence.status = SESSION_STATUS_FAILED
            state.evidence.stop_reason = history_problem
            self._invalid = True
            return False
        state.next_turn = turn + 1
        return True

    # -- one call ----------------------------------------------------------

    def _call(
        self,
        state: _SessionState,
        *,
        turn: int,
        request_id: str,
        messages: list[dict[str, Any]],
        ready_at: float,
        idle_before_ms: float,
        tools: tuple[dict[str, Any], ...] | None,
        cache_group: str,
    ) -> CallEvidence:
        spec = state.spec
        call = CallEvidence(
            operation_id=self.cell["operation_id"],
            scenario=self.cell["scenario"],
            phase=self.cell["phase"],
            window=self.cell.get("window"),
            concurrent_sessions=int(self.cell["concurrent_sessions"]),
            session_id=state.evidence.session_id,
            session_index=state.evidence.session_index,
            template=state.evidence.template,
            group_id=spec["group_id"],
            prefix_group_id=spec.get("prefix_group_id"),
            turn_group_id=cache_group,
            turn_index=turn,
            request_id=request_id,
            seed=state.evidence.seed,
            connection_id=state.connection_id,
            reconnected=(
                self.cell["scenario"] == "client_reconnect" and turn == 3
            ),
            connection_reused=(
                turn > 1
                and not (self.cell["scenario"] == "client_reconnect" and turn == 3)
            ),
            idle_before_ms=idle_before_ms,
            request_headers={},
            cache_group=cache_group,
        )
        payload: dict[str, Any] = {
            "model": self.target.model_id,
            "messages": messages,
            "stream": self.target.streaming,
            "max_completion_tokens": self.target.max_output_tokens,
        }
        if self.target.streaming:
            payload["stream_options"] = {"include_usage": True}
        if tools:
            payload["tools"] = list(tools)
            payload["tool_choice"] = "auto"
            call.tools_sent = True
        body = json.dumps(payload).encode()
        messages_json = json.dumps(messages)
        call.request_bytes = len(body)
        call.messages_chars = len(messages_json)
        call.request_body_sha256 = hashlib.sha256(body).hexdigest()
        call.messages_sha256 = sha256_text(messages_json)
        leading = messages[0].get("content") if messages else ""
        if isinstance(leading, str):
            call.leading_content_sha256 = sha256_text(leading)
            call.leading_content_chars = len(leading)
            call.leading_marker_sha256 = sha256_text(leading[:CACHE_NAMESPACE_WIDTH])
        labels = state.spec.get("turn_namespace_labels") or []
        call.namespace_label = labels[turn - 1] if len(labels) >= turn else None
        if isinstance(call.namespace_label, str):
            call.namespace_marker_sha256 = sha256_text(
                cache_namespace_marker(call.namespace_label)
            )
        call.requested_input_tokens = self.cell.get("requested_input_tokens")
        call.client_estimated_input_tokens = estimate_tokens(
            messages_json + (json.dumps(list(tools)) if tools else "")
        )
        default_bound = (
            131072 if self.cell["scenario"] == "context_sentinels" else 32768
        )
        bound = int(self.cell.get("session_max_input_tokens") or default_bound)
        call.input_bound_limit = bound
        if call.client_estimated_input_tokens > bound:
            call.outcome = OUTCOME_INELIGIBLE_INPUT_BOUND
            call.outcome_detail = (
                f"client_estimated_input_tokens {call.client_estimated_input_tokens} "
                f"exceeds session bound {bound}"
            )
            call.attribution = "harness"
            call.input_bound_exceeded = True
            call.delivered_size_state = "client_exceeded_bound"
            state.evidence.status = SESSION_STATUS_INELIGIBLE_INPUT_BOUND
            state.evidence.stop_reason = call.outcome_detail
            return call
        if self.max_total_attempts is not None and self._attempts >= self.max_total_attempts:
            call.outcome = OUTCOME_SKIPPED
            call.outcome_detail = "total_attempt_limit_reached"
            call.delivered_size_state = "not_sent"
            self._abort = True
            self._abort_reason = "total_attempt_limit"
            return call
        if self._deadline_reached():
            call.outcome = OUTCOME_SKIPPED
            call.outcome_detail = "cell_deadline_before_send"
            call.attribution = "harness"
            call.delivered_size_state = "not_sent"
            state.evidence.status = SESSION_STATUS_DEADLINE
            state.evidence.stop_reason = "cell_deadline"
            self._abort = True
            self._abort_reason = "cell_deadline"
            return call

        request_started = self.clock()
        headers = self._headers(cache_group, state.evidence.session_id)
        call.request_headers = _sanitized_headers(headers)
        self.journal.attempt_started(
            request_id,
            {
                "operation_id": self.cell["operation_id"],
                "session_id": state.evidence.session_id,
                "turn": turn,
                "request_id": request_id,
                "request_bytes": call.request_bytes,
            },
        )
        counted = False
        started: float | None = None
        watchdog: _TransportDeadline | None = None
        trace = _TransportTrace()
        response: Any | None = None
        try:
            # Journal work is local but not free. Recheck every stop rule after it
            # and before any socket connect/send, so a reservation can never turn
            # into a post-deadline request.
            if self._deadline_reached():
                return self._skip_after_journal(
                    state,
                    call,
                    "cell_deadline_before_send",
                    abort_reason="cell_deadline",
                )
            if (
                self.max_total_attempts is not None
                and self._attempts >= self.max_total_attempts
            ):
                return self._skip_after_journal(
                    state,
                    call,
                    "total_attempt_limit_reached",
                    abort_reason="total_attempt_limit",
                )
            if self.clock() - request_started >= float(self.target.timeout_s):
                return self._skip_after_journal(
                    state,
                    call,
                    "request_budget_exhausted_before_send",
                    abort_reason="request_budget_exhausted",
                )

            with self._lock:
                self._attempts += 1
                self._active_calls += 1
                self._max_calls = max(self._max_calls, self._active_calls)
                counted = True
            call.queue_ms = max(0.0, (self.clock() - ready_at) * 1000.0)
            call.started_at_utc = self.wall_clock()
            started = self.clock()

            if state.connection is None:
                state.connection = _open_connection(
                    self.target, timeout_s=self._bounded_timeout()
                )
            request_budget = max(
                0.0, float(self.target.timeout_s) - (self.clock() - request_started)
            )
            cell_remaining = self._remaining_seconds()
            transport_budget = max(0.001, min(request_budget, cell_remaining))
            self._arm_connection(state.connection, timeout_s=transport_budget)
            watchdog = _TransportDeadline(
                state.connection,
                seconds=transport_budget,
                cell_bounded=cell_remaining <= request_budget,
            )
            watchdog.start()
            self._acquire_transport(state, watchdog, transport_budget, trace)
            if watchdog.fired.is_set() or self._deadline_reached():
                raise TimeoutError("transport_budget_expired_before_send")
            state.connection.request(
                "POST", "/v1/chat/completions", body=body, headers=headers
            )
            response = state.connection.getresponse()
            if watchdog is not None:
                watchdog.retarget(_response_socket(response))
            call.http_status = response.status
            if 300 <= response.status < 400:
                self._finish_failure(
                    call, OUTCOME_PROTOCOL_FAILURE, "redirect_not_permitted", "provider"
                )
            elif response.status == 429:
                self._finish_failure(call, OUTCOME_RATE_LIMITED, "http_429", "provider")
                self._abort = True
                self._abort_reason = "rate_limit_exhausted"
            elif 500 <= response.status <= 599:
                self._finish_failure(
                    call, OUTCOME_SERVER_ERROR, f"http_{response.status}", "provider"
                )
            elif 400 <= response.status <= 499:
                outcome = (
                    OUTCOME_CONTEXT_REJECTED
                    if response.status in (400, 413)
                    else OUTCOME_CLIENT_ERROR
                )
                self._finish_failure(
                    call,
                    outcome,
                    f"http_{response.status}",
                    "harness" if response.status not in (400, 413) else "provider",
                )
            else:
                self._read_response(response, call, started)
        except TimeoutError:
            expired = self._expired_transport(request_started, watchdog)
            if expired == "cell":
                self._finish_failure(
                    call, OUTCOME_TIMEOUT, "cell_deadline_during_transport", "harness"
                )
                self._abort = True
                self._abort_reason = "cell_deadline"
            elif expired == "request":
                self._finish_failure(
                    call, OUTCOME_TIMEOUT, "request_timeout_during_transport", "provider"
                )
            else:
                self._finish_failure(call, OUTCOME_TIMEOUT, "timeout", "provider")
        except (OSError, http.client.HTTPException) as error:
            if trace.reused and trace.reuse_error is None:
                # A live-looking socket failed. The request is never retried on a
                # replacement transport; the failure is recorded for the caller.
                trace.reuse_error = f"{type(error).__name__}:reuse_failed"
            expired = self._expired_transport(request_started, watchdog)
            if expired == "cell":
                self._finish_failure(
                    call, OUTCOME_TIMEOUT, "cell_deadline_during_transport", "harness"
                )
                self._abort = True
                self._abort_reason = "cell_deadline"
            elif expired == "request":
                self._finish_failure(
                    call, OUTCOME_TIMEOUT, "request_timeout_during_transport", "provider"
                )
            else:
                self._finish_failure(
                    call,
                    OUTCOME_STREAM_INTERRUPTED
                    if call.http_status is not None
                    else OUTCOME_CLIENT_ERROR,
                    f"transport_error:{type(error).__name__}",
                    "provider" if call.http_status is not None else "unknown",
                )
        finally:
            transport_reusable = (
                response is not None
                and call.outcome in _COMPLETED_OUTCOMES
                and _response_reusable(response, timeout_s=0.05)
            )
            if response is not None and not transport_reusable:
                try:
                    response.close()
                except (OSError, http.client.HTTPException):
                    pass
            call.connection_measured = trace.measured
            call.connection_opened = trace.opened
            call.connection_reused = trace.reused
            call.connection_prior_closed = trace.prior_closed
            call.connection_reuse_error = trace.reuse_error
            call.connection_addresses = trace.addresses
            call.connection_resolve_ms = trace.resolve_ms
            call.connection_connect_ms = trace.connect_ms
            call.connections_opened_total = trace.opened_total
            if watchdog is not None:
                watchdog.cancel()
                # Only a completed, fully drained exchange on a socket the guard
                # never touched may carry the next turn's request.
                transport_reusable = (
                    transport_reusable
                    and not watchdog.fired.is_set()
                    and not watchdog.join_timed_out
                )
                if not transport_reusable:
                    self._close(state)
            if counted:
                with self._lock:
                    self._active_calls -= 1
            if started is not None:
                call.active_ms = _now_ms(started, self.clock)
            call.ended_at_utc = self.wall_clock()
            if call.outcome not in _COMPLETED_OUTCOMES and call.outcome not in (
                OUTCOME_SKIPPED,
                OUTCOME_INELIGIBLE_INPUT_BOUND,
            ):
                expired = self._expired_transport(request_started, watchdog)
                if expired == "cell":
                    self._finish_failure(
                        call, OUTCOME_TIMEOUT, "cell_deadline_during_transport", "harness"
                    )
                    self._abort = True
                    self._abort_reason = "cell_deadline"
                elif expired == "request":
                    self._finish_failure(
                        call, OUTCOME_TIMEOUT, "request_timeout_during_transport", "provider"
                    )
            if call.http_status is None and call.error_type is None and call.outcome == OUTCOME_UNKNOWN:
                call.outcome = OUTCOME_UNKNOWN
                call.outcome_detail = "no_response_recorded"
            self.journal.attempt_finished(call)
            if call.input_bound_exceeded:
                call.delivered_size_state = "client_exceeded_bound"
            elif call.provider_input_tokens is not None and call.provider_input_tokens > bound:
                call.input_bound_exceeded = True
                call.delivered_size_state = "provider_exceeded_bound"
                call.notes.append(
                    f"provider_input_tokens {call.provider_input_tokens} exceeded bound {bound}"
                )
                state.evidence.status = SESSION_STATUS_INELIGIBLE_INPUT_BOUND
                state.evidence.stop_reason = "provider_input_tokens_exceeded_bound"
            elif call.provider_input_tokens is not None and call.requested_input_tokens:
                delta = abs(
                    call.provider_input_tokens - int(call.requested_input_tokens)
                )
                call.delivered_size_conformance = (
                    delta / int(call.requested_input_tokens) <= 0.10
                )
                call.delivered_size_state = (
                    "within_tolerance"
                    if call.delivered_size_conformance
                    else "outside_tolerance"
                )
            elif call.provider_input_tokens is not None:
                call.delivered_size_state = "requested_size_not_declared"
            elif call.http_status is not None:
                call.delivered_size_state = "provider_count_missing"
            else:
                call.delivered_size_state = "not_sent"
        return call

    def _skip_after_journal(
        self,
        state: _SessionState,
        call: CallEvidence,
        detail: str,
        *,
        abort_reason: str,
    ) -> CallEvidence:
        """Account for a call whose durable intent expired before any send."""
        call.outcome = OUTCOME_SKIPPED
        call.outcome_detail = detail
        call.attribution = "harness"
        call.delivered_size_state = "not_sent"
        if abort_reason == "cell_deadline":
            state.evidence.status = SESSION_STATUS_DEADLINE
            state.evidence.stop_reason = "cell_deadline"
        else:
            state.evidence.stop_reason = detail
        self._abort = True
        self._abort_reason = abort_reason
        return call

    def _expired_transport(
        self,
        request_started: float,
        watchdog: _TransportDeadline | None,
    ) -> str | None:
        """Classify which absolute bound ended a transport phase, if any."""
        if watchdog is not None and watchdog.fired.is_set():
            return "cell" if watchdog.cell_bounded else "request"
        if self._deadline_reached():
            return "cell"
        if (
            watchdog is not None
            and watchdog.cell_bounded
            and self._remaining_seconds() <= 0.05
        ):
            # The socket timeout and the timer race at the same instant; a
            # timeout inside the guard's last tick is still a cell deadline.
            return "cell"
        if self.clock() - request_started >= float(self.target.timeout_s):
            return "request"
        return None


    def _headers(self, cache_group: str, session_id: str) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if self.target.streaming else "application/json",
        }
        if self.target.api_key:
            headers["Authorization"] = f"Bearer {self.target.api_key}"
        headers[self.target.cache_group_header] = cache_group
        headers[self.target.session_header] = session_id
        for name, value in self.target.extra_headers:
            headers[name] = value
        if any(name.lower() == "x-session-affinity" for name in headers):
            raise SessionEngineError("session affinity is disabled for this program")
        return headers

    def _read_response(self, response: Any, call: CallEvidence, started: float) -> None:
        if not self.target.streaming:
            self._read_nonstreaming(response, call, started)
            return
        content_type = (response.getheader("Content-Type") or "").lower()
        if content_type and "event-stream" not in content_type:
            self._finish_failure(
                call,
                OUTCOME_PROTOCOL_FAILURE,
                f"unexpected_content_type:{content_type}",
                "provider",
            )
            return
        state = _StreamState()
        reader = _SSEReader(
            response,
            started=started,
            clock=self.clock,
            timeout_s=self.target.timeout_s,
            absolute_deadline=self._cell_deadline,
        )
        try:
            for data in reader.events():
                if data == "[DONE]":
                    state.saw_done = True
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    self._finish_failure(
                        call, OUTCOME_PROTOCOL_FAILURE, "malformed_sse_json", "provider"
                    )
                    self._copy_stream_state(call, state)
                    return
                if not isinstance(chunk, dict):
                    self._finish_failure(
                        call, OUTCOME_PROTOCOL_FAILURE, "sse_event_not_object", "provider"
                    )
                    self._copy_stream_state(call, state)
                    return
                state.saw_event = True
                self._apply_chunk(state, chunk, started)
        except TimeoutError:
            self._copy_stream_state(call, state)
            if self._deadline_reached():
                self._finish_failure(
                    call, OUTCOME_TIMEOUT, "cell_deadline_during_transport", "harness"
                )
                self._abort = True
                self._abort_reason = "cell_deadline"
            else:
                self._finish_failure(
                    call,
                    OUTCOME_TIMEOUT,
                    "timeout_after_first_token"
                    if state.first_any is not None
                    else "timeout_before_first_token",
                    "provider",
                )
            return
        except ConnectionError as error:
            self._copy_stream_state(call, state)
            self._finish_failure(
                call, OUTCOME_STREAM_INTERRUPTED, f"stream_error:{error}", "provider"
            )
            return
        self._copy_stream_state(call, state)
        call.sse_done_seen = state.saw_done
        if not state.saw_event:
            self._finish_failure(call, OUTCOME_PROTOCOL_FAILURE, "empty_stream", "provider")
            return
        if state.finish_reason is None:
            self._finish_failure(
                call, OUTCOME_STREAM_INTERRUPTED, "missing_finish_reason", "provider"
            )
            return
        if not state.saw_done:
            call.evidence_gaps.append("sse_done_marker_absent")
        self._classify_completion(call, state.finish_reason)

    def _apply_chunk(self, state: _StreamState, chunk: dict[str, Any], started: float) -> None:
        _apply_usage(state, chunk)
        choices = chunk.get("choices")
        if not isinstance(choices, list) or not choices:
            return
        choice = choices[0] if isinstance(choices[0], dict) else None
        if choice is None:
            return
        if choice.get("finish_reason"):
            state.finish_reason = choice["finish_reason"]
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            return
        content = _content_of(delta)
        reasoning = _reasoning_of(delta)
        tool_fragments = delta.get("tool_calls")
        has_tool = isinstance(tool_fragments, list) and bool(tool_fragments)
        if (content or reasoning or has_tool) and state.first_any is None:
            state.first_any = _now_ms(started, self.clock)
        if reasoning:
            state.reasoning_parts.append(reasoning)
            if state.first_reasoning is None:
                state.first_reasoning = _now_ms(started, self.clock)
        if content:
            state.visible_parts.append(content)
            if state.first_visible is None:
                state.first_visible = _now_ms(started, self.clock)
        if has_tool:
            _assemble_from_delta(state.tool_slots, delta)

    def _copy_stream_state(self, call: CallEvidence, state: _StreamState) -> None:
        call.ttft_any_ms = state.first_any
        call.ttft_reasoning_ms = state.first_reasoning
        call.ttft_visible_ms = state.first_visible
        call.provider_input_tokens = state.prompt_tokens
        call.provider_output_tokens = state.completion_tokens
        call.provider_reasoning_tokens = state.reasoning_tokens
        call.cache_read_tokens = state.cache_read_tokens
        call.cache_write_tokens = state.cache_write_tokens
        call.visible_content = "".join(state.visible_parts)
        call.visible_chars = len(call.visible_content)
        call.visible_missing = not call.visible_content.strip()
        if call.visible_content:
            call.visible_content_sha256 = hashlib.sha256(
                call.visible_content.encode("utf-8")
            ).hexdigest()
        call.reasoning_chars = sum(len(part) for part in state.reasoning_parts)
        if call.provider_output_tokens is not None and call.provider_reasoning_tokens is not None:
            visible = call.provider_output_tokens - call.provider_reasoning_tokens
            call.provider_visible_output_tokens = max(0, visible)
        if state.tool_slots:
            call.tool_calls = [
                slot.to_dict() for slot in state.tool_slots.values()
            ]

    def _read_nonstreaming(self, response: Any, call: CallEvidence, started: float) -> None:
        try:
            raw = response.read()
        except TimeoutError:
            self._finish_failure(call, OUTCOME_TIMEOUT, "timeout_during_body", "provider")
            return
        except (OSError, http.client.HTTPException) as error:
            self._finish_failure(
                call, OUTCOME_STREAM_INTERRUPTED, f"body_error:{type(error).__name__}", "provider"
            )
            return
        try:
            document = json.loads(raw)
        except json.JSONDecodeError:
            self._finish_failure(call, OUTCOME_PROTOCOL_FAILURE, "malformed_json", "provider")
            return
        if not isinstance(document, dict):
            self._finish_failure(call, OUTCOME_PROTOCOL_FAILURE, "response_not_object", "provider")
            return
        state = _StreamState()
        _apply_usage(state, document)
        choices = document.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            choice = choices[0]
            state.finish_reason = choice.get("finish_reason")
            message = choice.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    state.visible_parts.append(content)
                for index, entry in enumerate(message.get("tool_calls") or []):
                    if not isinstance(entry, dict):
                        continue
                    slot = AssembledToolCall(index=index)
                    if isinstance(entry.get("id"), str):
                        slot.identifiers.append(entry["id"])
                    function = entry.get("function") or {}
                    if isinstance(function.get("name"), str):
                        slot.name = function["name"]
                    if isinstance(function.get("arguments"), str):
                        slot.argument_fragments.append(function["arguments"])
                    state.tool_slots[index] = slot
        state.first_any = _now_ms(started, self.clock) if state.visible_parts or state.tool_slots else None
        state.first_visible = state.first_any if state.visible_parts else None
        self._copy_stream_state(call, state)
        call.sse_done_seen = None
        if state.finish_reason is None:
            self._finish_failure(
                call, OUTCOME_PROTOCOL_FAILURE, "missing_finish_reason", "provider"
            )
            return
        self._classify_completion(call, state.finish_reason)

    def _classify_completion(self, call: CallEvidence, finish_reason: str) -> None:
        call.finish_reason = finish_reason
        call.finish_class = classify_finish_reason(finish_reason)
        if call.finish_class == FINISH_CEILING:
            call.truncated = True
            call.outcome = OUTCOME_OUTPUT_TRUNCATED
            call.outcome_detail = "finish_reason_length"
            call.attribution = "harness"
            return
        if call.finish_class == FINISH_CONTENT_FILTER:
            call.outcome = OUTCOME_PROTOCOL_FAILURE
            call.outcome_detail = "content_filter"
            call.attribution = "provider"
            return
        if call.finish_class not in (FINISH_NATURAL,):
            call.outcome = OUTCOME_PROTOCOL_FAILURE
            call.outcome_detail = f"finish_class_{call.finish_class}"
            call.attribution = "provider"
            return
        call.outcome = OUTCOME_SUCCESS
        call.usable_visible_response = not call.visible_missing
        call.outcome_detail = "completed"

    def _finish_failure(
        self, call: CallEvidence, outcome: str, detail: str, attribution: str
    ) -> None:
        call.finish_class = classify_finish_reason(call.finish_reason)
        call.outcome = outcome
        call.outcome_detail = detail
        call.attribution = attribution

    # -- stop rules --------------------------------------------------------

    def _remaining_seconds(self) -> float:
        if self._cell_deadline is None:
            return float("inf")
        return self._cell_deadline - self.clock()

    def _deadline_reached(self) -> bool:
        return self._remaining_seconds() <= 0.0

    def _bounded_timeout(self) -> float:
        remaining = self._remaining_seconds()
        if remaining == float("inf"):
            return float(self.target.timeout_s)
        return max(0.001, min(float(self.target.timeout_s), remaining))

    def _arm_connection(
        self,
        connection: http.client.HTTPConnection,
        *,
        timeout_s: float | None = None,
    ) -> None:
        timeout = self._bounded_timeout() if timeout_s is None else max(0.001, timeout_s)
        connection.timeout = timeout
        sock = getattr(connection, "sock", None)
        if sock is not None:
            try:
                sock.settimeout(timeout)
            except OSError:
                return

    def _acquire_transport(
        self,
        state: _SessionState,
        watchdog: _TransportDeadline,
        budget: float,
        trace: _TransportTrace,
    ) -> None:
        """Reuse the session's live socket, or resolve and connect only if needed.

        The session treatment is a persistent transport, so a live socket is
        reused as-is. A socket the peer closed, or one the guard shut down, is
        replaced before any request bytes are written; that replacement is
        recorded rather than hidden. Resolution and connect share the call's one
        remaining budget and never outlive it.
        """
        trace.measured = True
        if state.connection is not None and state.connection.sock is not None:
            if not _peer_closed(state.connection.sock):
                trace.reused = True
                trace.opened_total = state.connections_opened
                return
            trace.prior_closed = True
            self._close(state)
        if state.connection is None:
            state.connection = _open_connection(self.target, timeout_s=budget)
            watchdog.watch(state.connection)
        self._arm_connection(state.connection, timeout_s=budget)
        connection = state.connection
        started = self.clock()
        deadline = started + budget
        addresses, trace.resolve_ms = _resolve_addresses_bounded(
            connection.host,
            connection.port,
            timeout_s=max(0.001, deadline - started),
            clock=self.clock,
        )
        trace.addresses = len(addresses)
        connection._create_connection = _bounded_socket_factory(
            connection, addresses, clock=self.clock, deadline=deadline
        )
        connection.connect()
        trace.connect_ms = (self.clock() - started) * 1000.0 - trace.resolve_ms
        trace.opened = True
        with self._lock:
            state.connections_opened += 1
        trace.opened_total = state.connections_opened
        watchdog.retarget(connection.sock)

    def _deadline_skip(self, state: _SessionState, turn: int) -> bool:
        """Record a skipped call and stop the cell without sending anything."""
        state.evidence.status = SESSION_STATUS_DEADLINE
        state.evidence.stop_reason = "cell_deadline"
        state.evidence.calls.append(
            self._blank_call(state, turn, OUTCOME_SKIPPED, "cell_deadline_before_send")
        )
        self._abort = True
        self._abort_reason = "cell_deadline"
        return False

    def _stop_for_session(self, state: _SessionState) -> bool:
        if self._abort:
            state.evidence.stop_reason = self._abort_reason
            return True
        if state.stop:
            return True
        return False

    def _stop_after_call(self, state: _SessionState, call: CallEvidence) -> bool:
        if call.input_bound_exceeded:
            state.evidence.status = SESSION_STATUS_INELIGIBLE_INPUT_BOUND
            state.evidence.stop_reason = (
                call.outcome_detail or "provider_input_tokens_exceeded_bound"
            )
            return True
        if call.outcome == OUTCOME_SUCCESS:
            if call.visible_missing and not call.tool_calls:
                state.evidence.status = SESSION_STATUS_FAILED
                state.evidence.stop_reason = "unusable_visible_response"
                return True
            return False
        if call.outcome == OUTCOME_OUTPUT_TRUNCATED:
            state.evidence.status = SESSION_STATUS_CENSORED
            state.evidence.stop_reason = "dependent_turn_truncated"
            return True
        if call.outcome == OUTCOME_UNSUPPORTED_TOOL:
            state.evidence.status = SESSION_STATUS_UNSUPPORTED_TOOL
            state.evidence.stop_reason = call.outcome_detail
            return True
        if call.outcome == OUTCOME_INELIGIBLE_INPUT_BOUND:
            state.evidence.status = SESSION_STATUS_INELIGIBLE_INPUT_BOUND
            state.evidence.stop_reason = call.outcome_detail
            return True
        if call.outcome == OUTCOME_SKIPPED:
            state.evidence.status = SESSION_STATUS_DEADLINE
            state.evidence.stop_reason = call.outcome_detail
            return True
        if call.outcome in ENDPOINT_FAILURE_OUTCOMES:
            state.evidence.status = (
                SESSION_STATUS_RATE_LIMITED
                if call.outcome == OUTCOME_RATE_LIMITED
                else SESSION_STATUS_FAILED
            )
            state.evidence.stop_reason = call.outcome_detail
            return True
        state.evidence.status = SESSION_STATUS_FAILED
        state.evidence.stop_reason = call.outcome_detail or call.outcome
        return True

    # -- context sentinels -------------------------------------------------

    def _run_context(self) -> CellEvidence:
        self._cell_start = self.clock()
        self._cell_deadline = self._cell_start + float(self.max_cell_seconds)
        evidence = CellEvidence(
            operation_id=self.cell["operation_id"],
            scenario="context_sentinels",
            phase=self.cell["phase"],
            window=self.cell.get("window"),
            concurrency=1,
            workload_type=str(
                self.cell.get("workload_type") or WORKLOAD_LIVE_TURN_SESSION
            ),
            planned_sessions=0,
            planned_calls=int(self.cell["max_inference_attempts"]),
            started_at_utc=self.wall_clock(),
        )
        requested = int(self.cell["requested_input_tokens"])
        namespace = str(self.cell["namespace"])
        base_seed = int(namespace[:8], 16)
        frozen_fixtures = {
            (fixture["position"], fixture["replicate"]): fixture
            for fixture in self.cell.get("context_fixtures") or []
        }
        for position_index, position in enumerate(self.cell["sentinel_positions"]):
            for replicate in range(1, int(self.cell["replicates_per_position"]) + 1):
                fixture = context_fixture(
                    requested_input_tokens=requested,
                    position=position,
                    replicate=replicate,
                    seed=base_seed + 97 * position_index,
                    namespace=namespace,
                )
                frozen = frozen_fixtures.get((position, replicate))
                if frozen is None:
                    raise SessionEngineError(
                        f"{self.cell['operation_id']}: no frozen context fixture for "
                        f"{position}/r{replicate}"
                    )
                if frozen.get("prompt_sha256") != fixture["prompt_sha256"]:
                    raise SessionEngineError(
                        f"{self.cell['operation_id']}: context fixture {position}/r{replicate} "
                        "does not match the frozen plan bytes"
                    )
                request_id = f"{self.cell['operation_id']}-{position}-r{replicate}"
                label = f"ctx:{namespace}:{position}:r{replicate}"
                state = _SessionState(
                    spec={
                        "session_id": request_id,
                        "session_index": 0,
                        "template": "context",
                        "seed": base_seed,
                        "group_id": request_id,
                        "prefix_group_id": None,
                        "turn_group_ids": [request_id],
                        "turn_namespace_labels": [label],
                    },
                    template=None,
                    messages=[{"role": "user", "content": fixture["prompt"]}],
                    transcript_log=[],
                    evidence=SessionEvidence(
                        session_id=request_id,
                        session_index=0,
                        template="context",
                        group_id=request_id,
                        seed=base_seed,
                        scenario="context_sentinels",
                        operation_id=self.cell["operation_id"],
                        planned_calls=1,
                    ),
                )
                self._session_enter(state)
                call = self._call(
                    state,
                    turn=1,
                    request_id=request_id,
                    messages=state.messages,
                    ready_at=self.clock(),
                    idle_before_ms=0.0,
                    tools=None,
                    cache_group=request_id,
                )
                state.evidence.calls.append(call)
                check = self._context_check(fixture, call)
                evidence.context_checks.append(check)
                if call.outcome == OUTCOME_SKIPPED:
                    state.evidence.status = SESSION_STATUS_DEADLINE
                    state.evidence.stop_reason = "cell_deadline"
                elif call.outcome == OUTCOME_INELIGIBLE_INPUT_BOUND:
                    state.evidence.status = SESSION_STATUS_INELIGIBLE_INPUT_BOUND
                    state.evidence.stop_reason = call.outcome_detail
                elif check["api_accepted"]:
                    state.evidence.status = SESSION_STATUS_COMPLETED
                    state.evidence.stop_reason = check["classification"]
                else:
                    state.evidence.status = SESSION_STATUS_FAILED
                    state.evidence.stop_reason = check["classification"]
                state.transcript_log.append(
                    {
                        "session_id": request_id,
                        "turn": 1,
                        "request_id": request_id,
                        "messages_sent": copy.deepcopy(state.messages),
                        "assistant_reply": call.visible_content,
                        "finish_class": call.finish_class,
                        "outcome": call.outcome,
                    }
                )
                self._close(state)
                self._session_exit(state)
                evidence.sessions.append(state.evidence)
                if self._abort:
                    break
            if self._abort:
                break
        evidence.ended_at_utc = self.wall_clock()
        evidence.elapsed_ms = _now_ms(self._cell_start, self.clock)
        evidence.attempted_calls = self._attempts
        evidence.achieved_max_concurrent_sessions = self._max_sessions
        evidence.achieved_max_inflight_calls = self._max_calls
        evidence.actual_turn_order = list(self._actual_order)
        evidence.cache_prefix_audit = self._cache_prefix_audit(evidence)
        if self._abort_reason == "rate_limit_exhausted":
            evidence.status = CELL_STATUS_STOPPED_RATE_LIMIT
        elif self._abort_reason == "cell_deadline":
            evidence.status = CELL_STATUS_STOPPED_DEADLINE
        elif self._attempts == 0:
            evidence.status = CELL_STATUS_NOT_MEASURED
        elif self._attempts < evidence.planned_calls or len(evidence.context_checks) < evidence.planned_calls:
            evidence.status = CELL_STATUS_PARTIAL
        else:
            evidence.status = CELL_STATUS_COMPLETED
        evidence.stop_reason = self._abort_reason or (
            "all_calls_ineligible_input_bound"
            if evidence.status == CELL_STATUS_NOT_MEASURED
            else evidence.status
        )
        skipped = evidence.counters()["skipped_calls"]
        evidence.evidence_complete = (
            evidence.attempted_calls + skipped == evidence.planned_calls
        )
        return evidence

    def _context_check(self, fixture: dict[str, Any], call: CallEvidence) -> dict[str, Any]:
        expected = fixture["sentinel_code"]
        answered = call.visible_content.strip().strip("`").strip()
        demonstrated = call.outcome == OUTCOME_SUCCESS and answered == expected
        if call.http_status is None:
            classification = "not_sent"
        elif not 200 <= (call.http_status or 0) < 300:
            classification = "refused" if 400 <= (call.http_status or 0) < 500 else "failed"
        elif call.outcome == OUTCOME_OUTPUT_TRUNCATED:
            classification = "output_truncated"
        elif call.outcome in (OUTCOME_PROTOCOL_FAILURE, OUTCOME_STREAM_INTERRUPTED):
            classification = "malformed_response"
        elif call.outcome != OUTCOME_SUCCESS:
            classification = "failed"
        elif demonstrated:
            classification = "demonstrated_effective"
        else:
            classification = "api_accepted_but_unverified"
        tolerance = 0.10
        conformance: bool | None = None
        if call.provider_input_tokens is not None and fixture["requested_input_tokens"]:
            delta = abs(call.provider_input_tokens - fixture["requested_input_tokens"])
            conformance = delta / fixture["requested_input_tokens"] <= tolerance
        return {
            "session_id": call.session_id,
            "request_id": call.request_id,
            "position": fixture["position"],
            "replicate": fixture["replicate"],
            "requested_input_tokens": fixture["requested_input_tokens"],
            "sentinel_code": expected,
            "prompt_sha256": fixture["prompt_sha256"],
            "messages_sha256": call.messages_sha256,
            "namespace_label": call.namespace_label,
            "api_accepted": bool(
                call.http_status is not None and 200 <= call.http_status < 300
            ),
            "http_status": call.http_status,
            "outcome": call.outcome,
            "finish_class": call.finish_class,
            "finish_reason": call.finish_reason,
            "demonstrated_effective": demonstrated,
            "classification": classification,
            "answered_excerpt": call.visible_content[:200],
            "provider_input_tokens": call.provider_input_tokens,
            "client_estimated_input_tokens": call.client_estimated_input_tokens,
            "delivered_input_tokens": call.provider_input_tokens,
            "delivered_size_conformance": conformance,
            "delivered_size_tolerance": tolerance,
            "delivered_size_state": call.delivered_size_state,
            "note": (
                "A failed exact check establishes only that this request did not "
                "demonstrate the sentinel. It does not prove server-side truncation. "
                "Provider-vs-requested size conformance is reported separately."
            ),
        }

    # -- helpers -----------------------------------------------------------

    def _reconnect(self, state: _SessionState) -> None:
        self._close(state)
        state.connection_serial += 1
        state.connection_id = f"c{state.connection_serial}"
        state.connection = _open_connection(
            self.target, timeout_s=self._bounded_timeout()
        )

    def _close(self, state: _SessionState) -> None:
        if state.connection is not None:
            try:
                state.connection.close()
            except OSError:
                pass
            state.connection = None

    def _blank_call(
        self, state: _SessionState, turn: int, outcome: str, detail: str
    ) -> CallEvidence:
        request_id = f"{state.evidence.session_id}-t{turn:02d}"
        labels = state.spec.get("turn_namespace_labels") or []
        label = labels[turn - 1] if len(labels) >= turn else None
        return CallEvidence(
            operation_id=self.cell["operation_id"],
            scenario=self.cell["scenario"],
            phase=self.cell["phase"],
            window=self.cell.get("window"),
            concurrent_sessions=int(self.cell["concurrent_sessions"]),
            session_id=state.evidence.session_id,
            session_index=state.evidence.session_index,
            template=state.evidence.template,
            group_id=state.spec["group_id"],
            prefix_group_id=state.spec.get("prefix_group_id"),
            turn_group_id=state.spec["turn_group_ids"][turn - 1],
            turn_index=turn,
            request_id=request_id,
            seed=state.evidence.seed,
            connection_id=state.connection_id,
            reconnected=(
                self.cell["scenario"] == "client_reconnect" and turn == 3
            ),
            connections_opened_total=state.connections_opened,
            namespace_label=label,
            namespace_marker_sha256=(
                sha256_text(cache_namespace_marker(label))
                if isinstance(label, str)
                else None
            ),
            outcome=outcome,
            outcome_detail=detail,
            attribution="harness",
        )
