"""Session-dynamics-v1 program: finite plan, workload templates and manifest.

This module owns the *planning* half of the session suite. It does not open a
socket. Everything here is deterministic and offline:

- the nine scenarios and the 54-cell matrix come from the versioned program
  definition in ``configs/session-programs/session-dynamics-v1.json``;
- synthetic coding/document/transcript templates are generated from a declared
  seed, so a clean clone reproduces byte-identical workloads;
- every cell gets an explicit cache treatment, session/turn identity scheme,
  deadline, output path and argv;
- the manifest records source/workload/plan hashes and the conservative
  operational dollar envelope.

The program is an implementation/preflight contract. ``live_released_operation_ids``
is empty, so nothing in this plan authorizes paid inference.
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SESSION_PROGRAM_SCHEMA = "session-program/1.0.0"
SESSION_PLAN_SCHEMA = "session-plan/1.0.0"
SESSION_MANIFEST_SCHEMA = "session-manifest/1.0.0"

PROGRAM_ID = "session-dynamics-v1"
DIRECTIVE_ID = "D0003"
WORKLOAD_LIVE_TURN_SESSION = "live_turn_session_dynamics"

#: D0010 same-day default. A distinct program/workload identity and schema
#: version keep every legacy plan, journal and cell byte-identical. The
#: same-day matrix is finite and calendar-free: 27 screening cells plus three
#: context sentinels, executed in one foreground invocation.
SAME_DAY_PROGRAM_SCHEMA = "2.0"
SAME_DAY_PLAN_SCHEMA = "session-plan/2.0.0"
SAME_DAY_MANIFEST_SCHEMA = "session-manifest/2.0.0"
SAME_DAY_PROGRAM_ID = "session-dynamics-sameday-v1"
SAME_DAY_DIRECTIVE_ID = "D0010"
SAME_DAY_WORKLOAD_TYPE = "live_turn_session_dynamics_same_day"
SAME_DAY_OPERATION_PREFIX = "SDS"
SAME_DAY_CELL_DEADLINE_S = 600.0
SAME_DAY_INVOCATION_DEADLINE_S = 6.0 * 3600.0
SAME_DAY_MATRIX_CELLS = 30
SAME_DAY_MATRIX_ATTEMPTS = 558
SAME_DAY_PHASES = ("session_screening", "context_sentinels")
SAME_DAY_PROGRAM_STATE = "IMPLEMENTATION_AND_OFFLINE_PREFLIGHT"
SAME_DAY_RUN_MODE = "foreground_same_day"

#: Declared approximation used only to size synthetic fixtures and to enforce
#: the input bound before a request is sent. Provider ``usage.prompt_tokens`` is
#: the authoritative observation and is recorded separately on every call.
CHARS_PER_TOKEN = 4

#: Width of the leading, fixed-size namespace marker. Cache partitioning is
#: proved from these bytes, never from a grouping header: a provider that
#: ignores unknown headers would otherwise share the same prompt prefix.
CACHE_NAMESPACE_WIDTH = 80

#: Manifest release fields a manager may change without invalidating the
#: execution identity. `manifest_sha256` must still be re-sealed after a change.
RELEASE_MUTABLE_FIELDS = ("release_anchor_utc", "live_released_operation_ids")

#: Frozen template cycle for five sessions. At least one of each template is
#: present; the mapping is part of the plan hash and cannot vary at runtime.
TEMPLATE_CYCLE = ("coding", "document", "transcript", "coding", "document")

SCENARIOS: dict[str, dict[str, Any]] = {
    "independent": {
        "workload": "independent_prompts",
        "history": "none",
        "shared_prefix": False,
        "pair": ("cache-prefix-pair-v1", "baseline"),
    },
    "stable_prefix": {
        "workload": "stable_prefix",
        "history": "none",
        "shared_prefix": True,
        "pair": ("cache-prefix-pair-v1", "treatment"),
    },
    "prefix_invalidation": {
        "workload": "prefix_invalidation",
        "history": "none",
        "shared_prefix": True,
        "pair": ("prefix-invalidation-pair-v1", "treatment"),
    },
    "growing_transcript": {
        "workload": "growing_transcript",
        "history": "retained_assistant_replies",
        "shared_prefix": True,
        "pair": ("transcript-control-pair-v1", "baseline"),
    },
    "interleaved_sessions": {
        "workload": "growing_transcript",
        "history": "retained_assistant_replies",
        "shared_prefix": True,
        "pair": ("scheduling-pair-v1", "treatment"),
    },
    "client_reconnect": {
        "workload": "growing_transcript",
        "history": "retained_assistant_replies",
        "shared_prefix": True,
        "pair": ("connection-pair-v1", "treatment"),
    },
    "explicit_compaction": {
        "workload": "growing_transcript",
        "history": "retained_assistant_replies_compacted",
        "shared_prefix": True,
        "pair": ("compaction-pair-v1", "treatment"),
    },
    "idle_resume": {
        "workload": "growing_transcript",
        "history": "retained_assistant_replies",
        "shared_prefix": True,
        "pair": ("idle-pair-v1", "treatment"),
    },
    "tool_roundtrip": {
        "workload": "live_turn_tool_continuation",
        "history": "tool_protocol",
        "shared_prefix": True,
        "pair": (None, "none"),
    },
    "context_sentinels": {
        "workload": "context_sentinel",
        "history": "none",
        "shared_prefix": False,
        "pair": (None, "none"),
    },
}

#: Scenario order used to freeze the finite schedule inside each window.
TEMPORAL_SCENARIOS = ("growing_transcript", "interleaved_sessions")
CONTEXT_SIZES = (32768, 65536, 131072)
SENTINEL_POSITIONS = ("early", "middle", "late")

WINDOW_OFFSET_HOURS = {1: 0, 2: 6, 3: 24, 4: 30}

LIMIT_KEYS = (
    "max_total_new_inference_attempts",
    "max_concurrent_sessions",
    "max_total_inflight_model_calls",
    "max_completion_tokens_per_call",
    "session_input_tokens_initial_target",
    "session_input_tokens_max",
    "context_input_tokens_max",
    "request_timeout_s",
    "cell_process_deadline_s",
    "max_active_execution_hours",
    "max_program_elapsed_hours",
    "automatic_inference_retries",
    "replacement_trials",
    "unlisted_warmup_or_readiness_inference",
)

SAME_DAY_EXTRA_LIMIT_KEYS = ("max_foreground_invocation_seconds",)


@dataclass(frozen=True)
class ProgramProfile:
    """Per-family identity and matrix contract.

    The v1 profile is the temporal-release program accepted for D0009. The v2
    profile is the D0010 same-day default. Validation reads the profile from the
    declared schema/program id; a document cannot borrow one family's identity
    while carrying another family's matrix.
    """

    family: str
    program_schema: str
    program_id: str
    directive_id: str
    plan_schema: str
    manifest_schema: str
    manifest_type: str
    workload_type: str
    cells: int
    attempts: int
    phases: tuple[str, ...]
    screening_cells: int
    temporal_cells: int
    context_cells: int
    limit_keys: tuple[str, ...]


TEMPORAL_V1_PROFILE = ProgramProfile(
    family="temporal_v1",
    program_schema="1.0",
    program_id=PROGRAM_ID,
    directive_id=DIRECTIVE_ID,
    plan_schema=SESSION_PLAN_SCHEMA,
    manifest_schema=SESSION_MANIFEST_SCHEMA,
    manifest_type="session_dynamics_preflight",
    workload_type=WORKLOAD_LIVE_TURN_SESSION,
    cells=54,
    attempts=1038,
    phases=("session_screening", "temporal_repeats", "context_sentinels"),
    screening_cells=27,
    temporal_cells=24,
    context_cells=3,
    limit_keys=LIMIT_KEYS,
)

SAME_DAY_V2_PROFILE = ProgramProfile(
    family="same_day_v2",
    program_schema=SAME_DAY_PROGRAM_SCHEMA,
    program_id=SAME_DAY_PROGRAM_ID,
    directive_id=SAME_DAY_DIRECTIVE_ID,
    plan_schema=SAME_DAY_PLAN_SCHEMA,
    manifest_schema=SAME_DAY_MANIFEST_SCHEMA,
    manifest_type="session_dynamics_same_day_preflight",
    workload_type=SAME_DAY_WORKLOAD_TYPE,
    cells=SAME_DAY_MATRIX_CELLS,
    attempts=SAME_DAY_MATRIX_ATTEMPTS,
    phases=SAME_DAY_PHASES,
    screening_cells=27,
    temporal_cells=0,
    context_cells=3,
    limit_keys=LIMIT_KEYS + SAME_DAY_EXTRA_LIMIT_KEYS,
)

PROFILES = (TEMPORAL_V1_PROFILE, SAME_DAY_V2_PROFILE)


def profile_for_program_schema(schema: object) -> ProgramProfile:
    for profile in PROFILES:
        if profile.program_schema == schema:
            return profile
    raise SessionProgramError(
        f"unsupported program schema `{schema}`; supported: "
        + ", ".join(profile.program_schema for profile in PROFILES)
    )


def profile_for_plan_schema(schema: object) -> ProgramProfile:
    for profile in PROFILES:
        if profile.plan_schema == schema:
            return profile
    raise SessionProgramError(f"unsupported plan schema `{schema}`")


def profile_for_manifest_schema(schema: object) -> ProgramProfile:
    for profile in PROFILES:
        if profile.manifest_schema == schema:
            return profile
    raise SessionProgramError(f"unsupported manifest schema `{schema}`")


def profile_for_plan(plan: dict[str, Any]) -> ProgramProfile:
    return profile_for_plan_schema(plan.get("schema_version"))


def profile_for_program(program: SessionProgram) -> ProgramProfile:
    for profile in PROFILES:
        if profile.program_id == program.program_id:
            return profile
    raise SessionProgramError(f"unsupported program_id `{program.program_id}`")

#: Files whose contents define the observed behavior and are hashed into the
#: manifest. A missing file is a build error, never a silently absent hash.
SOURCE_FILES = (
    "src/endpoint_bench/session_program.py",
    "src/endpoint_bench/session_engine.py",
    "src/endpoint_bench/resolver_worker.py",
    "src/endpoint_bench/session_evidence.py",
    "src/endpoint_bench/cli.py",
    "src/endpoint_bench/trials.py",
    "src/endpoint_bench/probes.py",
    "src/endpoint_bench/bounds.py",
    "src/endpoint_bench/qualification.py",
    "src/endpoint_bench/network_guard.py",
    "src/endpoint_bench/registry.py",
    "src/endpoint_bench/cost.py",
    "scripts/analyze_bounded.py",
)


class SessionProgramError(ValueError):
    """Raised when the program definition, plan or manifest is invalid."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _relative_or_absolute(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def plan_content_sha256(plan: dict[str, Any]) -> str:
    """Canonical content digest of a plan, independent of generation time."""
    return sha256_text(
        canonical_json(
            {
                key: value
                for key, value in plan.items()
                if key not in ("plan_sha256", "generated_at_utc")
            }
        )
    )


def manifest_content_sha256(manifest: dict[str, Any]) -> str:
    """Canonical self-digest of a manifest document."""
    return sha256_text(
        canonical_json(
            {key: value for key, value in manifest.items() if key != "manifest_sha256"}
        )
    )


def manifest_execution_identity_sha256(manifest: dict[str, Any]) -> str:
    """Digest of everything except the manager-controlled release fields.

    A live release may set `release_anchor_utc` and
    `live_released_operation_ids`, then re-seal `manifest_sha256`. The execution
    identity stays stable across that release-only edit.
    """
    excluded = {"manifest_sha256", "execution_identity_sha256"}
    document = {key: value for key, value in manifest.items() if key not in excluded}
    release = dict(document.get("release") or {})
    for release_field in RELEASE_MUTABLE_FIELDS:
        release.pop(release_field, None)
    document["release"] = release
    return sha256_text(canonical_json(document))


def estimate_tokens(text: str) -> int:
    """Declared client-side size estimate. Never reported as provider truth."""
    return max(1, (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN)


def cache_namespace_marker(label: str) -> str:
    """Return the fixed-width leading marker for one cache namespace.

    The marker is the *actual* first bytes of the request content. Two requests
    share a provider prompt prefix only when these bytes and the content after
    them match; a custom grouping header is not proof of isolation.
    """
    if not isinstance(label, str) or not label:
        raise SessionProgramError("cache namespace label must be a non-empty string")
    if "\n" in label or "\r" in label:
        raise SessionProgramError("cache namespace label must be a single line")
    body = f"CACHE_NAMESPACE={label}"
    if len(body) + 1 > CACHE_NAMESPACE_WIDTH:
        raise SessionProgramError(
            f"cache namespace label `{label}` does not fit a {CACHE_NAMESPACE_WIDTH}-byte marker"
        )
    return body.ljust(CACHE_NAMESPACE_WIDTH - 1, ".") + "\n"


def prefix_with_namespace(prefix: str, label: str) -> str:
    """Replace the first line of a prefix with an equal-length namespace marker.

    The length and every designated fact at the end of the prefix are preserved,
    so paired conditions remain matched in size while their leading bytes are
    explicitly equal or unequal.
    """
    marker = cache_namespace_marker(label)
    newline = prefix.find("\n")
    width = (newline + 1) if newline >= 0 else len(marker)
    width = max(width, len(marker))
    if width > len(prefix):
        raise SessionProgramError("prefix is shorter than the namespace marker")
    replacement = marker.ljust(width, " ")[:width]
    return replacement + prefix[width:]


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SessionProgramError(f"{name} must be a positive integer")
    return value


def _finite_positive(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise SessionProgramError(f"{name} must be a positive number")
    return float(value)


# -- finite program definition ------------------------------------------------


@dataclass(frozen=True)
class Operation:
    operation_id: str
    phase: str
    scenario: str
    concurrent_sessions: int
    sessions: int
    model_calls_per_session: int
    max_inference_attempts: int
    window: int | None = None
    start_offset_hours: int | None = None
    requested_input_tokens: int | None = None
    sentinel_positions: tuple[str, ...] = ()
    replicates_per_position: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "phase": self.phase,
            "scenario": self.scenario,
            "concurrent_sessions": self.concurrent_sessions,
            "sessions": self.sessions,
            "model_calls_per_session": self.model_calls_per_session,
            "max_inference_attempts": self.max_inference_attempts,
            "window": self.window,
            "start_offset_hours": self.start_offset_hours,
            "requested_input_tokens": self.requested_input_tokens,
            "sentinel_positions": list(self.sentinel_positions),
            "replicates_per_position": self.replicates_per_position,
        }


@dataclass(frozen=True)
class SessionProgram:
    source_path: Path
    source_sha256: str
    program_id: str
    directive_id: str
    state: str
    target: str | None
    limits: dict[str, Any]
    operations: tuple[Operation, ...]
    live_released_operation_ids: tuple[str, ...]
    raw: dict[str, Any]
    family: str = "temporal_v1"

    @property
    def total_max_attempts(self) -> int:
        return sum(operation.max_inference_attempts for operation in self.operations)

    def operation(self, operation_id: str) -> Operation:
        for operation in self.operations:
            if operation.operation_id == operation_id:
                return operation
        raise SessionProgramError(f"unknown operation_id `{operation_id}`")


def load_program(path: Path | str) -> SessionProgram:
    source_path = Path(path)
    if not source_path.is_file():
        raise SessionProgramError(f"program file does not exist: {source_path}")
    try:
        raw = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SessionProgramError(f"could not read program `{source_path}`: {error}") from error
    if not isinstance(raw, dict):
        raise SessionProgramError("program root must be a JSON object")

    profile = profile_for_program_schema(raw.get("schema_version"))
    program_id = raw.get("program_id")
    if program_id != profile.program_id:
        raise SessionProgramError(
            f"program_id `{program_id}` is not `{profile.program_id}`"
        )
    directive_id = raw.get("directive_id")
    if directive_id != profile.directive_id:
        raise SessionProgramError(
            f"directive_id `{directive_id}` is not `{profile.directive_id}`"
        )
    if raw.get("live_released_operation_ids") != []:
        raise SessionProgramError(
            "this program is an implementation/preflight contract and must not list "
            "live-released operations"
        )

    limits = _validate_limits(raw.get("limits"), "program", profile)

    raw_operations = raw.get("operations")
    if not isinstance(raw_operations, list) or not raw_operations:
        raise SessionProgramError("program `operations` must be a non-empty array")
    operations: list[Operation] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw_operations):
        operation = _parse_operation(entry, index, limits, profile)
        if operation.operation_id in seen:
            raise SessionProgramError(
                f"duplicate operation_id `{operation.operation_id}`"
            )
        seen.add(operation.operation_id)
        operations.append(operation)

    _validate_matrix(operations, limits, profile)
    return SessionProgram(
        source_path=source_path,
        source_sha256=sha256_bytes(source_path.read_bytes()),
        program_id=program_id,
        directive_id=directive_id,
        state=str(raw.get("state")),
        target=raw.get("target"),
        limits=dict(limits),
        operations=tuple(operations),
        live_released_operation_ids=(),
        raw=raw,
        family=profile.family,
    )


def _parse_operation(
    entry: object,
    index: int,
    limits: dict[str, Any],
    profile: ProgramProfile,
) -> Operation:
    """Parse one operation with the family's frozen matrix rules."""
    context = f"operations[{index}]"
    if not isinstance(entry, dict):
        raise SessionProgramError(f"{context} must be an object")
    operation_id = entry.get("operation_id")
    if not isinstance(operation_id, str) or not operation_id:
        raise SessionProgramError(f"{context}: missing operation_id")
    scenario = entry.get("scenario")
    if scenario not in SCENARIOS:
        raise SessionProgramError(f"{context}: unknown scenario `{scenario}`")
    concurrency = _positive_int(
        entry.get("concurrent_sessions"), f"{context}.concurrent_sessions"
    )
    if concurrency > limits["max_concurrent_sessions"]:
        raise SessionProgramError(
            f"{context}: concurrency {concurrency} exceeds program maximum"
        )
    attempts = _positive_int(
        entry.get("max_inference_attempts"), f"{context}.max_inference_attempts"
    )
    phase = str(entry.get("phase"))
    if phase not in profile.phases:
        raise SessionProgramError(
            f"{context}: phase `{phase}` is not part of the "
            f"{profile.program_id} matrix"
        )
    if scenario == "context_sentinels":
        sessions = 0
        calls = 1
        requested = _positive_int(
            entry.get("requested_input_tokens"), f"{context}.requested_input_tokens"
        )
        if requested > limits["context_input_tokens_max"]:
            raise SessionProgramError(
                f"{context}: requested {requested} tokens exceeds context maximum"
            )
        if requested not in CONTEXT_SIZES:
            raise SessionProgramError(f"{context}: unsupported context size {requested}")
        positions = entry.get("sentinel_positions")
        if positions != list(SENTINEL_POSITIONS):
            raise SessionProgramError(
                f"{context}: sentinel_positions must be {list(SENTINEL_POSITIONS)}"
            )
        replicates = _positive_int(
            entry.get("replicates_per_position"), f"{context}.replicates_per_position"
        )
        if attempts != len(SENTINEL_POSITIONS) * replicates:
            raise SessionProgramError(
                f"{context}: max_inference_attempts must equal positions x replicates"
            )
    else:
        sessions = _positive_int(entry.get("sessions"), f"{context}.sessions")
        calls = _positive_int(
            entry.get("model_calls_per_session"), f"{context}.model_calls_per_session"
        )
        requested = None
        positions = ()
        replicates = None
        if attempts != sessions * calls:
            raise SessionProgramError(
                f"{context}: max_inference_attempts must equal sessions x model_calls_per_session"
            )
        if calls != 4:
            raise SessionProgramError(f"{context}: every session scenario uses four calls")
        if sessions != 5:
            raise SessionProgramError(f"{context}: every session scenario uses five sessions")
    window = entry.get("window")
    offset = entry.get("start_offset_hours")
    if profile.family == "same_day_v2":
        if window is not None or offset is not None:
            raise SessionProgramError(
                f"{context}: the same-day matrix is calendar-free; window and "
                "start_offset_hours must be null"
            )
    elif window is not None:
        if window not in WINDOW_OFFSET_HOURS:
            raise SessionProgramError(f"{context}: unsupported window {window}")
        if offset != WINDOW_OFFSET_HOURS[window]:
            raise SessionProgramError(
                f"{context}: window {window} must start at {WINDOW_OFFSET_HOURS[window]}h"
            )
    return Operation(
        operation_id=operation_id,
        phase=phase,
        scenario=str(scenario),
        concurrent_sessions=concurrency,
        sessions=sessions,
        model_calls_per_session=calls,
        max_inference_attempts=attempts,
        window=window,
        start_offset_hours=offset,
        requested_input_tokens=entry.get("requested_input_tokens"),
        sentinel_positions=tuple(entry.get("sentinel_positions") or ()),
        replicates_per_position=entry.get("replicates_per_position"),
    )


def _validate_matrix(
    operations: list[Operation],
    limits: dict[str, Any],
    profile: ProgramProfile,
) -> None:
    """Require the exact finite operation matrix the directive released for design."""
    total = sum(operation.max_inference_attempts for operation in operations)
    if total != limits["max_total_new_inference_attempts"]:
        raise SessionProgramError(
            f"operations plan {total} attempts, but the program limit is "
            f"{limits['max_total_new_inference_attempts']}"
        )
    screening = [op for op in operations if op.phase == "session_screening"]
    temporal = [op for op in operations if op.phase == "temporal_repeats"]
    context = [op for op in operations if op.phase == "context_sentinels"]
    if (
        len(operations) != profile.cells
        or len(screening) != profile.screening_cells
        or len(temporal) != profile.temporal_cells
        or len(context) != profile.context_cells
    ):
        raise SessionProgramError(
            f"expected {profile.cells} operations: {profile.screening_cells} screening, "
            f"{profile.temporal_cells} temporal, {profile.context_cells} context"
        )
    if total != profile.attempts:
        raise SessionProgramError(
            f"matrix plans {total} attempts; the {profile.program_id} matrix is "
            f"exactly {profile.attempts}"
        )
    screening_scenarios = {op.scenario for op in screening}
    if screening_scenarios != set(SCENARIOS) - {"context_sentinels"}:
        raise SessionProgramError("screening phase must cover all nine session scenarios")
    for scenario in SCENARIOS:
        if scenario == "context_sentinels":
            continue
        levels = sorted(
            op.concurrent_sessions for op in screening if op.scenario == scenario
        )
        if levels != [1, 2, 4]:
            raise SessionProgramError(
                f"scenario `{scenario}` must cover C=1,2,4 exactly once"
            )
    if profile.family == "temporal_v1":
        for window in WINDOW_OFFSET_HOURS:
            cells = [op for op in temporal if op.window == window]
            if len(cells) != 6:
                raise SessionProgramError(f"window {window} must contain six cells")
            if {op.scenario for op in cells} != set(TEMPORAL_SCENARIOS):
                raise SessionProgramError(f"window {window} has the wrong scenarios")
            if sorted(op.concurrent_sessions for op in cells) != [1, 1, 2, 2, 4, 4]:
                raise SessionProgramError(f"window {window} has the wrong concurrency coverage")
    else:
        if any(op.window is not None or op.start_offset_hours is not None for op in operations):
            raise SessionProgramError("the same-day matrix cannot carry a calendar offset")
        for op in operations:
            if not op.operation_id.startswith(f"{SAME_DAY_OPERATION_PREFIX}-"):
                raise SessionProgramError(
                    f"same-day operation `{op.operation_id}` must use the "
                    f"`{SAME_DAY_OPERATION_PREFIX}-` identity prefix"
                )
            expected = 20 if op.phase == "session_screening" else 6
            if op.max_inference_attempts != expected:
                raise SessionProgramError(
                    f"same-day operation `{op.operation_id}` must plan {expected} calls"
                )
            if op.phase == "context_sentinels" and op.replicates_per_position != 2:
                raise SessionProgramError(
                    f"same-day context operation `{op.operation_id}` must use two "
                    "replicates per sentinel position"
                )
    if sorted(op.requested_input_tokens for op in context) != list(CONTEXT_SIZES):
        raise SessionProgramError("context phase must cover 32768, 65536, 131072")


# -- deterministic synthetic workloads ---------------------------------------


@dataclass(frozen=True)
class WorkloadTemplate:
    name: str
    seed: int
    prefix: str
    tasks: tuple[str, ...]
    sentinel_facts: tuple[str, ...]

    @property
    def prefix_tokens(self) -> int:
        return estimate_tokens(self.prefix)

    @property
    def task_tokens(self) -> tuple[int, ...]:
        return tuple(estimate_tokens(task) for task in self.tasks)

    def to_descriptor(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "seed": self.seed,
            "prefix_sha256": sha256_text(self.prefix),
            "prefix_chars": len(self.prefix),
            "prefix_tokens_estimated": self.prefix_tokens,
            "task_sha256": [sha256_text(task) for task in self.tasks],
            "task_chars": [len(task) for task in self.tasks],
            "task_tokens_estimated": list(self.task_tokens),
            "sentinel_facts": list(self.sentinel_facts),
        }


_CODING_TERMS = (
    "parser", "scheduler", "cursor", "manifest", "buffer", "worker", "ledger",
    "retry", "checkpoint", "queue", "schema", "hash", "frame", "trace", "lock",
)
_DOC_TERMS = (
    "requirement", "scope", "rollout", "owner", "risk", "decision", "milestone",
    "dependency", "acceptance", "evidence", "caveat", "review", "interface",
)
_TRANSCRIPT_TERMS = (
    "agenda", "question", "estimate", "blocker", "followup", "budget", "design",
    "concern", "action", "deadline", "tradeoff", "decision", "context",
)


def _paragraphs(seed: int, terms: tuple[str, ...], target_chars: int, *, coding: bool) -> str:
    rng = random.Random(seed)
    parts: list[str] = []
    total = 0
    index = 0
    while total < target_chars:
        term_a = rng.choice(terms)
        term_b = rng.choice(terms)
        term_c = rng.choice(terms)
        n = rng.randint(7, 27)
        if coding:
            line = (
                f"def {term_a}_{index}(payload: dict) -> dict:\n"
                f"    # {term_b} path {index}; deterministic fixture, no network.\n"
                f"    value = payload.get('{term_b}', {n})\n"
                f"    return {{'{term_c}': value, 'step': {index}, 'ok': True}}\n"
            )
        else:
            line = (
                f"Paragraph {index}: the {term_a} depends on the {term_b} and the "
                f"reviewer records {n} observations about {term_c}. "
                f"No measurement is inferred from this statement; it is workload "
                f"material only. "
            )
        parts.append(line)
        total += len(line)
        index += 1
    return "".join(parts)[:target_chars]


def build_template(name: str, seed: int, *, prefix_chars: int, task_chars: int) -> WorkloadTemplate:
    """Build one deterministic template at fixed prefix/task lengths.

    All three templates share the same character budgets so stable-prefix,
    invalidation, and independent prompts are matched in size. The facts are
    designed to be recoverable by a deterministic local compactor.
    """
    if name not in ("coding", "document", "transcript"):
        raise SessionProgramError(f"unknown template `{name}`")
    facts = (
        f"BUILD_ID={sha256_text(f'{name}:{seed}:build')[:12]}",
        f"MIGRATION_DEADLINE=2031-04-{1 + seed % 27:02d}",
        f"CACHE_NAMESPACE=ns-{sha256_text(f'{name}:{seed}:ns')[:8]}",
        f"ROLLBACK_CODE=RB-{sha256_text(f'{name}:{seed}:rb')[:10]}",
    )
    if name == "coding":
        terms, coding = _CODING_TERMS, True
        header = (
            "Repository fixture for a deterministic code-review session. "
            "The files below are synthetic and contain no credentials.\n"
        )
    elif name == "document":
        terms, coding = _DOC_TERMS, False
        header = (
            "Product specification fixture for a deterministic document session. "
            "Every statement is synthetic workload material.\n"
        )
    else:
        terms, coding = _TRANSCRIPT_TERMS, False
        header = (
            "Meeting transcript fixture for a deterministic session. "
            "Speakers and decisions are synthetic.\n"
        )
    body_budget = max(0, prefix_chars - len(header))
    facts_line = "\nDesignated facts: " + "; ".join(facts) + "\n"
    body_budget = max(0, body_budget - len(facts_line))
    prefix = header + _paragraphs(seed, terms, body_budget, coding=coding)
    prefix = prefix[:body_budget]
    prefix += " " * max(0, body_budget - len(prefix))
    prefix += facts_line
    if len(prefix) < prefix_chars:
        prefix += " " * (prefix_chars - len(prefix))
    prefix = prefix[:prefix_chars]

    tasks: list[str] = []
    for turn in range(4):
        lead = (
            f"Turn {turn + 1}. Using only the material above, continue the "
            f"{'review' if coding else 'analysis'} with a short deterministic "
            f"response. Preserve the designated facts if you summarize. "
        )
        task_body = _paragraphs(seed + 11 + turn, terms, max(0, task_chars - len(lead)), coding=coding)
        task = (lead + task_body)[:task_chars]
        if len(task) < task_chars:
            task += " " * (task_chars - len(task))
        tasks.append(task)
    return WorkloadTemplate(
        name=name,
        seed=seed,
        prefix=prefix,
        tasks=tuple(tasks),
        sentinel_facts=facts,
    )


def build_templates(seed: int = 20261102, *, prefix_tokens: int = 8192, task_tokens: int = 2048) -> dict[str, WorkloadTemplate]:
    prefix_chars = prefix_tokens * CHARS_PER_TOKEN
    task_chars = task_tokens * CHARS_PER_TOKEN
    return {
        name: build_template(
            name,
            seed + index,
            prefix_chars=prefix_chars,
            task_chars=task_chars,
        )
        for index, name in enumerate(("coding", "document", "transcript"))
    }


def context_fixture(
    *,
    requested_input_tokens: int,
    position: str,
    replicate: int,
    seed: int,
    namespace: str = "",
) -> dict[str, Any]:
    """Build one deterministic context-sentinel request.

    The sentinel code is placed early, middle, or late in a filler document and
    the final user instruction asks only for that code. The check is exact, so a
    confusing or reasoning-heavy answer is recorded as unverified rather than
    graded.

    ``requested_input_tokens`` is a *full-request* budget: the serialized
    ``messages`` array, including its JSON wrapper, is sized to fit it. Sizing
    only the prompt text leaves the wrapper to push the delivered request over
    the declared bound, which is how a 131,072-token cell previously sent
    nothing.
    """
    if requested_input_tokens not in CONTEXT_SIZES:
        raise SessionProgramError(f"unsupported context size {requested_input_tokens}")
    if position not in SENTINEL_POSITIONS:
        raise SessionProgramError(f"unsupported sentinel position {position}")
    code = f"SENTINEL-{sha256_text(f'{seed}:{requested_input_tokens}:{position}:{replicate}')[:12].upper()}"
    instruction = (
        "\nFinal instruction: reply with only the verification code that was "
        "embedded above. Do not explain and do not add punctuation.\n"
    )
    marker = (
        f"\nThe verification code is {code}. "
        "Remember this code; the final instruction asks for it.\n"
    )
    namespace_line = (
        cache_namespace_marker(f"ctx:{namespace}:{position}:r{replicate}")
        if namespace
        else ""
    )
    wrapper_chars = len(json.dumps([{"role": "user", "content": ""}]))
    budget_chars = requested_input_tokens * CHARS_PER_TOKEN
    prompt = ""
    for _ in range(8):
        filler_chars = max(
            1,
            budget_chars
            - len(marker)
            - len(instruction)
            - len(namespace_line)
            - wrapper_chars,
        )
        filler = _paragraphs(
            seed + replicate,
            _DOC_TERMS,
            filler_chars,
            coding=False,
        )
        if position == "early":
            cut = max(1, len(filler) // 10)
        elif position == "middle":
            cut = len(filler) // 2
        else:
            cut = max(1, len(filler) * 9 // 10)
        document = filler[:cut] + marker + filler[cut:]
        prompt = namespace_line + document + instruction
        messages = [{"role": "user", "content": prompt}]
        estimated = estimate_tokens(json.dumps(messages))
        if estimated <= requested_input_tokens:
            break
        budget_chars -= (estimated - requested_input_tokens) * CHARS_PER_TOKEN
    messages = [{"role": "user", "content": prompt}]
    estimated = estimate_tokens(json.dumps(messages))
    if estimated > requested_input_tokens:
        raise SessionProgramError(
            f"could not size a {requested_input_tokens}-token context fixture; "
            f"best effort was {estimated} estimated tokens"
        )
    return {
        "requested_input_tokens": requested_input_tokens,
        "position": position,
        "replicate": replicate,
        "seed": seed,
        "sentinel_code": code,
        "prompt": prompt,
        "prompt_sha256": sha256_text(prompt),
        "prompt_chars": len(prompt),
        "messages_sha256": sha256_text(json.dumps(messages)),
        "client_estimated_input_tokens": estimated,
        "check": {"kind": "exact_trimmed_match", "expected": code},
    }


# -- cell plans ---------------------------------------------------------------


@dataclass(frozen=True)
class SessionSpec:
    session_id: str
    session_index: int
    template: str
    seed: int
    group_id: str
    prefix_group_id: str | None
    turn_group_ids: tuple[str, ...]
    turn_namespace_labels: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "session_index": self.session_index,
            "template": self.template,
            "seed": self.seed,
            "group_id": self.group_id,
            "prefix_group_id": self.prefix_group_id,
            "turn_group_ids": list(self.turn_group_ids),
            "turn_namespace_labels": list(self.turn_namespace_labels),
        }


@dataclass(frozen=True)
class CellPlan:
    operation: Operation
    namespace: str
    order_seed: int
    sessions: tuple[SessionSpec, ...]
    template_mapping: dict[str, str]
    cache_treatment: dict[str, Any]
    output_path: str
    template_prefix_tokens: int
    template_task_tokens: int
    session_order: tuple[str, ...]
    turn_order: tuple[str, ...]
    context_fixtures: tuple[dict[str, Any], ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.operation.to_dict(),
            "namespace": self.namespace,
            "order_seed": self.order_seed,
            "session_count": len(self.sessions),
            "sessions": [session.to_dict() for session in self.sessions],
            "template_mapping": dict(sorted(self.template_mapping.items())),
            "cache_treatment": self.cache_treatment,
            "session_order": list(self.session_order),
            "turn_order": list(self.turn_order),
            "output_path": self.output_path,
            "template_prefix_tokens_estimated": self.template_prefix_tokens,
            "template_task_tokens_estimated": self.template_task_tokens,
            "context_fixtures": [
                {
                    key: value
                    for key, value in fixture.items()
                    if key != "prompt"
                }
                for fixture in self.context_fixtures
            ],
        }


def _group_id(namespace: str, label: str) -> str:
    return sha256_text(f"{namespace}:{label}")[:16]


def _cache_treatment(operation: Operation, namespace: str) -> dict[str, Any]:
    scenario = SCENARIOS[operation.scenario]
    pair_id, pair_role = scenario["pair"]
    shared = bool(scenario["shared_prefix"])
    return {
        "pair_id": pair_id,
        "pair_role": pair_role,
        "shared_prefix": shared,
        "affinity": "disabled",
        "affinity_header_sent": False,
        "cache_group_header": "X-Cache-Group",
        "session_header": "X-Session-Id",
        "first_turn_measured": True,
        "hidden_warmup": False,
        "group_scope": (
            "shared_within_session"
            if shared
            else "unique_per_request"
        ),
        "namespace": namespace,
        "isolation": {
            "mechanism": "leading_content_marker",
            "marker_width_chars": CACHE_NAMESPACE_WIDTH,
            "marker_format": "CACHE_NAMESPACE=<label> padded with dots and a newline",
            "applies_to": "first line of the first message content",
            "headers_are_not_proof": True,
            "independent": "unique_per_request",
            "stable_prefix": "shared_per_session_turns_1_to_4",
            "prefix_invalidation": "new_marker_at_turn_3",
            "explicit_compaction": "marker_stable_summary_appended_at_turn_3",
            "context_sentinels": "unique_per_request",
        },
        "matched": {
            "prefix_tokens_estimated": 8192,
            "suffix_tokens_estimated": 2048,
            "output_cap_tokens": 8192,
            "template_mapping": "coding,document,transcript,coding,document",
        },
        "telemetry_separation": {
            "provider_cache_hit": "provider usage cache-read/write counters, when reported",
            "timing_inferred_benefit": "not computed; requires a paired clean-cache baseline",
            "unknown_state": "recorded as unknown, never zero",
        },
    }


def _session_specs(
    operation: Operation,
    namespace: str,
    base_seed: int,
) -> tuple[SessionSpec, ...]:
    specs: list[SessionSpec] = []
    for index, template_name in enumerate(TEMPLATE_CYCLE):
        session_id = f"{operation.operation_id}-s{index + 1:02d}"
        session_seed = base_seed + 1009 * (index + 1)
        shared = bool(SCENARIOS[operation.scenario]["shared_prefix"])
        prefix_group = _group_id(namespace, f"{session_id}:prefix") if shared else None
        turn_groups = tuple(
            _group_id(
                namespace,
                (
                    f"{session_id}:shared"
                    if shared
                    and not (
                        operation.scenario == "prefix_invalidation" and turn >= 3
                    )
                    else f"{session_id}:turn{turn}:unique"
                ),
            )
            for turn in range(1, operation.model_calls_per_session + 1)
        )
        turn_labels = tuple(
            _turn_namespace_label(
                operation=operation,
                namespace=namespace,
                session_id=session_id,
                prefix_group=prefix_group,
                turn=turn,
            )
            for turn in range(1, operation.model_calls_per_session + 1)
        )
        # Build the marker once here so a malformed label fails plan generation,
        # not a live cell after the journal intent has been written.
        for label in turn_labels:
            cache_namespace_marker(label)
        specs.append(
            SessionSpec(
                session_id=session_id,
                session_index=index,
                template=template_name,
                seed=session_seed,
                group_id=_group_id(namespace, session_id),
                prefix_group_id=prefix_group,
                turn_group_ids=turn_groups,
                turn_namespace_labels=turn_labels,
            )
        )
    return tuple(specs)


def _turn_namespace_label(
    *,
    operation: Operation,
    namespace: str,
    session_id: str,
    prefix_group: str | None,
    turn: int,
) -> str:
    """Freeze the actual leading cache namespace for one turn.

    - independent: unique per request, so no prior request can prewarm it.
    - stable_prefix / growing transcripts / tools: one label per session.
    - prefix_invalidation: a new label at turn 3, the explicit invalidation
      point, while turns 1-2 keep the original label.
    """
    cell = namespace[:12]
    if operation.scenario == "independent":
        return f"{cell}:req:{session_id}:t{turn:02d}"
    if operation.scenario == "prefix_invalidation" and turn >= 3:
        return f"{cell}:inv:{session_id}"
    if prefix_group is None:
        return f"{cell}:req:{session_id}:t{turn:02d}"
    return f"{cell}:grp:{prefix_group}"


def _context_fixtures(
    operation: Operation, base_seed: int, namespace: str
) -> tuple[dict[str, Any], ...]:
    fixtures: list[dict[str, Any]] = []
    for position_index, position in enumerate(operation.sentinel_positions):
        for replicate in range(1, int(operation.replicates_per_position or 0) + 1):
            fixtures.append(
                context_fixture(
                    requested_input_tokens=int(operation.requested_input_tokens or 0),
                    position=position,
                    replicate=replicate,
                    seed=base_seed + 97 * position_index,
                    namespace=namespace,
                )
            )
    return tuple(fixtures)


def build_plan(
    program: SessionProgram,
    *,
    output_dir: Path | str,
    release_nonce: str | None = None,
) -> dict[str, Any]:
    """Build the frozen, resumable plan. No I/O beyond source hashes.

    The v1 family keeps the 54-cell temporal matrix and its original nonce
    default. The v2 family is the D0010 same-day matrix: 30 cells, no calendar
    offset, and one finite foreground invocation.
    """
    profile = profile_for_program(program)
    out = Path(output_dir)
    if release_nonce is None:
        if profile.family == "same_day_v2":
            nonce = sha256_text(
                f"{program.program_id}:{program.source_sha256}:{SAME_DAY_DIRECTIVE_ID}"
            )[:16]
        else:
            nonce = sha256_text(
                f"{program.program_id}:{program.source_sha256}:2026-11-02"
            )[:16]
    else:
        nonce = release_nonce
    templates = build_templates()
    template_descriptors = {
        name: template.to_descriptor() for name, template in sorted(templates.items())
    }
    source_hashes: dict[str, str] = {}
    repo_root = Path(__file__).resolve().parents[2]
    for relative in SOURCE_FILES:
        path = repo_root / relative
        if not path.is_file():
            raise SessionProgramError(f"manifest source file is missing: {relative}")
        source_hashes[relative] = sha256_bytes(path.read_bytes())

    cells: list[CellPlan] = []
    for operation in program.operations:
        namespace = sha256_text(f"{nonce}:{operation.operation_id}")[:20]
        base_seed = int(namespace[:8], 16)
        session_specs = (
            ()
            if operation.scenario == "context_sentinels"
            else _session_specs(operation, namespace, base_seed)
        )
        session_order = [spec.session_id for spec in session_specs]
        random.Random(base_seed).shuffle(session_order)
        if operation.scenario == "interleaved_sessions":
            turn_order = [
                f"{session_id}-t{turn:02d}"
                for turn in range(1, operation.model_calls_per_session + 1)
                for session_id in session_order
            ]
        else:
            turn_order = [
                f"{session_id}-t{turn:02d}"
                for session_id in session_order
                for turn in range(1, operation.model_calls_per_session + 1)
            ]
        cells.append(
            CellPlan(
                operation=operation,
                namespace=namespace,
                order_seed=base_seed,
                sessions=session_specs,
                session_order=tuple(session_order),
                turn_order=tuple(turn_order),
                template_mapping={
                    f"session-{index + 1:02d}": template
                    for index, template in enumerate(TEMPLATE_CYCLE)
                },
                cache_treatment=_cache_treatment(operation, namespace),
                output_path=f"cells/{operation.operation_id}",
                template_prefix_tokens=8192,
                template_task_tokens=2048,
                context_fixtures=(
                    _context_fixtures(operation, base_seed, namespace)
                    if operation.scenario == "context_sentinels"
                    else ()
                ),
            )
        )

    ordered = _freeze_order(cells, nonce, profile)
    schedule = _schedule(ordered, program.limits, profile)
    plan: dict[str, Any] = {
        "schema_version": profile.plan_schema,
        "plan_id": f"{program.program_id}-{nonce}",
        "program_id": program.program_id,
        "directive_id": program.directive_id,
        "workload_type": profile.workload_type,
        "generated_at_utc": utc_now(),
        "release": {
            "state": program.state,
            "release_nonce": nonce,
            "release_anchor_utc": None,
            "live_released_operation_ids": [],
            "note": (
                "Offline same-day preflight. Execution is one finite foreground "
                "invocation; non-loopback traffic requires an explicit "
                "--authorize-live run."
                if profile.family == "same_day_v2"
                else (
                    "Offline implementation/preflight only. Actual window anchors and "
                    "live operation release are manager-controlled."
                )
            ),
        },
        "target": program.target,
        "program_path": _relative_or_absolute(program.source_path, repo_root),
        "limits": dict(program.limits),
        "totals": {
            "cells": len(cells),
            "inference_attempts": sum(
                cell.operation.max_inference_attempts for cell in cells
            ),
            "sessions": sum(
                len(cell.sessions) for cell in cells if cell.operation.sessions
            ),
            "model_calls": sum(
                cell.operation.max_inference_attempts for cell in cells
            ),
            "phases": {
                phase: sum(1 for cell in cells if cell.operation.phase == phase)
                for phase in profile.phases
            },
        },
        "identity": {
            "scenario": "explicit per cell",
            "template": "frozen cycle coding,document,transcript,coding,document",
            "seed": "derived from release nonce and operation_id",
            "group": "explicit shared/unique ids per session/turn",
            "session": "<operation_id>-sNN",
            "turn": "t01..t04",
            "request": "<session_id>-tNN",
            "model_region_client_reasoning": (
                "bound from the registry at dry-run; reasoning parameter is unset "
                "(provider default) and recorded as such"
            ),
            "affinity": "disabled for every cell",
            "workload_mode": "live_dependent_turns",
            "connection": "pooled per session unless client_reconnect changes it at turn 3",
        },
        "workloads": {
            "templates": template_descriptors,
            "chars_per_token_estimate": CHARS_PER_TOKEN,
            "cache_isolation": {
                "mechanism": "leading_content_marker",
                "marker_width_chars": CACHE_NAMESPACE_WIDTH,
                "proof": "actual first-message bytes recorded per call and audited after the cell",
                "headers_are_not_proof": True,
            },
            "note": (
                "Client token estimates size fixtures and enforce the pre-send bound. "
                "They are not provider token counts and never replace usage."
            ),
        },
        "source_hashes": dict(sorted(source_hashes.items())),
        "program_sha256": program.source_sha256,
        "operations": [cell.to_dict() for cell in ordered],
        "schedule": schedule,
        "exclusions": (
            [
                "No temporal-repeat, scheduled-window, multi-day, C8, offered-rate, "
                "sustained-load, >131072-input, higher-output-cap, affinity-on, or "
                "additional-deployment cell is part of this plan.",
                "One same-day invocation measures one finite observation. It does not "
                "establish day-to-day stability, an SLO, a capacity limit, a "
                "qualification verdict, or a population tail.",
                "Qualification thresholds are null; the three context cells measure "
                "API acceptance and sentinel recall only.",
                "Legacy session-dynamics-v1 temporal observations are not reused, "
                "relabelled or extended by this plan.",
            ]
            if profile.family == "same_day_v2"
            else [
                "No C8, offered-rate, sustained-load, >131072-input, higher-output-cap, "
                "affinity-on, or additional-deployment cell is part of this plan.",
                "Qualification thresholds are null; no SLO, capacity limit, or universal "
                "ceiling conclusion is pre-registered.",
                "Historical pilot cells are not reused or relabelled as new windows.",
            ]
        ),
        "output_root": (
            str(out)
            if profile.family == "temporal_v1"
            else _relative_or_absolute(out, repo_root)
        ),
    }
    if profile.family == "same_day_v2":
        plan["run_mode"] = {
            "kind": SAME_DAY_RUN_MODE,
            "description": "one foreground invocation; no calendar dependency",
            "calendar_anchors": False,
            "not_before_windows": False,
            "scheduled_waits": False,
            "background_monitors": False,
            "automatic_repeat_blocks": False,
            "invocation_deadline_s": program.limits[
                "max_foreground_invocation_seconds"
            ],
            "cell_deadline_s": program.limits["cell_process_deadline_s"],
            "analysis": "written by the same invocation after execution stops",
        }
    plan["plan_sha256"] = plan_content_sha256(plan)
    return plan


def _freeze_order(
    cells: list[CellPlan], nonce: str, profile: ProgramProfile
) -> list[CellPlan]:
    """Balanced deterministic order within each phase.

    The order is frozen in the plan and is never reordered based on observed
    speed. The v1 family keeps one group per temporal window; the same-day
    family has screening followed by context sentinels and no calendar group.
    """
    groups: dict[str, list[CellPlan]] = {}
    for cell in cells:
        if cell.operation.phase == "temporal_repeats":
            key = f"temporal-window-{cell.operation.window}"
        else:
            key = cell.operation.phase
        groups.setdefault(key, []).append(cell)
    ordered: list[CellPlan] = []
    phase_order = (
        ["session_screening", "context_sentinels"]
        if profile.family == "same_day_v2"
        else [
            "session_screening",
            "temporal-window-1",
            "temporal-window-2",
            "temporal-window-3",
            "temporal-window-4",
            "context_sentinels",
        ]
    )
    if sorted(groups) != sorted(phase_order):
        raise SessionProgramError(
            f"unexpected plan phases: {sorted(groups)}; expected {sorted(phase_order)}"
        )
    for key in phase_order:
        group = list(groups[key])
        group.sort(key=lambda cell: cell.operation.operation_id)
        seed = int(sha256_text(f"{nonce}:{key}")[:8], 16)
        random.Random(seed).shuffle(group)
        ordered.extend(group)
    return ordered


def _schedule(
    cells: list[CellPlan], limits: dict[str, Any], profile: ProgramProfile
) -> list[dict[str, Any]]:
    """Freeze the finite execution order and its time guards.

    The v1 family carries temporal-anchor offsets. The same-day family carries
    only program-start offsets: every cell is eligible immediately, and the
    single bound is the finite foreground invocation deadline.
    """
    if profile.family == "same_day_v2":
        return _schedule_same_day(cells, limits)
    deadline = float(limits["cell_process_deadline_s"])
    schedule: list[dict[str, Any]] = []
    for index, cell in enumerate(cells):
        window = cell.operation.window
        if cell.operation.phase == "context_sentinels":
            anchor = "temporal_anchor"
            not_before = 30 * 3600 + 6 * deadline
        elif window is None:
            anchor = "program_start"
            not_before = 0.0
        else:
            anchor = "temporal_anchor"
            not_before = float(WINDOW_OFFSET_HOURS[window]) * 3600.0
        schedule.append(
            {
                "sequence": index + 1,
                "operation_id": cell.operation.operation_id,
                "phase": cell.operation.phase,
                "window": window,
                "anchor": anchor,
                "not_before_offset_s": not_before,
                "cell_process_deadline_s": deadline,
                "max_inference_attempts": cell.operation.max_inference_attempts,
                "output_path": cell.output_path,
            }
        )
    schedule.append(
        {
            "sequence": len(schedule) + 1,
            "operation_id": None,
            "phase": "guard",
            "anchor": "program_start",
            "max_screening_elapsed_s": 12 * 3600,
            "note": (
                "If screening has not finished within 12h, the temporal anchor cannot "
                "be set inside the 48h elapsed cap; stop rather than backfill or relabel."
            ),
        }
    )
    _validate_schedule(schedule, limits, profile)
    return schedule


def _schedule_same_day(
    cells: list[CellPlan], limits: dict[str, Any]
) -> list[dict[str, Any]]:
    """Freeze the calendar-free 30-cell order for one foreground invocation."""
    deadline = float(limits["cell_process_deadline_s"])
    invocation = float(limits["max_foreground_invocation_seconds"])
    schedule: list[dict[str, Any]] = []
    for index, cell in enumerate(cells):
        schedule.append(
            {
                "sequence": index + 1,
                "operation_id": cell.operation.operation_id,
                "phase": cell.operation.phase,
                "window": None,
                "anchor": "program_start",
                "not_before_offset_s": 0.0,
                "cell_process_deadline_s": deadline,
                "max_inference_attempts": cell.operation.max_inference_attempts,
                "output_path": cell.output_path,
            }
        )
    schedule.append(
        {
            "sequence": len(schedule) + 1,
            "operation_id": None,
            "phase": "guard",
            "anchor": "program_start",
            "max_invocation_elapsed_s": invocation,
            "cell_deadline_total_s": len(cells) * deadline,
            "note": (
                "One foreground invocation. If the invocation deadline is reached, "
                "stop starting cells and preserve every unstarted cell as skipped."
            ),
        }
    )
    _validate_schedule(schedule, limits, SAME_DAY_V2_PROFILE)
    return schedule


def _validate_schedule(
    schedule: list[dict[str, Any]],
    limits: dict[str, Any],
    profile: ProgramProfile,
) -> None:
    if profile.family == "same_day_v2":
        _validate_schedule_same_day(schedule, limits)
        return
    active_hours = sum(
        item.get("cell_process_deadline_s") or 0.0 for item in schedule
    ) / 3600.0
    if active_hours > limits["max_active_execution_hours"]:
        raise SessionProgramError(
            f"worst-case planned active time {active_hours:.2f}h exceeds the "
            f"{limits['max_active_execution_hours']}h cap"
        )
    temporal = [item for item in schedule if item.get("window") is not None]
    by_window: dict[int, list[dict[str, Any]]] = {}
    for item in temporal:
        by_window.setdefault(int(item["window"]), []).append(item)
    for window, items in by_window.items():
        window_end = WINDOW_OFFSET_HOURS[window] * 3600 + len(items) * limits[
            "cell_process_deadline_s"
        ]
        next_offsets = [
            WINDOW_OFFSET_HOURS[other] * 3600
            for other in WINDOW_OFFSET_HOURS
            if WINDOW_OFFSET_HOURS[other] * 3600 > WINDOW_OFFSET_HOURS[window] * 3600
        ]
        if next_offsets and window_end > min(next_offsets):
            raise SessionProgramError(
                f"window {window} can run past the next window start"
            )
    context = [item for item in schedule if item["phase"] == "context_sentinels"]
    if context:
        window_four_end = 30 * 3600 + 6 * limits["cell_process_deadline_s"]
        if context[0]["not_before_offset_s"] < window_four_end:
            raise SessionProgramError("context phase must not overlap window 4")


def _validate_schedule_same_day(
    schedule: list[dict[str, Any]], limits: dict[str, Any]
) -> None:
    invocation = float(limits["max_foreground_invocation_seconds"])
    deadline = float(limits["cell_process_deadline_s"])
    cells = [item for item in schedule if item.get("operation_id")]
    guards = [item for item in schedule if item.get("phase") == "guard"]
    if len(guards) != 1:
        raise SessionProgramError("same-day schedule must contain exactly one guard")
    guard = guards[0]
    if guard.get("max_invocation_elapsed_s") != invocation:
        raise SessionProgramError(
            "same-day guard does not carry the finite invocation deadline"
        )
    if float(guard.get("cell_deadline_total_s") or 0.0) > invocation:
        raise SessionProgramError(
            "worst-case cell deadlines exceed the foreground invocation deadline"
        )
    for item in cells:
        if item.get("window") is not None:
            raise SessionProgramError("same-day schedule cannot carry a window")
        if item.get("anchor") != "program_start":
            raise SessionProgramError("same-day schedule must anchor at program_start")
        if float(item.get("not_before_offset_s") or 0.0) != 0.0:
            raise SessionProgramError(
                "same-day schedule cannot carry a not-before offset"
            )
        if float(item.get("cell_process_deadline_s") or 0.0) != deadline:
            raise SessionProgramError(
                "same-day schedule cell deadline does not match the program limit"
            )
    if len(cells) * deadline > limits["max_active_execution_hours"] * 3600.0:
        raise SessionProgramError(
            "worst-case cell deadlines exceed the active-execution cap"
        )


# -- manifest ----------------------------------------------------------------


def _conservative_program_usd(program: SessionProgram, rates: dict[str, float]) -> float:
    """Conservative worst-case envelope from the finite plan.

    Each call is bounded by the session input maximum and the output cap. That
    overstates every 10K cell, which is the point: the value is an operational
    guard, not a prediction.
    """
    input_tokens = 0
    output_tokens = 0
    for operation in program.operations:
        if operation.scenario == "context_sentinels":
            per_call_input = int(operation.requested_input_tokens or 0)
        else:
            per_call_input = int(program.limits["session_input_tokens_max"])
        input_tokens += per_call_input * operation.max_inference_attempts
        output_tokens += (
            int(program.limits["max_completion_tokens_per_call"])
            * operation.max_inference_attempts
        )
    nominal = (
        input_tokens / 1_000_000 * rates["prompt_per_million_tokens"]
        + output_tokens / 1_000_000 * rates["completion_per_million_tokens"]
    )
    return round(nominal * rates["safety_multiplier"], 6)


def _cell_usd_envelope(
    cell: dict[str, Any], limits: dict[str, Any], rates: dict[str, float]
) -> float:
    attempts = int(cell["max_inference_attempts"])
    per_call_input = (
        int(cell["requested_input_tokens"])
        if cell["scenario"] == "context_sentinels"
        else int(limits["session_input_tokens_max"])
    )
    per_call_output = int(limits["max_completion_tokens_per_call"])
    nominal = (
        attempts * per_call_input / 1_000_000 * rates["prompt_per_million_tokens"]
        + attempts * per_call_output / 1_000_000 * rates["completion_per_million_tokens"]
    )
    return round(nominal * rates["safety_multiplier"], 6)


def build_manifest(
    plan: dict[str, Any],
    *,
    manifest_path: Path,
    plan_path: Path | None = None,
    target: dict[str, Any] | None = None,
    operational_rates: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Build the execution manifest with exact supported argv.

    The manifest is a preflight contract. It contains no credential values, only
    the environment-variable name the executor may read at runtime.
    """
    profile = profile_for_plan(plan)
    if plan.get("plan_sha256") != plan_content_sha256(plan):
        raise SessionProgramError("plan hash is stale; rebuild the plan before manifesting")
    rates = operational_rates or {
        "prompt_per_million_tokens": 0.30,
        "completion_per_million_tokens": 1.20,
        "safety_multiplier": 1.5,
        "source": "existing local verified rate card, 2026-11-01",
    }
    limits = plan["limits"]
    cells = plan["operations"]
    target_block = copy.deepcopy(
        target
        or {
            "deployment_key": None,
            "model_id": plan.get("target"),
            "base_url": None,
            "protocol": None,
            "region": None,
            "credential_env_name": None,
            "bound": False,
            "note": "generate-only plan; bind a registry target in dry-run",
        }
    )
    operations: list[dict[str, Any]] = []
    for cell in cells:
        cell_id = cell["operation_id"]
        attempts = cell["max_inference_attempts"]
        cell_envelope = _cell_usd_envelope(cell, limits, rates)
        argv = [
            "endpoint-bench",
            "session",
            "execute",
            "--manifest",
            str(manifest_path),
            "--operation",
            cell_id,
            "--execute",
            "--max-requests",
            str(attempts),
            "--max-usd",
            str(cell_envelope),
            "--authorize-live",
        ]
        operations.append(
            {
                "operation_id": cell_id,
                "workload_type": plan.get("workload_type", WORKLOAD_LIVE_TURN_SESSION),
                "phase": cell["phase"],
                "scenario": cell["scenario"],
                "window": cell["window"],
                "start_offset_hours": cell.get("start_offset_hours"),
                "concurrent_sessions": cell["concurrent_sessions"],
                "sessions": cell["session_count"],
                "model_calls_per_session": cell["model_calls_per_session"],
                "max_inference_attempts": attempts,
                "requested_input_tokens": cell.get("requested_input_tokens"),
                "output_path": cell["output_path"],
                "argv": argv,
                "model": target_block.get("model_id"),
                "region": target_block.get("region"),
                "client": {
                    "name": "endpoint-bench-session-client",
                    "connection_reuse": "pooled_per_session",
                    "session_affinity": False,
                    "streaming": True,
                    "reasoning_parameter": "unset_provider_default",
                },
                "timeouts": {
                    "request_timeout_s": limits["request_timeout_s"],
                    "cell_process_deadline_s": limits["cell_process_deadline_s"],
                },
                "session_max_input_tokens": (
                    limits["context_input_tokens_max"]
                    if cell["scenario"] == "context_sentinels"
                    else limits["session_input_tokens_max"]
                ),
                "max_output_tokens": limits["max_completion_tokens_per_call"],
                "cache_treatment": copy.deepcopy(cell["cache_treatment"]),
                "session_ids": [
                    session["session_id"] for session in cell["sessions"]
                ],
                "template_mapping": dict(cell["template_mapping"]),
                "identity_scheme": copy.deepcopy(plan["identity"]),
            }
        )
    attempts_total = sum(item["max_inference_attempts"] for item in operations)
    if attempts_total != limits["max_total_new_inference_attempts"]:
        raise SessionProgramError(
            f"manifest plans {attempts_total} attempts, expected "
            f"{limits['max_total_new_inference_attempts']}"
        )
    envelope = _conservative_program_usd(
        _program_from_plan(plan), rates  # type: ignore[arg-type]
    )
    manifest: dict[str, Any] = {
        "schema_version": profile.manifest_schema,
        "manifest_type": profile.manifest_type,
        "generated_at_utc": utc_now(),
        "program_id": plan["program_id"],
        "directive_id": plan["directive_id"],
        "workload_type": plan.get("workload_type", profile.workload_type),
        "plan_id": plan["plan_id"],
        "plan_sha256": plan["plan_sha256"],
        "plan_path": str(plan_path or "plan.json"),
        "program_path": plan.get("program_path"),
        "release": copy.deepcopy(plan["release"]),
        "target": target_block,
        "limits": dict(limits),
        "timeouts": {
            "request_timeout_s": limits["request_timeout_s"],
            "cell_process_deadline_s": limits["cell_process_deadline_s"],
            "timeout_equality_proved": True,
        },
        "totals": {
            **copy.deepcopy(plan["totals"]),
            "max_inference_attempts": attempts_total,
            "operational_usd_envelope": envelope,
            "operational_usd_note": (
                "Worst-case client-side envelope: every call bounded by the session "
                "input maximum and output cap, multiplied by the declared safety "
                "factor. Not a spending authorization and not a provider billing cap."
            ),
        },
        "operational_rates": dict(rates),
        "source_hashes": dict(plan["source_hashes"]),
        "program_sha256": plan["program_sha256"],
        "workloads": copy.deepcopy(plan["workloads"]),
        "operations": operations,
        "schedule": copy.deepcopy(plan["schedule"]),
        "exclusions": list(plan["exclusions"]),
        "run_policy": {
            "one_active_cell": True,
            "max_concurrent_sessions": limits["max_concurrent_sessions"],
            "max_inflight_model_calls": limits["max_total_inflight_model_calls"],
            "automatic_retries": 0,
            "replacement_trials": 0,
            "hidden_warmup": False,
            "affinity": "disabled",
            "on_invalid_evidence": "stop_further_cells_preserve_partial",
            "on_rate_limit_exhaustion": "stop_further_cells_preserve_partial",
            "censoring": "measured outcome; preserve lower bounds and denominators",
        },
        "digest_scheme": {
            "plan_content_digest": (
                "plan_sha256: canonical JSON over the plan excluding plan_sha256 "
                "and generated_at_utc"
            ),
            "manifest_content_digest": (
                "manifest_sha256: canonical JSON over the manifest excluding manifest_sha256"
            ),
            "execution_identity_digest": (
                "execution_identity_sha256: canonical JSON over the manifest excluding "
                "manifest_sha256, execution_identity_sha256, and the release-mutable "
                "fields release.release_anchor_utc and "
                "release.live_released_operation_ids"
            ),
            "file_digest": (
                "program_sha256 and source_hashes are SHA-256 over file bytes, not "
                "canonical-content digests"
            ),
            "release_mutable_fields": list(RELEASE_MUTABLE_FIELDS),
            "reseal_after_release": True,
        },
    }
    if profile.family == "same_day_v2":
        manifest["timeouts"]["invocation_deadline_s"] = limits[
            "max_foreground_invocation_seconds"
        ]
        manifest["run_mode"] = copy.deepcopy(plan["run_mode"])
        manifest["run_policy"].update(
            {
                "foreground_same_day": True,
                "calendar_anchors": False,
                "not_before_windows": False,
                "scheduled_waits": False,
                "background_monitors": False,
                "automatic_repeat_blocks": False,
                "on_invocation_deadline": (
                    "stop_starting_cells_preserve_unstarted"
                ),
            }
        )
    manifest["execution_identity_sha256"] = manifest_execution_identity_sha256(manifest)
    manifest["manifest_sha256"] = manifest_content_sha256(manifest)
    return manifest


def _program_from_plan(plan: dict[str, Any]) -> SessionProgram:
    """Minimal rehydration used only for the envelope calculation."""
    operations = tuple(
        Operation(
            operation_id=cell["operation_id"],
            phase=cell["phase"],
            scenario=cell["scenario"],
            concurrent_sessions=cell["concurrent_sessions"],
            sessions=cell["session_count"],
            model_calls_per_session=cell["model_calls_per_session"],
            max_inference_attempts=cell["max_inference_attempts"],
            window=cell.get("window"),
            start_offset_hours=cell.get("start_offset_hours"),
            requested_input_tokens=cell.get("requested_input_tokens"),
            sentinel_positions=tuple(cell.get("sentinel_positions") or ()),
            replicates_per_position=cell.get("replicates_per_position"),
        )
        for cell in plan["operations"]
    )
    return SessionProgram(
        source_path=Path("<plan>"),
        source_sha256=plan["program_sha256"],
        program_id=plan["program_id"],
        directive_id=plan["directive_id"],
        state=plan["release"]["state"],
        target=plan.get("target"),
        limits=plan["limits"],
        operations=operations,
        live_released_operation_ids=(),
        raw={},
        family=profile_for_plan(plan).family,
    )


PLAN_OPERATION_REQUIRED = (
    "operation_id",
    "phase",
    "scenario",
    "concurrent_sessions",
    "sessions",
    "model_calls_per_session",
    "max_inference_attempts",
    "window",
    "requested_input_tokens",
    "namespace",
    "order_seed",
    "session_count",
    "session_order",
    "turn_order",
    "template_mapping",
    "cache_treatment",
    "output_path",
    "context_fixtures",
)

MANIFEST_OPERATION_REQUIRED = (
    "operation_id",
    "workload_type",
    "phase",
    "scenario",
    "window",
    "concurrent_sessions",
    "sessions",
    "model_calls_per_session",
    "max_inference_attempts",
    "requested_input_tokens",
    "output_path",
    "argv",
    "timeouts",
    "session_max_input_tokens",
    "max_output_tokens",
    "cache_treatment",
    "session_ids",
    "template_mapping",
)

_SHA256_HEX = set("0123456789abcdef")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SessionProgramError(message)


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in _SHA256_HEX for character in value.lower())
    )


def _require_sha256(value: object, name: str) -> None:
    _require(_is_sha256(value), f"{name} must be a 64-character SHA-256 hex digest")


def _validate_limits(
    limits: object, context: str, profile: ProgramProfile
) -> dict[str, Any]:
    _require(isinstance(limits, dict), f"{context} limits must be an object")
    assert isinstance(limits, dict)
    for key in profile.limit_keys:
        _require(key in limits, f"{context} limits missing `{key}`")
    for key in (
        "max_total_new_inference_attempts",
        "max_concurrent_sessions",
        "max_total_inflight_model_calls",
        "max_completion_tokens_per_call",
        "session_input_tokens_initial_target",
        "session_input_tokens_max",
        "context_input_tokens_max",
    ):
        _positive_int(limits[key], f"{context}.{key}")
    for key in ("request_timeout_s", "cell_process_deadline_s"):
        _finite_positive(limits[key], f"{context}.{key}")
    if profile.family == "same_day_v2":
        _finite_positive(
            limits["max_foreground_invocation_seconds"],
            f"{context}.max_foreground_invocation_seconds",
        )
        _require(
            limits["max_total_new_inference_attempts"] == SAME_DAY_MATRIX_ATTEMPTS,
            f"{context}: the same-day matrix is exactly {SAME_DAY_MATRIX_ATTEMPTS} calls",
        )
        _require(
            float(limits["cell_process_deadline_s"]) == SAME_DAY_CELL_DEADLINE_S,
            f"{context}: the same-day cell deadline must be {SAME_DAY_CELL_DEADLINE_S:.0f}s",
        )
        _require(
            float(limits["max_foreground_invocation_seconds"])
            == SAME_DAY_INVOCATION_DEADLINE_S,
            f"{context}: the same-day invocation deadline must be "
            f"{SAME_DAY_INVOCATION_DEADLINE_S:.0f}s",
        )
        _require(
            float(limits["request_timeout_s"]) == 240.0,
            f"{context}: request timeout must remain 240s",
        )
        _require(
            int(limits["max_completion_tokens_per_call"]) == 8192,
            f"{context}: output cap must remain 8192 tokens",
        )
        _require(
            int(limits["session_input_tokens_max"]) == 32768,
            f"{context}: session input maximum must remain 32768 tokens",
        )
        _require(
            int(limits["context_input_tokens_max"]) == 131072,
            f"{context}: context input maximum must remain 131072 tokens",
        )
    _require(
        _positive_int(limits["max_concurrent_sessions"], "max_concurrent_sessions") <= 4,
        "max_concurrent_sessions cannot exceed 4 for this program",
    )
    _require(
        limits["max_total_inflight_model_calls"] == limits["max_concurrent_sessions"],
        "max_total_inflight_model_calls must equal max_concurrent_sessions",
    )
    _require(limits["automatic_inference_retries"] == 0, "automatic retries must be zero")
    _require(limits["replacement_trials"] == 0, "replacement trials must be zero")
    _require(
        limits["unlisted_warmup_or_readiness_inference"] == 0,
        "hidden warmup/readiness inference must be zero",
    )
    return limits


def _validate_run_mode(
    run_mode: object, limits: dict[str, Any], context: str
) -> dict[str, Any]:
    """Require the calendar-free same-day execution policy."""
    _require(isinstance(run_mode, dict), f"{context} run_mode must be an object")
    assert isinstance(run_mode, dict)
    _require(
        run_mode.get("kind") == SAME_DAY_RUN_MODE,
        f"{context} run_mode.kind must be `{SAME_DAY_RUN_MODE}`",
    )
    for key in (
        "calendar_anchors",
        "not_before_windows",
        "scheduled_waits",
        "background_monitors",
        "automatic_repeat_blocks",
    ):
        _require(
            run_mode.get(key) is False,
            f"{context} run_mode.{key} must be false for a same-day invocation",
        )
    _require(
        float(run_mode.get("invocation_deadline_s") or 0.0)
        == float(limits["max_foreground_invocation_seconds"]),
        f"{context} run_mode invocation deadline differs from the program limit",
    )
    _require(
        float(run_mode.get("cell_deadline_s") or 0.0)
        == float(limits["cell_process_deadline_s"]),
        f"{context} run_mode cell deadline differs from the program limit",
    )
    return run_mode


def validate_plan_document(plan: dict[str, Any]) -> None:
    """Validate a frozen plan from its content, not from its declared digest."""
    _require(isinstance(plan, dict), "plan must be a JSON object")
    profile = profile_for_plan_schema(plan.get("schema_version"))
    _require_sha256(plan.get("plan_sha256"), "plan.plan_sha256")
    _require(
        plan["plan_sha256"] == plan_content_sha256(plan),
        "plan content does not match its declared plan_sha256",
    )
    _require(plan.get("program_id") == profile.program_id, "plan program_id drift")
    _require(plan.get("directive_id") == profile.directive_id, "plan directive_id drift")
    _require(
        plan.get("workload_type") == profile.workload_type,
        "plan workload_type drift",
    )
    _require(isinstance(plan.get("plan_id"), str) and plan["plan_id"], "plan_id missing")
    _require(
        isinstance(plan.get("program_path"), str) and plan["program_path"],
        "plan program_path missing",
    )
    limits = _validate_limits(plan.get("limits"), "plan", profile)
    operations = plan.get("operations")
    _require(isinstance(operations, list), "plan operations must be a list")
    assert isinstance(operations, list)
    _require(
        len(operations) == profile.cells,
        f"plan must contain exactly {profile.cells} operations",
    )
    source_hashes = plan.get("source_hashes")
    _require(isinstance(source_hashes, dict), "plan source_hashes must be an object")
    assert isinstance(source_hashes, dict)
    _require(
        set(source_hashes) == set(SOURCE_FILES),
        "plan source_hashes must cover exactly the frozen source files",
    )
    for relative, digest in source_hashes.items():
        _require_sha256(digest, f"plan.source_hashes[{relative}]")
    _require_sha256(plan.get("program_sha256"), "plan.program_sha256")

    workloads = plan.get("workloads")
    _require(isinstance(workloads, dict), "plan workloads must be an object")
    templates = workloads.get("templates") if isinstance(workloads, dict) else None
    _require(
        isinstance(templates, dict) and set(templates) == {"coding", "document", "transcript"},
        "plan workloads.templates must describe coding, document and transcript",
    )
    for name, descriptor in templates.items():
        _require(isinstance(descriptor, dict), f"workload descriptor `{name}` must be an object")
        _require_sha256(descriptor.get("prefix_sha256"), f"workloads.{name}.prefix_sha256")
        _require(
            isinstance(descriptor.get("task_sha256"), list)
            and len(descriptor["task_sha256"]) == 4,
            f"workloads.{name}.task_sha256 must cover four tasks",
        )

    seen: set[str] = set()
    namespaces: set[str] = set()
    attempts_total = 0
    sessions_total = 0
    phase_counts = {phase: 0 for phase in profile.phases}
    for index, operation in enumerate(operations):
        context = f"plan.operations[{index}]"
        _require(isinstance(operation, dict), f"{context} must be an object")
        assert isinstance(operation, dict)
        for key in PLAN_OPERATION_REQUIRED:
            _require(key in operation, f"{context} missing `{key}`")
        operation_id = operation["operation_id"]
        _require(isinstance(operation_id, str) and operation_id, f"{context} operation_id")
        _require(operation_id not in seen, f"plan duplicate operation_id `{operation_id}`")
        seen.add(operation_id)
        namespace = operation["namespace"]
        _require(
            isinstance(namespace, str) and namespace,
            f"{context} namespace missing",
        )
        _require(namespace not in namespaces, f"plan duplicate cell namespace `{namespace}`")
        namespaces.add(namespace)
        scenario = operation["scenario"]
        _require(scenario in SCENARIOS, f"{context} unknown scenario `{scenario}`")
        phase = operation["phase"]
        _require(phase in phase_counts, f"{context} unsupported phase `{phase}`")
        phase_counts[phase] += 1
        concurrency = _positive_int(operation["concurrent_sessions"], f"{context}.concurrency")
        _require(
            concurrency <= limits["max_concurrent_sessions"],
            f"{context} concurrency exceeds the limit",
        )
        attempts = _positive_int(
            operation["max_inference_attempts"], f"{context}.max_inference_attempts"
        )
        attempts_total += attempts
        _require(
            operation["output_path"] == f"cells/{operation_id}",
            f"{context} output_path drift",
        )
        _require(
            isinstance(operation["session_order"], list)
            and isinstance(operation["turn_order"], list),
            f"{context} session/turn order must be lists",
        )
        _require(
            isinstance(operation["cache_treatment"], dict),
            f"{context} cache_treatment must be an object",
        )
        if profile.family == "same_day_v2":
            _require(
                operation.get("window") is None
                and operation.get("start_offset_hours") is None,
                f"{context} same-day plan cannot carry a calendar offset",
            )
        if scenario == "context_sentinels":
            _require(
                operation["session_count"] == 0,
                f"{context} context session_count must be zero",
            )
            _require(
                operation["sessions"] == [],
                f"{context} context sessions must be an empty list",
            )
            requested = _positive_int(
                operation["requested_input_tokens"], f"{context}.requested_input_tokens"
            )
            _require(
                requested in CONTEXT_SIZES,
                f"{context} requested_input_tokens is not a released context size",
            )
            _require(
                requested <= limits["context_input_tokens_max"],
                f"{context} requested size exceeds the context maximum",
            )
            positions = operation.get("sentinel_positions")
            replicates = operation.get("replicates_per_position")
            _require(
                positions == list(SENTINEL_POSITIONS),
                f"{context} sentinel_positions drift",
            )
            _require(
                attempts == len(SENTINEL_POSITIONS) * int(replicates or 0),
                f"{context} attempt count does not equal positions x replicates",
            )
            fixtures = operation["context_fixtures"]
            _require(
                isinstance(fixtures, list) and len(fixtures) == attempts,
                f"{context} context fixtures do not match the attempt count",
            )
            for fixture in fixtures:
                _require(
                    isinstance(fixture, dict),
                    f"{context} context fixture must be an object",
                )
                _require_sha256(
                    fixture.get("prompt_sha256"),
                    f"{context}.context_fixture.prompt_sha256",
                )
                _require(
                    fixture.get("client_estimated_input_tokens", 0) <= requested,
                    f"{context} context fixture exceeds the requested full-request budget",
                )
        else:
            sessions = _positive_int(operation["session_count"], f"{context}.session_count")
            calls = _positive_int(
                operation["model_calls_per_session"], f"{context}.model_calls_per_session"
            )
            sessions_total += sessions
            _require(
                isinstance(operation["sessions"], list)
                and len(operation["sessions"]) == sessions,
                f"{context} sessions list does not match session_count",
            )
            _require(
                attempts == sessions * calls,
                f"{context} attempt count does not equal sessions x calls",
            )
            _require(calls == 4, f"{context} every session scenario uses four calls")
            _require(sessions == 5, f"{context} every session scenario uses five sessions")
            _require(
                len(operation["session_order"]) == sessions,
                f"{context} session_order length drift",
            )
            _require(
                len(operation["turn_order"]) == attempts,
                f"{context} turn_order length drift",
            )
            session_ids = [session.get("session_id") for session in operation.get("sessions", [])]
            _require(
                len(session_ids) == sessions and len(set(session_ids)) == sessions,
                f"{context} session identities are missing or duplicated",
            )
            for session in operation["sessions"]:
                labels = session.get("turn_namespace_labels")
                _require(
                    isinstance(labels, list) and len(labels) == calls,
                    f"{context} session `{session.get('session_id')}` has no frozen turn labels",
                )
                for label in labels:
                    cache_namespace_marker(label)

    totals = plan.get("totals")
    _require(isinstance(totals, dict), "plan totals must be an object")
    assert isinstance(totals, dict)
    _require(
        totals.get("cells") == profile.cells,
        f"plan totals.cells must be {profile.cells}",
    )
    _require(
        totals.get("inference_attempts") == attempts_total,
        "plan totals.inference_attempts does not match the operations",
    )
    _require(
        attempts_total == limits["max_total_new_inference_attempts"],
        "plan attempt total does not match the finite limit",
    )
    _require(
        totals.get("sessions") == sessions_total,
        "plan totals.sessions does not match the operations",
    )
    _require(
        totals.get("phases") == phase_counts,
        "plan totals.phases does not match the operations",
    )
    _validate_matrix(
        [
            Operation(
                operation_id=operation["operation_id"],
                phase=operation["phase"],
                scenario=operation["scenario"],
                concurrent_sessions=operation["concurrent_sessions"],
                sessions=operation["session_count"],
                model_calls_per_session=operation["model_calls_per_session"],
                max_inference_attempts=operation["max_inference_attempts"],
                window=operation.get("window"),
                start_offset_hours=operation.get("start_offset_hours"),
                requested_input_tokens=operation.get("requested_input_tokens"),
                sentinel_positions=tuple(operation.get("sentinel_positions") or ()),
                replicates_per_position=operation.get("replicates_per_position"),
            )
            for operation in operations
        ],
        limits,
        profile,
    )
    if profile.family == "same_day_v2":
        _validate_run_mode(plan.get("run_mode"), limits, "plan")
    else:
        _require("run_mode" not in plan, "the temporal plan cannot carry a same-day run_mode")
    schedule = plan.get("schedule")
    _require(isinstance(schedule, list), "plan schedule must be a list")
    assert isinstance(schedule, list)
    scheduled = {
        item.get("operation_id")
        for item in schedule
        if isinstance(item, dict) and item.get("operation_id")
    }
    _require(scheduled == seen, "plan schedule does not cover every operation exactly once")
    _validate_schedule(schedule, limits, profile)


def validate_manifest_document(
    manifest: dict[str, Any], *, require_unreleased: bool = False
) -> None:
    """One validation contract for prepared and manager-released manifests.

    The manifest self-digest covers the whole document. The execution identity
    digest excludes only the documented release fields, so a live release can
    change those and re-seal without silently changing the measured program.
    """
    _require(isinstance(manifest, dict), "manifest must be a JSON object")
    profile = profile_for_manifest_schema(manifest.get("schema_version"))
    _require_sha256(manifest.get("manifest_sha256"), "manifest.manifest_sha256")
    _require(
        manifest["manifest_sha256"] == manifest_content_sha256(manifest),
        "manifest hash is stale or the document was edited after generation",
    )
    _require_sha256(
        manifest.get("execution_identity_sha256"),
        "manifest.execution_identity_sha256",
    )
    _require(
        manifest["execution_identity_sha256"]
        == manifest_execution_identity_sha256(manifest),
        "manifest execution identity is stale",
    )
    _require(manifest.get("program_id") == profile.program_id, "manifest program_id drift")
    _require(
        manifest.get("directive_id") == profile.directive_id,
        "manifest directive_id drift",
    )
    _require(
        manifest.get("workload_type") == profile.workload_type,
        "manifest workload_type drift",
    )
    _require_sha256(manifest.get("plan_sha256"), "manifest.plan_sha256")
    _require_sha256(manifest.get("program_sha256"), "manifest.program_sha256")
    _require(
        isinstance(manifest.get("program_path"), str) and manifest["program_path"],
        "manifest program_path missing",
    )
    limits = _validate_limits(manifest.get("limits"), "manifest", profile)
    source_hashes = manifest.get("source_hashes")
    _require(isinstance(source_hashes, dict), "manifest source_hashes must be an object")
    assert isinstance(source_hashes, dict)
    _require(
        set(source_hashes) == set(SOURCE_FILES),
        "manifest source_hashes must cover exactly the frozen source files",
    )
    for relative, digest in source_hashes.items():
        _require_sha256(digest, f"manifest.source_hashes[{relative}]")
    workloads = manifest.get("workloads")
    _require(isinstance(workloads, dict), "manifest workloads must be an object")
    templates = workloads.get("templates") if isinstance(workloads, dict) else None
    _require(
        isinstance(templates, dict) and set(templates) == {"coding", "document", "transcript"},
        "manifest workloads.templates must describe coding, document and transcript",
    )
    digest_scheme = manifest.get("digest_scheme")
    _require(isinstance(digest_scheme, dict), "manifest digest_scheme missing")
    assert isinstance(digest_scheme, dict)
    _require(
        digest_scheme.get("release_mutable_fields") == list(RELEASE_MUTABLE_FIELDS),
        "manifest digest_scheme release fields drift",
    )
    if profile.family == "same_day_v2":
        _validate_run_mode(manifest.get("run_mode"), limits, "manifest")
        _require(
            isinstance(manifest.get("timeouts"), dict),
            "manifest timeouts must be an object",
        )
        assert isinstance(manifest["timeouts"], dict)
        _require(
            float(manifest["timeouts"].get("invocation_deadline_s") or 0.0)
            == float(limits["max_foreground_invocation_seconds"]),
            "manifest timeouts.invocation_deadline_s differs from the program limit",
        )
    else:
        _require(
            "run_mode" not in manifest,
            "the temporal manifest cannot carry a same-day run_mode",
        )

    operations = manifest.get("operations")
    _require(isinstance(operations, list), "manifest operations must be a list")
    assert isinstance(operations, list)
    _require(
        len(operations) == profile.cells,
        f"manifest must contain exactly {profile.cells} operations",
    )
    seen: set[str] = set()
    attempts_total = 0
    for index, operation in enumerate(operations):
        context = f"manifest.operations[{index}]"
        _require(isinstance(operation, dict), f"{context} must be an object")
        assert isinstance(operation, dict)
        for key in MANIFEST_OPERATION_REQUIRED:
            _require(key in operation, f"{context} missing `{key}`")
        operation_id = operation["operation_id"]
        _require(isinstance(operation_id, str) and operation_id, f"{context} operation_id")
        _require(
            operation_id not in seen,
            f"manifest duplicate operation_id `{operation_id}`",
        )
        seen.add(operation_id)
        _require(
            operation["workload_type"] == profile.workload_type,
            f"{context} workload_type drift",
        )
        _require(
            operation["scenario"] in SCENARIOS,
            f"{context} unknown scenario `{operation['scenario']}`",
        )
        _require(
            operation["phase"] in profile.phases,
            f"{context} unsupported phase",
        )
        concurrency = _positive_int(operation["concurrent_sessions"], f"{context}.concurrency")
        _require(
            concurrency <= limits["max_concurrent_sessions"],
            f"{context} concurrency exceeds the manifest limit",
        )
        attempts = _positive_int(
            operation["max_inference_attempts"], f"{context}.max_inference_attempts"
        )
        attempts_total += attempts
        _require(
            operation["output_path"] == f"cells/{operation_id}",
            f"{context} output_path drift",
        )
        timeouts = operation["timeouts"]
        _require(isinstance(timeouts, dict), f"{context} timeouts must be an object")
        assert isinstance(timeouts, dict)
        _require(
            timeouts.get("request_timeout_s") == limits["request_timeout_s"],
            f"{context}: request timeout differs from the program limit",
        )
        _require(
            timeouts.get("cell_process_deadline_s")
            == limits["cell_process_deadline_s"],
            f"{context}: cell deadline differs from the program limit",
        )
        _require(
            operation["max_output_tokens"] == limits["max_completion_tokens_per_call"],
            f"{context}: output cap differs from the program limit",
        )
        session_bound = _positive_int(
            operation["session_max_input_tokens"], f"{context}.session_max_input_tokens"
        )
        _require(
            session_bound <= limits["context_input_tokens_max"],
            f"{context}: session input bound exceeds the context maximum",
        )
        argv = operation["argv"]
        _require(isinstance(argv, list) and argv, f"{context} argv must be a non-empty list")
        _require(
            "--operation" in argv
            and argv[argv.index("--operation") + 1] == operation_id,
            f"{context} argv does not name the operation",
        )
        _require(
            "--max-requests" in argv
            and argv[argv.index("--max-requests") + 1] == str(attempts),
            f"{context} argv request cap does not equal the operation attempts",
        )
        template_mapping = operation["template_mapping"]
        _require(
            isinstance(template_mapping, dict)
            and set(template_mapping.values()) <= {"coding", "document", "transcript"},
            f"{context} template_mapping drift",
        )
        session_ids = operation["session_ids"]
        _require(isinstance(session_ids, list), f"{context} session_ids must be a list")
        _require(
            len(session_ids) == len(set(session_ids)),
            f"{context} duplicate session identity",
        )
        if operation["scenario"] == "context_sentinels":
            _require(operation["sessions"] == 0, f"{context} context sessions must be zero")
            _require(
                operation["requested_input_tokens"] in CONTEXT_SIZES,
                f"{context} requested size is not a released context size",
            )
            _require(
                attempts == 6,
                f"{context} context cell must contain six planned calls",
            )
            _require(session_ids == [], f"{context} context cell must not declare sessions")
        else:
            sessions = _positive_int(operation["sessions"], f"{context}.sessions")
            calls = _positive_int(
                operation["model_calls_per_session"], f"{context}.model_calls_per_session"
            )
            _require(
                attempts == sessions * calls,
                f"{context} attempt count does not equal sessions x calls",
            )
            _require(
                len(session_ids) == sessions,
                f"{context} session_ids do not match the session count",
            )

    totals = manifest.get("totals")
    _require(isinstance(totals, dict), "manifest totals must be an object")
    assert isinstance(totals, dict)
    _require(
        totals.get("cells") == profile.cells,
        f"manifest totals.cells must be {profile.cells}",
    )
    _require(
        totals.get("max_inference_attempts") == attempts_total,
        "manifest totals.max_inference_attempts does not match the operations",
    )
    _require(
        attempts_total == limits["max_total_new_inference_attempts"],
        f"manifest attempts {attempts_total} != limit "
        f"{limits['max_total_new_inference_attempts']}",
    )

    release = manifest.get("release")
    _require(isinstance(release, dict), "manifest release must be an object")
    assert isinstance(release, dict)
    released = release.get("live_released_operation_ids")
    _require(isinstance(released, list), "release.live_released_operation_ids must be a list")
    _require(
        len(released) == len(set(released)),
        "release.live_released_operation_ids contains duplicates",
    )
    _require(
        set(released) <= seen,
        "release.live_released_operation_ids names an unknown operation",
    )
    if require_unreleased:
        _require(
            not released,
            "this manifest is an offline preflight; it must not release live operations",
        )
    anchor = release.get("release_anchor_utc")
    _require(
        anchor is None or isinstance(anchor, str),
        "release.release_anchor_utc must be a string or null",
    )
    if profile.family == "same_day_v2":
        _require(
            anchor is None,
            "the same-day path cannot carry a calendar release anchor",
        )

    schedule = manifest.get("schedule")
    _require(isinstance(schedule, list), "manifest schedule must be a list")
    assert isinstance(schedule, list)
    scheduled = {
        item.get("operation_id")
        for item in schedule
        if isinstance(item, dict) and item.get("operation_id")
    }
    _require(scheduled == seen, "manifest schedule does not cover every operation")


def validate_manifest(
    manifest: dict[str, Any], *, require_unreleased: bool = True
) -> None:
    """Backward-compatible entry point; one core contract, one option."""
    validate_manifest_document(manifest, require_unreleased=require_unreleased)


def verify_manifest_against_plan(manifest: dict[str, Any], plan: dict[str, Any]) -> None:
    """Recompute the plan digest and require the manifest to match its content."""
    _require(
        plan.get("plan_sha256") == plan_content_sha256(plan),
        "plan content does not match its declared plan_sha256",
    )
    _require(
        manifest.get("plan_sha256") == plan["plan_sha256"],
        "plan file does not match the manifest's plan hash",
    )
    for key in ("plan_id", "program_id", "directive_id", "workload_type"):
        _require(
            manifest.get(key) == plan.get(key),
            f"manifest {key} does not match the plan",
        )
    _require(
        manifest.get("limits") == plan.get("limits"),
        "manifest limits do not match the plan",
    )
    _require(
        manifest.get("run_mode") == plan.get("run_mode"),
        "manifest run_mode does not match the plan",
    )
    _require(
        manifest.get("source_hashes") == plan.get("source_hashes"),
        "manifest source hashes do not match the plan",
    )
    _require(
        manifest.get("workloads") == plan.get("workloads"),
        "manifest workload descriptors do not match the plan",
    )
    _require(
        manifest.get("program_sha256") == plan.get("program_sha256"),
        "manifest program hash does not match the plan",
    )
    _require(
        manifest.get("program_path") == plan.get("program_path"),
        "manifest program path does not match the plan",
    )
    plan_operations = plan.get("operations") or []
    manifest_operations = manifest.get("operations") or []
    _require(
        len(plan_operations) == len(manifest_operations),
        "manifest operation count does not match the plan",
    )
    for index, (plan_cell, manifest_cell) in enumerate(
        zip(plan_operations, manifest_operations, strict=True)
    ):
        _require(
            plan_cell.get("operation_id") == manifest_cell.get("operation_id"),
            f"operation order drift at index {index}",
        )
        for key in (
            "phase",
            "scenario",
            "window",
            "start_offset_hours",
            "concurrent_sessions",
            "model_calls_per_session",
            "max_inference_attempts",
            "requested_input_tokens",
            "output_path",
        ):
            _require(
                plan_cell.get(key) == manifest_cell.get(key),
                f"{manifest_cell.get('operation_id')}: plan/manifest `{key}` drift",
            )
        _require(
            plan_cell.get("session_count") == manifest_cell.get("sessions"),
            f"{manifest_cell.get('operation_id')}: session count drift",
        )
        _require(
            plan_cell.get("template_mapping") == manifest_cell.get("template_mapping"),
            f"{manifest_cell.get('operation_id')}: template mapping drift",
        )
        _require(
            plan_cell.get("cache_treatment") == manifest_cell.get("cache_treatment"),
            f"{manifest_cell.get('operation_id')}: cache treatment drift",
        )
    _require(
        manifest.get("schedule") == plan.get("schedule"),
        "manifest schedule does not match the plan",
    )
    plan_totals = plan.get("totals") or {}
    manifest_totals = manifest.get("totals") or {}
    for key in ("cells", "inference_attempts", "sessions", "model_calls", "phases"):
        _require(
            manifest_totals.get(key) == plan_totals.get(key),
            f"manifest totals.{key} does not match the plan",
        )
    _require(
        manifest_totals.get("max_inference_attempts")
        == plan_totals.get("inference_attempts"),
        "manifest totals.max_inference_attempts does not match the plan",
    )
    rates = manifest.get("operational_rates")
    _require(isinstance(rates, dict), "manifest operational_rates must be an object")
    assert isinstance(rates, dict)
    for key in (
        "prompt_per_million_tokens",
        "completion_per_million_tokens",
        "safety_multiplier",
    ):
        value = rates.get(key)
        _require(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value >= 0,
            f"manifest operational_rates.{key} must be non-negative",
        )
    expected_envelope = _conservative_program_usd(_program_from_plan(plan), rates)
    _require(
        abs(
            float(manifest_totals.get("operational_usd_envelope", -1))
            - float(expected_envelope)
        )
        <= 1e-9,
        "manifest operational_usd_envelope does not match the frozen rates and plan",
    )


def verify_workloads(manifest: dict[str, Any]) -> None:
    """Rebuild the deterministic templates and require exact descriptor equality."""
    templates = build_templates()
    rebuilt = {
        name: template.to_descriptor() for name, template in sorted(templates.items())
    }
    workloads = manifest.get("workloads") or {}
    _require(
        workloads.get("templates") == rebuilt,
        "workload template descriptors do not match the current deterministic fixtures",
    )
    _require(
        workloads.get("chars_per_token_estimate") == CHARS_PER_TOKEN,
        "workload token estimate drift",
    )
    isolation = workloads.get("cache_isolation") or {}
    _require(
        isolation.get("marker_width_chars") == CACHE_NAMESPACE_WIDTH,
        "cache isolation marker width drift",
    )
    _require(
        isolation.get("headers_are_not_proof") is True,
        "cache isolation must not rely on headers as proof",
    )


def verify_context_fixtures(plan: dict[str, Any]) -> None:
    """Rebuild every context fixture and compare it to the frozen descriptor."""
    for cell in plan.get("operations") or []:
        if cell.get("scenario") != "context_sentinels":
            continue
        requested = int(cell["requested_input_tokens"])
        namespace = str(cell["namespace"])
        expected: list[dict[str, Any]] = []
        for position_index, position in enumerate(cell["sentinel_positions"]):
            for replicate in range(1, int(cell["replicates_per_position"]) + 1):
                expected.append(
                    context_fixture(
                        requested_input_tokens=requested,
                        position=position,
                        replicate=replicate,
                        seed=int(cell["order_seed"]) + 97 * position_index,
                        namespace=namespace,
                    )
                )
        frozen = cell.get("context_fixtures") or []
        _require(
            len(frozen) == len(expected),
            f"{cell['operation_id']}: context fixture count drift",
        )
        for index, (fixture, rebuilt) in enumerate(zip(frozen, expected, strict=True)):
            comparable = {
                key: value for key, value in rebuilt.items() if key != "prompt"
            }
            _require(
                fixture == comparable,
                f"{cell['operation_id']}: context fixture {index} does not match "
                "the current deterministic bytes",
            )


def verify_program_identity(
    manifest: dict[str, Any], plan: dict[str, Any], repo_root: Path
) -> None:
    """Re-read the program definition and require frozen identity equality."""
    program_path = Path(str(plan.get("program_path")))
    if not program_path.is_absolute():
        program_path = repo_root / program_path
    _require(program_path.is_file(), f"program definition is missing: {program_path}")
    program = load_program(program_path)
    _require(
        program.source_sha256 == manifest.get("program_sha256"),
        "program file hash does not match the manifest",
    )
    _require(
        program.program_id == manifest.get("program_id"),
        "program id drift",
    )
    _require(
        program.directive_id == manifest.get("directive_id"),
        "program directive drift",
    )
    _require(
        program.limits == manifest.get("limits"),
        "program limits do not match the manifest",
    )
    plan_operations = {
        operation["operation_id"]: operation for operation in plan.get("operations") or []
    }
    _require(
        len(program.operations) == len(plan_operations),
        "program operation count drift",
    )
    for operation in program.operations:
        cell = plan_operations.get(operation.operation_id)
        _require(cell is not None, f"program operation `{operation.operation_id}` missing from plan")
        assert cell is not None
        _require(
            cell.get("scenario") == operation.scenario,
            f"{operation.operation_id}: program scenario drift",
        )
        _require(
            cell.get("max_inference_attempts") == operation.max_inference_attempts,
            f"{operation.operation_id}: program attempt count drift",
        )
        _require(
            cell.get("concurrent_sessions") == operation.concurrent_sessions,
            f"{operation.operation_id}: program concurrency drift",
        )


def verify_runtime_sources(
    manifest: dict[str, Any], repo_root: Path | None = None
) -> None:
    """Compare the recorded source file SHA-256 values with the runtime files."""
    root = repo_root or Path(__file__).resolve().parents[2]
    recorded = manifest.get("source_hashes") or {}
    _require(
        set(recorded) == set(SOURCE_FILES),
        "source hash inventory is incomplete",
    )
    for relative in SOURCE_FILES:
        path = root / relative
        _require(path.is_file(), f"frozen source file is missing: {relative}")
        actual = sha256_bytes(path.read_bytes())
        _require(
            actual == recorded[relative],
            f"frozen source drift: `{relative}` has {actual}, manifest records "
            f"{recorded[relative]}",
        )


def verify_prepared_manifest(
    manifest: dict[str, Any],
    plan: dict[str, Any],
    *,
    repo_root: Path | None = None,
    require_unreleased: bool = False,
    check_sources: bool = True,
) -> None:
    """Single validation path used by the CLI and the preflight builder."""
    root = repo_root or Path(__file__).resolve().parents[2]
    validate_plan_document(plan)
    validate_manifest_document(manifest, require_unreleased=require_unreleased)
    verify_manifest_against_plan(manifest, plan)
    verify_workloads(manifest)
    verify_context_fixtures(plan)
    verify_program_identity(manifest, plan, root)
    if check_sources:
        verify_runtime_sources(manifest, root)


def load_target_metadata(
    path: Path | str, *, note: str | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Bind a target from committed, credential-free metadata.

    The metadata file is the same binding source the checked preflight builder
    uses. It carries no credential value: only the environment-variable name.
    """
    source = Path(path)
    if not source.is_file():
        raise SessionProgramError(f"target metadata does not exist: {source}")
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SessionProgramError(
            f"could not read target metadata `{source}`: {error}"
        ) from error
    if not isinstance(document, dict):
        raise SessionProgramError("target metadata must be a JSON object")
    required = (
        "deployment_key",
        "model_id",
        "base_url",
        "protocol",
        "region",
        "credential_env_name",
    )
    missing = [key for key in required if not document.get(key)]
    if missing:
        raise SessionProgramError(
            f"target metadata missing: {', '.join(missing)}"
        )
    pricing = document.get("pricing") or {}
    prompt = pricing.get("prompt_per_million_tokens")
    completion = pricing.get("completion_per_million_tokens")
    if (
        isinstance(prompt, bool)
        or not isinstance(prompt, (int, float))
        or prompt < 0
        or isinstance(completion, bool)
        or not isinstance(completion, (int, float))
        or completion < 0
    ):
        raise SessionProgramError(
            "target pricing needs non-negative prompt/completion rates"
        )
    rates = {
        "prompt_per_million_tokens": float(prompt),
        "completion_per_million_tokens": float(completion),
        "safety_multiplier": 1.5,
        "source": str(pricing.get("source") or "target metadata"),
        "effective_date": pricing.get("effective_date"),
        "pricing_verified": pricing.get("verified") is True,
    }
    target = {
        "deployment_key": document["deployment_key"],
        "model_id": document["model_id"],
        "base_url": document["base_url"],
        "protocol": document["protocol"],
        "region": document["region"],
        "credential_env_name": document["credential_env_name"],
        "model_revision": document.get("model_revision"),
        "pricing_verified": pricing.get("verified") is True,
        "bound": True,
        "binding_source": document.get("binding_source"),
        "target_metadata_sha256": sha256_bytes(source.read_bytes()),
        "target_metadata_path": str(source),
        "note": note
        or (
            "Target-bound preflight only. Live release remains manager-controlled; "
            "no credential value is stored in this manifest."
        ),
    }
    return target, rates


def write_json_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)
