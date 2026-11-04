"""Evidence writing, restart journal, validation and analysis for session cells.

The journal is written before each request and after it completes. On restart an
operation that was started but not finished is marked `interrupted` and is never
re-run: the directive permits resume of unstarted operations only. Partial
evidence is preserved and reported, not silently regenerated.

Analysis never invents a threshold, never drops a denominator, and never turns
missing provider telemetry into zero.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .qualification import percentile
from .session_engine import (
    ENDPOINT_FAILURE_OUTCOMES,
    OUTCOME_INELIGIBLE_INPUT_BOUND,
    OUTCOME_OUTPUT_TRUNCATED,
    OUTCOME_SKIPPED,
    OUTCOME_SUCCESS,
    SESSION_STATUS_CENSORED,
    SESSION_STATUS_COMPLETED,
    CellEvidence,
)
from .session_program import (
    SAME_DAY_WORKLOAD_TYPE,
    WORKLOAD_LIVE_TURN_SESSION,
    SessionProgramError,
    utc_now,
    write_json_atomic,
)
from .session_program import (
    validate_manifest_document as _validate_manifest_document,
)

STATE_SCHEMA = "session-state/1.0.0"
CELL_SCHEMA = "session-cell/1.0.0"
ANALYSIS_SCHEMA = "session-results/1.0.0"


class SessionEvidenceError(ValueError):
    """Raised when session evidence is missing, duplicated or inconsistent."""


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SessionEvidenceError(f"{name} must be a non-negative integer")
    return value


def _number(value: object, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SessionEvidenceError(f"{name} must be a finite number or null")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise SessionEvidenceError(f"{name} must be finite and non-negative")
    return number


# -- restart journal ----------------------------------------------------------


class JsonJournal:
    """Atomic single-writer journal with an exclusive foreground lock.

    The in-process ``threading.Lock`` protects concurrent writes inside one
    process. It is not a program lock: a second executor process could otherwise
    open the same state, mark a live operation ``interrupted`` and start another
    cell. ``flock`` is held for the journal's lifetime and is released by the OS
    if the process dies, so lock acquisition is the evidence that no live writer
    exists. Reconciliation never infers a dead process from ``started`` alone.
    """

    def __init__(
        self,
        path: Path,
        *,
        program_id: str,
        plan_sha256: str,
        acquire_lock: bool = True,
    ) -> None:
        self.path = path
        self.program_id = program_id
        self.plan_sha256 = plan_sha256
        self.lock = threading.Lock()
        self.lock_path = path.with_name(path.name + ".lock")
        self._lock_handle: Any | None = None
        if acquire_lock:
            self._acquire_exclusive_lock()
        try:
            if path.is_file():
                self.state = json.loads(path.read_text(encoding="utf-8"))
                if self.state.get("plan_sha256") != plan_sha256:
                    raise SessionEvidenceError(
                        f"journal `{path}` belongs to plan {self.state.get('plan_sha256')}, "
                        f"not {plan_sha256}; refusing to mix evidence"
                    )
                self._reconcile_interrupted()
            else:
                self.state = {
                    "schema_version": STATE_SCHEMA,
                    "program_id": program_id,
                    "plan_sha256": plan_sha256,
                    "created_at_utc": utc_now(),
                    "updated_at_utc": utc_now(),
                    "operations": {},
                    "attempts": {},
                }
                self.save()
        except BaseException:
            self.close()
            raise

    def _acquire_exclusive_lock(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.lock_path, "a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            handle.close()
            raise SessionEvidenceError(
                f"another process holds the exclusive program-state lock "
                f"`{self.lock_path}`; refusing to open, reconcile or replay this program"
            ) from error
        handle.seek(0)
        handle.truncate()
        handle.write(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "state_path": str(self.path),
                    "acquired_at_utc": utc_now(),
                }
            )
            + "\n"
        )
        handle.flush()
        self._lock_handle = handle

    @property
    def lock_held(self) -> bool:
        return self._lock_handle is not None

    def close(self) -> None:
        if self._lock_handle is None:
            return
        try:
            fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._lock_handle.close()
            self._lock_handle = None

    def __enter__(self) -> JsonJournal:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _reconcile_interrupted(self) -> None:
        """Mark unfinished work interrupted only after the exclusive lock is held."""
        changed = False
        for operation in self.state.get("operations", {}).values():
            if operation.get("status") == "started":
                operation["status"] = "interrupted"
                operation["interrupted_at_utc"] = utc_now()
                operation["interrupted_reason"] = (
                    "exclusive program lock acquired with an unfinished operation; "
                    "no live writer remained, so the prior inference is never replayed"
                )
                changed = True
        for attempt in self.state.get("attempts", {}).values():
            if attempt.get("status") == "started":
                attempt["status"] = "interrupted"
                attempt["interrupted_at_utc"] = utc_now()
                changed = True
        if changed:
            self.save()

    def save(self) -> None:
        with self.lock:
            self.state["updated_at_utc"] = utc_now()
            write_json_atomic(self.path, self.state)

    def begin_operation(self, operation_id: str) -> None:
        with self.lock:
            operation = self.state["operations"].setdefault(operation_id, {})
            if operation.get("status") in ("started", "completed", "interrupted"):
                raise SessionEvidenceError(
                    f"operation `{operation_id}` is already {operation.get('status')}; "
                    "prior inference is never repeated"
                )
            operation["status"] = "started"
            operation["started_at_utc"] = utc_now()
            self.state["updated_at_utc"] = utc_now()
            write_json_atomic(self.path, self.state)

    def attempt_started(self, request_id: str, metadata: dict[str, Any]) -> None:
        with self.lock:
            attempts = self.state["attempts"]
            if request_id in attempts:
                raise SessionEvidenceError(
                    f"request `{request_id}` already has an attempt record; "
                    "a replacement call is not permitted"
                )
            attempts[request_id] = {
                "status": "started",
                "started_at_utc": utc_now(),
                **metadata,
            }
            self.state["updated_at_utc"] = utc_now()
            write_json_atomic(self.path, self.state)

    def attempt_finished(self, call: Any) -> None:
        with self.lock:
            attempt = self.state["attempts"].get(call.request_id)
            if attempt is None:
                raise SessionEvidenceError(
                    f"request `{call.request_id}` finished without a start record"
                )
            attempt.update(
                {
                    "status": "finished",
                    "ended_at_utc": utc_now(),
                    "outcome": call.outcome,
                    "http_status": call.http_status,
                    "active_ms": call.active_ms,
                    "visible_chars": call.visible_chars,
                }
            )
            self.state["updated_at_utc"] = utc_now()
            write_json_atomic(self.path, self.state)

    def finish_operation(
        self,
        operation_id: str,
        *,
        cell_path: str | None,
        counters: dict[str, int],
        status: str,
        stop_reason: str | None,
        duration_ms: float | None = None,
    ) -> None:
        with self.lock:
            operation = self.state["operations"].setdefault(operation_id, {})
            operation.update(
                {
                    "status": "completed" if cell_path else "invalid",
                    "ended_at_utc": utc_now(),
                    "cell_path": cell_path,
                    "counters": counters,
                    "cell_status": status,
                    "stop_reason": stop_reason,
                    "duration_ms": duration_ms,
                }
            )
            self.state["updated_at_utc"] = utc_now()
            write_json_atomic(self.path, self.state)

    def fail_operation(self, operation_id: str, reason: str) -> None:
        with self.lock:
            operation = self.state["operations"].setdefault(operation_id, {})
            operation.update(
                {
                    "status": "invalid",
                    "ended_at_utc": utc_now(),
                    "invalid_reason": reason,
                }
            )
            self.state["updated_at_utc"] = utc_now()
            write_json_atomic(self.path, self.state)

    def operation_status(self, operation_id: str) -> str:
        return str(self.state.get("operations", {}).get(operation_id, {}).get("status", "unstarted"))

    def completed_operations(self) -> set[str]:
        return {
            operation_id
            for operation_id, operation in self.state.get("operations", {}).items()
            if operation.get("status") == "completed"
        }

    def resumable_operations(self) -> set[str]:
        return {
            operation_id
            for operation_id, operation in self.state.get("operations", {}).items()
            if operation.get("status") == "unstarted"
        }


# -- evidence writing ---------------------------------------------------------


@dataclass(frozen=True)
class CellArtifacts:
    directory: Path
    cell_path: Path
    calls_path: Path
    sessions_path: Path
    cell_sha256: str
    calls_sha256: str
    sessions_sha256: str

    def to_dict(self) -> dict[str, str]:
        return {
            "directory": str(self.directory),
            "cell_path": str(self.cell_path),
            "calls_path": str(self.calls_path),
            "sessions_path": str(self.sessions_path),
            "cell_sha256": self.cell_sha256,
            "calls_sha256": self.calls_sha256,
            "sessions_sha256": self.sessions_sha256,
        }


def write_cell_evidence(root: Path, evidence: CellEvidence) -> CellArtifacts:
    directory = root / "cells" / evidence.operation_id
    if directory.exists() and (directory / "cell.json").exists():
        raise SessionEvidenceError(
            f"cell evidence already exists for `{evidence.operation_id}`; "
            "runs are immutable and a completed cell is never replaced"
        )
    directory.mkdir(parents=True, exist_ok=True)
    cell_path = directory / "cell.json"
    calls_path = directory / "calls.jsonl"
    sessions_path = directory / "sessions.jsonl"
    write_json_atomic(cell_path, evidence.to_dict())
    calls = [call for session in evidence.sessions for call in session.calls]
    calls_path.write_text(
        "\n".join(json.dumps(call.to_dict(), sort_keys=True) for call in calls)
        + ("\n" if calls else ""),
        encoding="utf-8",
    )
    sessions_path.write_text(
        "\n".join(
            json.dumps(session.to_dict(), sort_keys=True) for session in evidence.sessions
        )
        + ("\n" if evidence.sessions else ""),
        encoding="utf-8",
    )
    return CellArtifacts(
        directory=directory,
        cell_path=cell_path,
        calls_path=calls_path,
        sessions_path=sessions_path,
        cell_sha256=_sha256_file(cell_path),
        calls_sha256=_sha256_file(calls_path),
        sessions_sha256=_sha256_file(sessions_path),
    )


def load_cell(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SessionEvidenceError(f"could not read cell `{path}`: {error}") from error
    if not isinstance(document, dict):
        raise SessionEvidenceError(f"cell `{path}` must be an object")
    validate_cell(document)
    return document


def validate_cell(cell: dict[str, Any]) -> None:
    if cell.get("schema_version") != CELL_SCHEMA:
        raise SessionEvidenceError(
            f"unsupported cell schema `{cell.get('schema_version')}`"
        )
    operation_id = cell.get("operation_id")
    if not isinstance(operation_id, str) or not operation_id:
        raise SessionEvidenceError("cell has no operation_id")
    if cell.get("workload_type") not in (
        WORKLOAD_LIVE_TURN_SESSION,
        SAME_DAY_WORKLOAD_TYPE,
    ):
        raise SessionEvidenceError(
            f"{operation_id}: workload_type must be `{WORKLOAD_LIVE_TURN_SESSION}` "
            f"or `{SAME_DAY_WORKLOAD_TYPE}`; replay and live-turn evidence are not "
            "interchangeable"
        )
    planned_calls = _int(cell.get("planned_calls"), "planned_calls")
    sessions = cell.get("sessions")
    if not isinstance(sessions, list):
        raise SessionEvidenceError(f"{operation_id}: sessions must be a list")
    calls: list[dict[str, Any]] = []
    session_ids: set[str] = set()
    for session in sessions:
        if not isinstance(session, dict):
            raise SessionEvidenceError(f"{operation_id}: session record must be an object")
        session_id = session.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise SessionEvidenceError(f"{operation_id}: session has no session_id")
        if session_id in session_ids:
            raise SessionEvidenceError(
                f"{operation_id}: duplicate session identity `{session_id}`"
            )
        session_ids.add(session_id)
        session_calls = session.get("calls")
        if not isinstance(session_calls, list):
            raise SessionEvidenceError(f"{operation_id}/{session_id}: calls must be a list")
        calls.extend(session_calls)
    request_ids: set[str] = set()
    for call in calls:
        if not isinstance(call, dict):
            raise SessionEvidenceError(f"{operation_id}: call record must be an object")
        request_id = call.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            raise SessionEvidenceError(f"{operation_id}: call has no request_id")
        if request_id in request_ids:
            raise SessionEvidenceError(
                f"{operation_id}: duplicate request identity `{request_id}`"
            )
        request_ids.add(request_id)
        outcome = call.get("outcome")
        if not isinstance(outcome, str):
            raise SessionEvidenceError(f"{operation_id}/{request_id}: missing outcome")
        transport = call.get("transport") or {}
        completion = call.get("completion") or {}
        timing = call.get("timing") or {}
        output = call.get("output") or {}
        if outcome == OUTCOME_SUCCESS:
            status = transport.get("http_status")
            if status is None or not 200 <= status < 300:
                raise SessionEvidenceError(
                    f"{operation_id}/{request_id}: success without an observed 2xx"
                )
            if completion.get("finish_class") != "natural":
                raise SessionEvidenceError(
                    f"{operation_id}/{request_id}: success without a natural finish"
                )
            if output.get("visible_missing") and not (
                (call.get("tools") or {}).get("tool_calls")
            ):
                raise SessionEvidenceError(
                    f"{operation_id}/{request_id}: success without visible content"
                )
        if outcome == OUTCOME_OUTPUT_TRUNCATED:
            if completion.get("finish_class") != "ceiling":
                raise SessionEvidenceError(
                    f"{operation_id}/{request_id}: truncation without ceiling class"
                )
            status = transport.get("http_status")
            if status is None or not 200 <= status < 300:
                raise SessionEvidenceError(
                    f"{operation_id}/{request_id}: truncation without an observed 2xx"
                )
        if outcome in (OUTCOME_SKIPPED, OUTCOME_INELIGIBLE_INPUT_BOUND):
            if transport.get("http_status") is not None:
                raise SessionEvidenceError(
                    f"{operation_id}/{request_id}: skipped call recorded an HTTP status"
                )
        for name in ("ttft_any_ms", "ttft_visible_ms", "active_ms", "queue_ms", "idle_before_ms"):
            _number(timing.get(name), f"{operation_id}/{request_id}.{name}")
    context_checks = cell.get("context_checks") or []
    if context_checks and len(context_checks) > planned_calls:
        raise SessionEvidenceError(
            f"{operation_id}: {len(context_checks)} context checks for "
            f"{planned_calls} planned calls"
        )
    counters = cell.get("counters")
    if not isinstance(counters, dict):
        raise SessionEvidenceError(f"{operation_id}: counters must be an object")
    if "evidence_complete" in cell and not isinstance(cell["evidence_complete"], bool):
        raise SessionEvidenceError(
            f"{operation_id}: evidence_complete must be boolean"
        )
    audit = cell.get("cache_prefix_audit")
    if audit is not None:
        if not isinstance(audit, dict) or not isinstance(
            audit.get("consistent"), bool
        ):
            raise SessionEvidenceError(
                f"{operation_id}: cache_prefix_audit is malformed"
            )
        if audit.get("headers_used_as_proof") is not False:
            raise SessionEvidenceError(
                f"{operation_id}: cache_prefix_audit must not use headers as proof"
            )
    attempted = sum(
        1
        for call in calls
        if call.get("outcome") not in (OUTCOME_SKIPPED, OUTCOME_INELIGIBLE_INPUT_BOUND)
    )
    if counters.get("attempted_calls") != attempted:
        raise SessionEvidenceError(
            f"{operation_id}: counters.attempted_calls {counters.get('attempted_calls')} "
            f"does not match records {attempted}"
        )
    http_attempts = sum(
        1
        for call in calls
        if (call.get("transport") or {}).get("http_status") is not None
        or (call.get("transport") or {}).get("error_type") is not None
    )
    if counters.get("http_attempts") != http_attempts:
        raise SessionEvidenceError(
            f"{operation_id}: counters.http_attempts {counters.get('http_attempts')} "
            f"does not match records {http_attempts}"
        )
    successes = sum(1 for call in calls if call.get("outcome") == OUTCOME_SUCCESS)
    truncated = sum(1 for call in calls if call.get("outcome") == OUTCOME_OUTPUT_TRUNCATED)
    failures = sum(
        1
        for call in calls
        if call.get("outcome")
        not in (
            OUTCOME_SUCCESS,
            OUTCOME_OUTPUT_TRUNCATED,
            OUTCOME_SKIPPED,
            OUTCOME_INELIGIBLE_INPUT_BOUND,
        )
    )
    skipped = sum(
        1
        for call in calls
        if call.get("outcome") in (OUTCOME_SKIPPED, OUTCOME_INELIGIBLE_INPUT_BOUND)
    )
    if (successes, truncated, failures, skipped) != (
        counters.get("successful_calls"),
        counters.get("truncated_calls"),
        counters.get("failed_calls"),
        counters.get("skipped_calls"),
    ):
        raise SessionEvidenceError(
            f"{operation_id}: outcome counters do not match call records"
        )


# -- analysis -----------------------------------------------------------------


def _distribution(values: list[float]) -> dict[str, Any]:
    if not values:
        return {
            "n": 0,
            "p50": None,
            "p95": None,
            "mean": None,
            "min": None,
            "max": None,
            "estimator": "linear_interpolation_of_closest_ranks",
            "note": "no eligible observations; no value is invented",
        }
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "p50": percentile(ordered, 0.50),
        "p95": percentile(ordered, 0.95),
        "mean": sum(ordered) / len(ordered),
        "min": ordered[0],
        "max": ordered[-1],
        "estimator": "linear_interpolation_of_closest_ranks",
        "note": (
            "finite-sample descriptive value; p95 is unstable below 20 observations "
            "and is not a population tail claim"
        ),
    }


def _calls_of(cell: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        call
        for session in cell.get("sessions", [])
        for call in session.get("calls", [])
    ]


def _group_summary(cells: list[dict[str, Any]]) -> dict[str, Any]:
    calls = [call for cell in cells for call in _calls_of(cell)]
    sessions = [session for cell in cells for session in cell.get("sessions", [])]
    natural = [call for call in calls if call.get("outcome") == OUTCOME_SUCCESS]
    truncated = [call for call in calls if call.get("outcome") == OUTCOME_OUTPUT_TRUNCATED]
    failures = [
        call
        for call in calls
        if call.get("outcome")
        not in (
            OUTCOME_SUCCESS,
            OUTCOME_OUTPUT_TRUNCATED,
            OUTCOME_SKIPPED,
            OUTCOME_INELIGIBLE_INPUT_BOUND,
        )
    ]
    endpoint_failures = [
        call for call in calls if call.get("outcome") in ENDPOINT_FAILURE_OUTCOMES
    ]
    skipped = [
        call
        for call in calls
        if call.get("outcome") in (OUTCOME_SKIPPED, OUTCOME_INELIGIBLE_INPUT_BOUND)
    ]
    visible = [
        float(call["timing"]["ttft_visible_ms"])
        for call in calls
        if call.get("timing", {}).get("ttft_visible_ms") is not None
    ]
    any_token = [
        float(call["timing"]["ttft_any_ms"])
        for call in calls
        if call.get("timing", {}).get("ttft_any_ms") is not None
    ]
    active_natural = [
        float(call["timing"]["active_ms"])
        for call in natural
        if call.get("timing", {}).get("active_ms") is not None
    ]
    active_censored = [
        float(call["timing"]["active_ms"])
        for call in truncated
        if call.get("timing", {}).get("active_ms") is not None
    ]
    queue = [
        float(call["timing"]["queue_ms"])
        for call in calls
        if call.get("timing", {}).get("queue_ms") is not None
    ]
    elapsed = [
        float(session["timing"]["elapsed_ms"])
        for session in sessions
        if session.get("timing", {}).get("elapsed_ms") is not None
        and session.get("status") in (SESSION_STATUS_COMPLETED, SESSION_STATUS_CENSORED)
    ]
    idle = [
        float(session["timing"]["idle_ms"])
        for session in sessions
        if session.get("timing", {}).get("idle_ms") is not None
    ]
    cache_read = [
        call["output"]["cache_read_tokens"]
        for call in calls
        if call.get("output", {}).get("cache_read_tokens") is not None
    ]
    cache_write = [
        call["output"]["cache_write_tokens"]
        for call in calls
        if call.get("output", {}).get("cache_write_tokens") is not None
    ]
    reasoning = [
        call["output"]["provider_reasoning_tokens"]
        for call in calls
        if call.get("output", {}).get("provider_reasoning_tokens") is not None
    ]
    input_tokens = [
        call["input"]["provider_input_tokens"]
        for call in calls
        if call.get("input", {}).get("provider_input_tokens") is not None
    ]
    output_tokens = [
        call["output"]["provider_output_tokens"]
        for call in calls
        if call.get("output", {}).get("provider_output_tokens") is not None
    ]
    visible_tokens = [
        call["output"]["provider_visible_output_tokens"]
        for call in calls
        if call.get("output", {}).get("provider_visible_output_tokens") is not None
    ]
    achieved = max(
        (int(cell.get("achieved_max_inflight_calls") or 0) for cell in cells), default=0
    )
    achieved_sessions = max(
        (int(cell.get("achieved_max_concurrent_sessions") or 0) for cell in cells),
        default=0,
    )
    return {
        "cells": sorted(cell["operation_id"] for cell in cells),
        "denominators": {
            "planned_calls": sum(int(cell.get("planned_calls") or 0) for cell in cells),
            "records_total": len(calls),
            "attempted_calls": len(calls) - len(skipped),
            "successful_calls": len(natural),
            "truncated_calls": len(truncated),
            "failed_calls": len(failures),
            "endpoint_failed_calls": len(endpoint_failures),
            "unsupported_tool_calls": sum(
                1 for call in calls if call.get("outcome") == "unsupported_tool"
            ),
            "invalid_tool_argument_calls": sum(
                1 for call in calls if call.get("outcome") == "invalid_tool_arguments"
            ),
            "tool_absent_calls": sum(
                1 for call in calls if call.get("outcome") == "tool_call_absent"
            ),
            "tool_budget_calls": sum(
                1 for call in calls if call.get("outcome") == "tool_budget_exhausted"
            ),
            "skipped_or_ineligible_calls": len(skipped),
            "visible_ttft_observed": len(visible),
            "any_ttft_observed": len(any_token),
            "active_natural_observed": len(active_natural),
            "active_censored_observed": len(active_censored),
            "session_elapsed_observed": len(elapsed),
            "provider_input_observed": len(input_tokens),
            "provider_output_observed": len(output_tokens),
            "provider_visible_output_observed": len(visible_tokens),
            "reasoning_observed": len(reasoning),
            "cache_read_observed": len(cache_read),
            "cache_write_observed": len(cache_write),
            "partition_consistent": (
                len(natural) + len(truncated) + len(failures) + len(skipped) == len(calls)
            ),
        },
        "achieved_max_inflight_calls": achieved,
        "achieved_max_concurrent_sessions": achieved_sessions,
        "time_to_first_visible_content_ms": _distribution(visible),
        "time_to_first_any_token_ms": _distribution(any_token),
        "active_latency_natural_ms": _distribution(active_natural),
        "active_latency_censored_lower_bound_ms": _distribution(active_censored),
        "session_elapsed_ms": _distribution(elapsed),
        "queue_ms": _distribution(queue),
        "idle_ms": _distribution(idle),
        "tokens": {
            "provider_input_sum": sum(input_tokens) if input_tokens else None,
            "provider_output_sum": sum(output_tokens) if output_tokens else None,
            "reasoning_sum": sum(reasoning) if reasoning else None,
            "visible_output_sum": sum(visible_tokens) if visible_tokens else None,
            "trials_reporting": {
                "input": len(input_tokens),
                "output": len(output_tokens),
                "reasoning": len(reasoning),
                "visible_output": len(visible_tokens),
            },
        },
        "cache_telemetry": {
            "cache_read_sum": sum(cache_read) if cache_read else None,
            "cache_write_sum": sum(cache_write) if cache_write else None,
            "calls_reporting_read": len(cache_read),
            "calls_reporting_write": len(cache_write),
            "timing_inferred_benefit": None,
            "timing_inferred_benefit_note": (
                "not computed: a clean-cache paired baseline is required and provider "
                "cache state is not directly observable"
            ),
            "unknown_state": "telemetry absent is recorded as unknown, never zero",
        },
        "metric_eligibility": {
            "time_to_first_visible_content": (
                "observed" if visible else "not_observed"
            ),
            "time_to_first_any_token": "observed" if any_token else "not_observed",
            "active_natural_completion": (
                "observed" if active_natural else "not_observed"
            ),
            "active_completion_latency": (
                "observed_natural_only"
                if active_natural and not active_censored
                else "lower_bound_only"
                if active_censored
                else "not_observed"
            ),
            "session_elapsed": "observed" if elapsed else "not_observed",
            "cache_benefit": "not_computed_requires_clean_cache_baseline",
            "token_normalized_throughput": "requires_independent_sizing",
            "qualification": "not_applicable_thresholds_null",
        },
        "session_status_counts": _counts(
            session.get("status") for session in sessions
        ),
        "outcome_counts": _counts(call.get("outcome") for call in calls),
    }


def _counts(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _context_summary(cells: list[dict[str, Any]]) -> dict[str, Any]:
    checks = [
        check
        for cell in cells
        for check in cell.get("context_checks", [])
    ]
    by_size: dict[str, Any] = {}
    for size in sorted({int(check["requested_input_tokens"]) for check in checks}):
        subset = [check for check in checks if int(check["requested_input_tokens"]) == size]
        by_size[str(size)] = {
            "checks": len(subset),
            "by_position": {
                position: _counts(
                    check["classification"]
                    for check in subset
                    if check["position"] == position
                )
                for position in ("early", "middle", "late")
            },
            "api_accepted": sum(1 for check in subset if check["api_accepted"]),
            "demonstrated_effective": sum(
                1 for check in subset if check["demonstrated_effective"]
            ),
            "refused": sum(1 for check in subset if check["classification"] == "refused"),
            "output_truncated": sum(
                1 for check in subset if check["classification"] == "output_truncated"
            ),
            "unverified": sum(
                1
                for check in subset
                if check["classification"] == "api_accepted_but_unverified"
            ),
            "malformed_response": sum(
                1
                for check in subset
                if check["classification"] == "malformed_response"
            ),
            "note": (
                "API acceptance is not effective context. A failed exact check does "
                "not prove server-side truncation."
            ),
        }
    return {
        "checks_total": len(checks),
        "by_requested_input_tokens": by_size,
        "denominators": {
            "checks_total": len(checks),
            "classified": sum(
                1 for check in checks if check.get("classification") is not None
            ),
            "missing": sum(
                1 for check in checks if check.get("classification") is None
            ),
        },
    }


def analyze_cells(
    root: Path,
    *,
    manifest: dict[str, Any] | None = None,
    require_complete: bool = True,
) -> dict[str, Any]:
    cells_dir = root / "cells"
    if not cells_dir.is_dir():
        raise SessionEvidenceError(f"no cells directory under `{root}`")
    cells: list[dict[str, Any]] = []
    seen_operations: set[str] = set()
    for path in sorted(cells_dir.glob("*/cell.json")):
        cell = load_cell(path)
        operation_id = cell["operation_id"]
        if operation_id in seen_operations:
            raise SessionEvidenceError(
                f"duplicate cell evidence for operation `{operation_id}`"
            )
        seen_operations.add(operation_id)
        if manifest is not None:
            declared = {item["operation_id"] for item in manifest.get("operations", [])}
            if operation_id not in declared:
                raise SessionEvidenceError(
                    f"cell `{operation_id}` is not in the supplied manifest"
                )
        cells.append(cell)
    if manifest is not None:
        declared = {item["operation_id"] for item in manifest.get("operations", [])}
        missing = sorted(declared - seen_operations)
        if missing and require_complete:
            raise SessionEvidenceError(
                f"{len(missing)} declared operations have no cell evidence: "
                + ", ".join(missing[:8])
            )
    else:
        missing = []

    groups: dict[tuple[str, int], list[dict[str, Any]]] = {}
    windows: dict[tuple[str, int, int | None], list[dict[str, Any]]] = {}
    templates: dict[tuple[str, str], list[dict[str, Any]]] = {}
    context_cells: list[dict[str, Any]] = []
    for cell in cells:
        scenario = str(cell["scenario"])
        concurrency = int(cell["concurrency"])
        if scenario == "context_sentinels":
            context_cells.append(cell)
            continue
        groups.setdefault((scenario, concurrency), []).append(cell)
        windows.setdefault((scenario, concurrency, cell.get("window")), []).append(cell)
        for template in sorted(
            {session.get("template") for session in cell.get("sessions", [])}
        ):
            templates.setdefault((scenario, str(template)), []).append(cell)

    report: dict[str, Any] = {
        "schema_version": ANALYSIS_SCHEMA,
        "analysis": "session_dynamics_descriptive",
        "generated_at_utc": utc_now(),
        "program_id": (manifest or {}).get("program_id"),
        "plan_sha256": (manifest or {}).get("plan_sha256"),
        "cell_count": len(cells),
        "workload_type": (
            cells[0].get("workload_type") if cells else None
        ),
        "missing_operations": missing,
        "scenarios": {
            scenario: {
                str(concurrency): _group_summary(group)
                for (group_scenario, concurrency), group in sorted(groups.items())
                if group_scenario == scenario
            }
            for scenario in sorted({scenario for scenario, _ in groups})
        },
        "windows": {
            f"{scenario}:C{concurrency}:W{window}": _group_summary(group)
            for (scenario, concurrency, window), group in sorted(
                windows.items(), key=lambda item: (item[0][0], item[0][1], str(item[0][2]))
            )
        },
        "templates": {
            f"{scenario}:{template}": _group_summary(group)
            for (scenario, template), group in sorted(templates.items())
        },
        "context_sentinels": _context_summary(context_cells),
        "claims": {
            "qualification": None,
            "thresholds": null_thresholds(),
            "endpoint_qualified": False,
            "population_inference": False,
            "universal_ceiling_tail_claim": False,
            "note": (
                "These are finite-sample descriptive observations. No SLO, capacity "
                "limit, qualification verdict, or universal fixed-cap censoring claim "
                "is supported."
            ),
        },
        "small_sample_warning": (
            "Twenty calls per cell and five sessions per scenario are small samples. "
            "Window-level and pooled percentiles are descriptive, not population tail "
            "estimates."
        ),
        "exclusions": [
            "No call was retried or replaced.",
            "Skipped and ineligible calls remain in the denominator.",
            "Missing provider telemetry is null, never zero.",
        ],
    }
    return report


def null_thresholds() -> dict[str, None]:
    return {
        "first_visible_ms": None,
        "active_latency_ms": None,
        "session_elapsed_ms": None,
        "censoring_fraction": None,
        "error_rate": None,
    }


def render_markdown(report: dict[str, Any]) -> str:
    same_day = (
        report.get("workload_type") == "live_turn_session_dynamics_same_day"
    )
    lines = [
        "# Same-day session-dynamics results"
        if same_day
        else "# Session-dynamics-v1 results",
        "",
        f"Generated: {report.get('generated_at_utc')}",
        "",
        "**Descriptive finite-sample evidence.** No threshold is ratified, no endpoint "
        "is qualified, and no population or universal ceiling-tail claim is made.",
        "",
        "## Scenario x concurrency",
        "",
        "| Scenario | C | planned | attempts | natural | truncated | failed | skipped | "
        "visible p50 (ms) | active natural p50 (ms) | session p50 (ms) | achieved calls |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for scenario, levels in sorted(report.get("scenarios", {}).items()):
        for level, summary in sorted(levels.items(), key=lambda item: int(item[0])):
            den = summary["denominators"]
            lines.append(
                "| {scenario} | {level} | {planned} | {attempts} | {natural} | {truncated} | "
                "{failed} | {skipped} | {visible} | {active} | {session} | {achieved} |".format(
                    scenario=scenario,
                    level=level,
                    planned=den["planned_calls"],
                    attempts=den["attempted_calls"],
                    natural=den["successful_calls"],
                    truncated=den["truncated_calls"],
                    failed=den["failed_calls"],
                    skipped=den["skipped_or_ineligible_calls"],
                    visible=_fmt(summary["time_to_first_visible_content_ms"]["p50"]),
                    active=_fmt(summary["active_latency_natural_ms"]["p50"]),
                    session=_fmt(summary["session_elapsed_ms"]["p50"]),
                    achieved=summary["achieved_max_inflight_calls"],
                )
            )
    if not same_day:
        lines.extend(
            [
                "",
                "## Window distributions",
                "",
                "Each window is reported separately; pooling across windows is only a "
                "compatible descriptive summary, not a reliability claim.",
                "",
                "| Cell | n visible | visible p50 | visible p95 | active natural p50 | censored |",
                "| --- | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for key, summary in sorted(report.get("windows", {}).items()):
            den = summary["denominators"]
            visible = summary["time_to_first_visible_content_ms"]
            active = summary["active_latency_natural_ms"]
            lines.append(
                f"| {key} | {visible['n']} | {_fmt(visible['p50'])} | {_fmt(visible['p95'])} | "
                f"{_fmt(active['p50'])} | {den['truncated_calls']} |"
            )
    context = report.get("context_sentinels", {})
    lines.extend(
        [
            "",
            "## Context sentinels",
            "",
            "| Requested input | checks | API accepted | demonstrated effective | refused | "
            "output truncated | malformed | unverified |",
            "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for size, summary in sorted(
        context.get("by_requested_input_tokens", {}).items(), key=lambda item: int(item[0])
    ):
        lines.append(
            f"| {size} | {summary['checks']} | {summary['api_accepted']} | "
            f"{summary['demonstrated_effective']} | {summary['refused']} | "
            f"{summary['output_truncated']} | {summary['malformed_response']} | "
            f"{summary['unverified']} |"
        )
    lines.extend(
        [
            "",
            "## Denominators and exclusions",
            "",
            "- Failed, truncated, skipped and ineligible calls stay in the table; none is "
            "silently omitted or replaced.",
            "- A truncated call's active time is a lower bound, not a completion time.",
            "- Cache telemetry is provider-reported only; timing-inferred benefit is not "
            "computed and unknown state is not zero.",
            "- API-accepted context is not demonstrated effective context.",
            "",
            "## Limits",
            "",
            report.get("small_sample_warning", ""),
            "",
        ]
    )
    return "\n".join(lines)


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}"


def write_analysis(root: Path, report: dict[str, Any]) -> dict[str, str]:
    json_path = root / "analysis.json"
    markdown_path = root / "analysis.md"
    write_json_atomic(json_path, report)
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return {
        "analysis_json": str(json_path),
        "analysis_json_sha256": _sha256_file(json_path),
        "analysis_markdown": str(markdown_path),
        "analysis_markdown_sha256": _sha256_file(markdown_path),
    }


def validate_manifest_document(
    manifest: dict[str, Any], *, require_unreleased: bool = False
) -> None:
    """Use the single canonical validator and translate its error type."""
    try:
        _validate_manifest_document(
            manifest, require_unreleased=require_unreleased
        )
    except SessionProgramError as error:
        raise SessionEvidenceError(str(error)) from error
