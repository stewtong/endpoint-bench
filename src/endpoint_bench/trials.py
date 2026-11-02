from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

TRIAL_SCHEMA_VERSION = "0.1.0"


class TrialSchemaError(ValueError):
    """Raised when trial evidence is internally inconsistent."""


class LifecycleState(StrEnum):
    """Observation points in a trial's request lifecycle.

    Ordered from least to most complete. A trial's lifecycle is the furthest
    observation actually recorded, not an assumption about what happened after.
    """

    PLANNED = "planned"
    STARTED = "started"
    TRANSPORT_COMPLETED = "transport_completed"
    PROTOCOL_COMPLETED = "protocol_completed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class OutcomeState(StrEnum):
    """Terminal outcome. Separate from lifecycle by design."""

    SUCCESS = "success"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    SERVER_ERROR = "server_error"
    CLIENT_ERROR = "client_error"
    PROTOCOL_FAILURE = "protocol_failure"
    STREAM_INTERRUPTED = "stream_interrupted"
    CONTEXT_REJECTED = "context_rejected"
    OUTPUT_TRUNCATED = "output_truncated"
    BUDGET_EXCEEDED = "budget_exceeded"
    UNKNOWN = "unknown"


#: Outcomes that count as a successfully completed trial.
SUCCESS_OUTCOMES = frozenset({OutcomeState.SUCCESS})

#: Outcomes that represent a failure attributable to the endpoint or protocol.
ENDPOINT_FAILURE_OUTCOMES = frozenset(
    {
        OutcomeState.TIMEOUT,
        OutcomeState.RATE_LIMITED,
        OutcomeState.SERVER_ERROR,
        OutcomeState.PROTOCOL_FAILURE,
        OutcomeState.STREAM_INTERRUPTED,
        OutcomeState.CONTEXT_REJECTED,
        OutcomeState.OUTPUT_TRUNCATED,
    }
)

#: Who is responsible for a failed or unexpected outcome.
#:
#: `model` exists so that protocol-looking failures caused by model choice are not
#: reported as infrastructure faults. A capable model that declines to call a tool
#: is not a broken endpoint.
ATTRIBUTION_VALUES = ("provider", "model", "harness", "unknown")

#: Default tolerance for client-estimated versus provider-reported input tokens.
DEFAULT_TOKEN_TOLERANCE_PCT = 10.0

#: Workload kinds. Replay is never described as live agent execution.
WORKLOAD_SYNTHETIC_SINGLE_TURN = "synthetic_single_turn"
WORKLOAD_RECORDED_MULTI_TURN_REPLAY = "recorded_multi_turn_replay"
WORKLOAD_LIVE_TURN_CONFORMANCE = "live_turn_conformance"
WORKLOAD_FRAMEWORK_AGENT = "framework_agent_execution"


def utc_now() -> str:
    """UTC RFC 3339 timestamp with a trailing Z."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class HttpAttempt:
    """One HTTP request on the wire.

    Attempts are counted separately from trials because a retrying client can
    issue several attempts for one logical trial. AIPerf 0.13.0 does not retry
    inference requests, so under AIPerf this is normally one attempt per trial;
    the field exists so that assumption is observable rather than assumed.
    """

    attempt_index: int
    started_at: str | None = None
    ended_at: str | None = None
    http_status: int | None = None
    error_type: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_index": self.attempt_index,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "http_status": self.http_status,
            "error_type": self.error_type,
            "error_message": self.error_message,
        }


@dataclass(frozen=True)
class TokenAccounting:
    """Four distinct token measures that must never be collapsed into one.

    - `target`: the workload size the profile asked for.
    - `client_estimated`: what the client tokenizer counted in the serialized request.
    - `provider_input`: what the provider reported in `usage`.
    - `observed_output`: what the client counted in the response.

    A mismatch between `client_estimated` and `provider_input` means workload
    sizing is unverified, which blocks token-normalized claims. No correction
    factor is ever applied.
    """

    target_input_tokens: int | None = None
    client_estimated_input_tokens: int | None = None
    provider_input_tokens: int | None = None
    provider_output_tokens: int | None = None
    observed_output_tokens: int | None = None
    tolerance_pct: float = DEFAULT_TOKEN_TOLERANCE_PCT

    @property
    def discrepancy_pct(self) -> float | None:
        """Relative difference between client estimate and provider report."""
        client = self.client_estimated_input_tokens
        provider = self.provider_input_tokens
        if client is None or provider is None or client == 0:
            return None
        return abs(provider - client) / client * 100.0

    @property
    def sizing_verified(self) -> bool | None:
        """Whether workload sizing is corroborated within tolerance.

        `None` means unverifiable, which is never treated as a pass.
        """
        discrepancy = self.discrepancy_pct
        if discrepancy is None:
            return None
        return discrepancy <= self.tolerance_pct

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_input_tokens": self.target_input_tokens,
            "client_estimated_input_tokens": self.client_estimated_input_tokens,
            "provider_input_tokens": self.provider_input_tokens,
            "provider_output_tokens": self.provider_output_tokens,
            "observed_output_tokens": self.observed_output_tokens,
            "discrepancy_pct": self.discrepancy_pct,
            "tolerance_pct": self.tolerance_pct,
            "sizing_verified": self.sizing_verified,
        }


@dataclass
class Trial:
    """One logical benchmark trial with its lifecycle, outcome, and timing."""

    trial_id: str
    deployment_key: str
    profile_name: str
    outcome: OutcomeState = OutcomeState.UNKNOWN
    lifecycle_events: dict[str, str] = field(default_factory=dict)
    attempts: list[HttpAttempt] = field(default_factory=list)
    ttft_ms: float | None = None
    duration_ms: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    finish_reason: str | None = None
    outcome_detail: str | None = None
    notes: list[str] = field(default_factory=list)
    attribution: str = "unknown"
    token_accounting: TokenAccounting = field(default_factory=TokenAccounting)

    def observe(self, state: LifecycleState) -> None:
        """Record a lifecycle observation. First observation wins."""
        self.lifecycle_events.setdefault(state.value, utc_now())

    @property
    def lifecycle(self) -> LifecycleState:
        """Furthest lifecycle state actually observed."""
        observed = [LifecycleState(name) for name in self.lifecycle_events]
        if LifecycleState.SKIPPED in observed:
            return LifecycleState.SKIPPED
        if LifecycleState.CANCELLED in observed:
            return LifecycleState.CANCELLED
        for state in (
            LifecycleState.PROTOCOL_COMPLETED,
            LifecycleState.TRANSPORT_COMPLETED,
            LifecycleState.STARTED,
        ):
            if state in observed:
                return state
        return LifecycleState.PLANNED

    @property
    def http_attempts(self) -> int:
        return len(self.attempts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": TRIAL_SCHEMA_VERSION,
            "trial_id": self.trial_id,
            "deployment_key": self.deployment_key,
            "profile_name": self.profile_name,
            "lifecycle": self.lifecycle.value,
            "lifecycle_events": dict(sorted(self.lifecycle_events.items())),
            "outcome": self.outcome.value,
            "outcome_detail": self.outcome_detail,
            "http_attempts": self.http_attempts,
            "attempts": [attempt.to_dict() for attempt in self.attempts],
            "timing": {"ttft_ms": self.ttft_ms, "duration_ms": self.duration_ms},
            "usage": {
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "total_tokens": self.total_tokens,
            },
            "token_accounting": self.token_accounting.to_dict(),
            "attribution": self.attribution,
            "finish_reason": self.finish_reason,
            "notes": list(self.notes),
        }

    def validate(self) -> None:
        """Reject internally inconsistent trial evidence.

        These are the invariants the plan requires to hold before any trial is
        counted. A violation means the harness is wrong, not the endpoint.
        """
        lifecycle = self.lifecycle

        if self.outcome is OutcomeState.SUCCESS and lifecycle is not LifecycleState.PROTOCOL_COMPLETED:
            raise TrialSchemaError(
                f"trial `{self.trial_id}`: outcome `success` requires lifecycle "
                f"`protocol_completed`, observed `{lifecycle.value}`"
            )
        if lifecycle is LifecycleState.SKIPPED and self.http_attempts:
            raise TrialSchemaError(
                f"trial `{self.trial_id}`: skipped trial recorded {self.http_attempts} HTTP attempt(s)"
            )
        if lifecycle is LifecycleState.PLANNED and self.http_attempts:
            raise TrialSchemaError(
                f"trial `{self.trial_id}`: planned-only trial recorded HTTP attempts"
            )
        if lifecycle in (LifecycleState.STARTED, LifecycleState.TRANSPORT_COMPLETED) and not self.http_attempts:
            raise TrialSchemaError(
                f"trial `{self.trial_id}`: lifecycle `{lifecycle.value}` requires at least one HTTP attempt"
            )
        for name in ("ttft_ms", "duration_ms"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise TrialSchemaError(f"trial `{self.trial_id}`: `{name}` cannot be negative")
        if self.attribution not in ATTRIBUTION_VALUES:
            raise TrialSchemaError(
                f"trial `{self.trial_id}`: `attribution` `{self.attribution}` is not one of "
                f"{', '.join(ATTRIBUTION_VALUES)}"
            )


@dataclass(frozen=True)
class RunCounters:
    """Separate counters for each lifecycle stage and for HTTP attempts.

    Percentiles are never computed from these; they are accounting only.
    """

    planned: int
    started: int
    transport_completed: int
    protocol_completed: int
    succeeded: int
    failed: int
    skipped: int
    cancelled: int
    http_attempts: int

    def to_dict(self) -> dict[str, int]:
        return {
            "planned_trials": self.planned,
            "started_trials": self.started,
            "transport_completed_trials": self.transport_completed,
            "protocol_completed_trials": self.protocol_completed,
            "successful_trials": self.succeeded,
            "failed_trials": self.failed,
            "skipped_trials": self.skipped,
            "cancelled_trials": self.cancelled,
            "http_attempts": self.http_attempts,
        }


def count_trials(trials: list[Trial]) -> RunCounters:
    """Derive counters from trial evidence, enforcing the nesting invariants.

    `failed` is every started trial that neither succeeded nor was cancelled. It
    is deliberately broader than "endpoint-attributable failure": a transport
    error is a failure of the trial whether or not the endpoint caused it.
    Per-outcome attribution lives in `outcome_summary`.
    """
    for trial in trials:
        trial.validate()

    lifecycles = Counter(trial.lifecycle for trial in trials)

    planned = len(trials)
    started = sum(
        count
        for state, count in lifecycles.items()
        if state
        in (
            LifecycleState.STARTED,
            LifecycleState.TRANSPORT_COMPLETED,
            LifecycleState.PROTOCOL_COMPLETED,
        )
    )
    succeeded = sum(1 for trial in trials if trial.outcome in SUCCESS_OUTCOMES)
    cancelled = lifecycles[LifecycleState.CANCELLED]
    counters = RunCounters(
        planned=planned,
        started=started,
        transport_completed=lifecycles[LifecycleState.TRANSPORT_COMPLETED]
        + lifecycles[LifecycleState.PROTOCOL_COMPLETED],
        protocol_completed=lifecycles[LifecycleState.PROTOCOL_COMPLETED],
        succeeded=succeeded,
        failed=started - succeeded - cancelled,
        skipped=lifecycles[LifecycleState.SKIPPED],
        cancelled=cancelled,
        http_attempts=sum(len(trial.attempts) for trial in trials),
    )
    if not counters.succeeded <= counters.protocol_completed <= counters.started <= counters.planned:
        raise TrialSchemaError(
            "counter nesting violated: require "
            f"successful({counters.succeeded}) <= protocol_completed({counters.protocol_completed}) "
            f"<= started({counters.started}) <= planned({counters.planned})"
        )
    if counters.failed < 0:
        raise TrialSchemaError("counter accounting produced a negative failure count")
    return counters


def outcome_summary(trials: list[Trial]) -> dict[str, int]:
    """Counts per outcome state, with only observed outcomes present."""
    counts = Counter(trial.outcome.value for trial in trials)
    return dict(sorted(counts.items()))


def token_sizing_summary(trials: list[Trial]) -> dict[str, Any]:
    """Whether token-normalized claims are supported by this evidence.

    Unverifiable sizing is not a pass. A single unverified or out-of-tolerance
    trial is enough to withhold support for token-normalized claims.
    """
    verdicts = [trial.token_accounting.sizing_verified for trial in trials]
    evaluated = [verdict for verdict in verdicts if verdict is not None]
    unverifiable = sum(1 for verdict in verdicts if verdict is None)
    return {
        "trials_evaluated": len(evaluated),
        "within_tolerance": sum(1 for verdict in evaluated if verdict),
        "outside_tolerance": sum(1 for verdict in evaluated if not verdict),
        "unverifiable": unverifiable,
        "claims_supported": bool(evaluated) and all(evaluated) and unverifiable == 0,
    }


def write_trials_jsonl(path: Path, trials: list[Trial]) -> str:
    """Write normalized trials as JSONL and return the file's SHA-256.

    Each line carries the trial schema version so a later reader can reject
    incompatible records instead of reinterpreting them.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(trial.to_dict(), sort_keys=True) for trial in trials]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return sha256_bytes(path.read_bytes())


def sha256_bytes(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()
