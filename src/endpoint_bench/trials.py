from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

TRIAL_SCHEMA_VERSION = "0.2.0"


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
#:
#: `OUTPUT_TRUNCATED` is deliberately excluded. Hitting the configured output
#: ceiling is caused by the harness's own `max_completion_tokens`, not by the
#: endpoint, so counting it as an endpoint failure would misattribute our
#: configuration as a provider defect. It is tracked by `truncated` instead.
ENDPOINT_FAILURE_OUTCOMES = frozenset(
    {
        OutcomeState.TIMEOUT,
        OutcomeState.RATE_LIMITED,
        OutcomeState.SERVER_ERROR,
        OutcomeState.PROTOCOL_FAILURE,
        OutcomeState.STREAM_INTERRUPTED,
        OutcomeState.CONTEXT_REJECTED,
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

# -- token accounting vocabulary ---------------------------------------------

#: Which side produced the token counts AIPerf used for its metrics.
#:
#: The distinction is load-bearing. Under `provider_usage`, AIPerf sets
#: `input_sequence_length` from the server's `usage.prompt_tokens`, so that
#: metric is *not* an independent client estimate and comparing the two is
#: circular. Every sizing verdict must name which comparison it performed.
ACCOUNTING_CLIENT_TOKENIZER = "client_tokenizer"
ACCOUNTING_PROVIDER_USAGE = "provider_usage"
ACCOUNTING_SOURCES = (ACCOUNTING_CLIENT_TOKENIZER, ACCOUNTING_PROVIDER_USAGE)

#: Sizing verdict states. Never collapsed into one boolean.
SIZING_VERIFIED = "verified"
SIZING_FAILED = "failed"
SIZING_UNVERIFIABLE = "unverifiable"
SIZING_NOT_OBSERVED = "not_observed"
SIZING_STATES = (SIZING_VERIFIED, SIZING_FAILED, SIZING_UNVERIFIABLE, SIZING_NOT_OBSERVED)

#: Which comparison is authoritative for workload sizing in a given mode.
CHECK_CLIENT_VS_PROVIDER = "client_vs_provider"
CHECK_PROVIDER_VS_TARGET = "provider_vs_target"

# -- completion classification ----------------------------------------------

#: The server stopped for a reason other than our ceiling.
FINISH_NATURAL = "natural"
#: The server stopped because it reached our `max_completion_tokens`.
FINISH_CEILING = "ceiling"
#: The server stopped on a safety or content policy.
FINISH_CONTENT_FILTER = "content_filter"
#: The stream carried no terminal `finish_reason` at all.
FINISH_ABSENT = "absent"
#: A terminal `finish_reason` was present but is not in any known vocabulary.
FINISH_UNKNOWN = "unknown"

_FINISH_NATURAL = frozenset(
    {"stop", "end_turn", "stop_sequence", "eos", "tool_calls", "function_call", "completed"}
)
_FINISH_CEILING = frozenset({"length", "max_tokens"})
_FINISH_CONTENT_FILTER = frozenset({"content_filter", "safety", "blocked"})

#: Evidence gaps recorded when a required observation is missing.
EVIDENCE_HTTP_STATUS_UNOBSERVED = "http_status_unobserved"
EVIDENCE_FINISH_REASON_UNOBSERVED = "finish_reason_unobserved"
EVIDENCE_USAGE_UNOBSERVED = "usage_unobserved"


def classify_finish_reason(value: str | None) -> str:
    """Map a provider `finish_reason` onto a terminal class.

    Providers are not consistent here: the same condition surfaces as `length`
    or `max_tokens`, and natural completion as `stop` or `end_turn` or
    `tool_calls`. Unknown values are reported as unknown rather than assumed
    natural.
    """
    if value is None or value == "":
        return FINISH_ABSENT
    lowered = value.strip().lower()
    if lowered in _FINISH_CEILING:
        return FINISH_CEILING
    if lowered in _FINISH_CONTENT_FILTER:
        return FINISH_CONTENT_FILTER
    if lowered in _FINISH_NATURAL:
        return FINISH_NATURAL
    return FINISH_UNKNOWN


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
    """Distinct token measures that must never be collapsed into one.

    - `target`: the workload size the profile asked for.
    - `client_estimated`: what the client tokenizer counted in the serialized
      request. `None` when no client tokenization ran, which is the case under
      provider accounting. It is never back-filled from a provider-sourced
      metric, because that would manufacture an agreement with itself.
    - `provider_input`: what the provider reported in `usage`.
    - `provider_output`: the provider's `usage.completion_tokens`.
    - `observed_output`: what the client counted in the response. `None` under
      provider accounting for the same reason as `client_estimated`.

    Two independent checks are derived, and both are always reported:

    - `provider_vs_target`: did the provider see the workload size we asked for?
      This is available in both modes and is the authoritative sizing check when
      token counts come from the provider.
    - `client_vs_provider`: does an independent client tokenizer agree with the
      provider? This is only meaningful when the client actually tokenized, so it
      is `not_observed` under provider accounting rather than trivially true.

    No correction factor is ever applied to either comparison.
    """

    target_input_tokens: int | None = None
    client_estimated_input_tokens: int | None = None
    provider_input_tokens: int | None = None
    provider_output_tokens: int | None = None
    observed_output_tokens: int | None = None
    tolerance_pct: float = DEFAULT_TOKEN_TOLERANCE_PCT
    #: Which side produced the metrics AIPerf reported.
    accounting_source: str = ACCOUNTING_CLIENT_TOKENIZER
    #: Free-form provenance note explaining why a measure is absent.
    client_estimate_note: str | None = None

    def __post_init__(self) -> None:
        if self.accounting_source not in ACCOUNTING_SOURCES:
            raise TrialSchemaError(
                f"accounting_source `{self.accounting_source}` is not one of "
                f"{', '.join(ACCOUNTING_SOURCES)}"
            )
        # Enforce the anti-circularity invariant at construction time. Under
        # provider accounting AIPerf derives `input_sequence_length` from
        # `usage.prompt_tokens`, so a populated client estimate would be the
        # provider count wearing a client label.
        if self.accounting_source == ACCOUNTING_PROVIDER_USAGE and (
            self.client_estimated_input_tokens is not None
            or self.observed_output_tokens is not None
        ):
            raise TrialSchemaError(
                "provider accounting cannot carry a client-side token estimate; "
                "AIPerf derives those metrics from provider usage, so the comparison "
                "would be circular"
            )

    @property
    def provider_target_conformance_pct(self) -> float | None:
        """Relative difference between provider-reported and requested input size."""
        target = self.target_input_tokens
        provider = self.provider_input_tokens
        if target is None or provider is None or target <= 0:
            return None
        return abs(provider - target) / target * 100.0

    @property
    def provider_target_conformance(self) -> str:
        """Did the provider see the workload size the profile requested?"""
        value = self.provider_target_conformance_pct
        if value is None:
            return SIZING_UNVERIFIABLE
        return SIZING_VERIFIED if value <= self.tolerance_pct else SIZING_FAILED

    @property
    def client_provider_agreement_pct(self) -> float | None:
        """Relative difference between an independent client count and the provider."""
        client = self.client_estimated_input_tokens
        provider = self.provider_input_tokens
        if client is None or provider is None or client == 0:
            return None
        return abs(provider - client) / client * 100.0

    @property
    def client_provider_agreement(self) -> str:
        """Does an independent client tokenizer agree with the provider?"""
        if self.accounting_source == ACCOUNTING_PROVIDER_USAGE:
            return SIZING_NOT_OBSERVED
        value = self.client_provider_agreement_pct
        if value is None:
            return SIZING_UNVERIFIABLE
        return SIZING_VERIFIED if value <= self.tolerance_pct else SIZING_FAILED

    @property
    def discrepancy_pct(self) -> float | None:
        """Retained name for the client-versus-provider difference.

        `None` whenever no independent client estimate exists. Under provider
        accounting this is always `None`; a zero here would mean the client and
        the provider were the same measurement, not that they agreed.
        """
        return self.client_provider_agreement_pct

    @property
    def primary_check(self) -> str:
        """Which sizing comparison governs token-normalized claims for this trial."""
        if self.accounting_source == ACCOUNTING_PROVIDER_USAGE:
            return CHECK_PROVIDER_VS_TARGET
        return CHECK_CLIENT_VS_PROVIDER

    @property
    def primary_sizing_verdict(self) -> str:
        if self.primary_check == CHECK_PROVIDER_VS_TARGET:
            return self.provider_target_conformance
        return self.client_provider_agreement

    @property
    def sizing_verified(self) -> bool | None:
        """Whether the authoritative check for this mode passed.

        `None` means unverifiable, which is never treated as a pass.
        """
        verdict = self.primary_sizing_verdict
        if verdict == SIZING_VERIFIED:
            return True
        if verdict == SIZING_FAILED:
            return False
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "accounting_source": self.accounting_source,
            "target_input_tokens": self.target_input_tokens,
            "client_estimated_input_tokens": self.client_estimated_input_tokens,
            "provider_input_tokens": self.provider_input_tokens,
            "provider_output_tokens": self.provider_output_tokens,
            "observed_output_tokens": self.observed_output_tokens,
            "client_estimate_note": self.client_estimate_note,
            "provider_vs_target": {
                "discrepancy_pct": self.provider_target_conformance_pct,
                "verdict": self.provider_target_conformance,
            },
            "client_vs_provider": {
                "discrepancy_pct": self.client_provider_agreement_pct,
                "verdict": self.client_provider_agreement,
            },
            "discrepancy_pct": self.discrepancy_pct,
            "tolerance_pct": self.tolerance_pct,
            "primary_check": self.primary_check,
            "sizing_verified": self.sizing_verified,
        }


@dataclass(frozen=True)
class ProviderTelemetry:
    """Provider-reported cache and reasoning token counts.

    Every field is `None` when the provider did not report it, and `None` is
    never rendered as zero. Absence means "not observed", and a report that
    turned it into zero would claim the provider cached nothing.

    `sources` records where each value came from, because AIPerf 0.13.0
    normalizes cache-read and reasoning counts but does not expose cache-write at
    all. Cache-write is recovered from the raw streamed `usage` and is marked as
    such so a reader can tell a first-class metric from a recovered one.
    """

    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None
    visible_output_tokens: int | None = None
    sources: dict[str, str] = field(default_factory=dict)

    @property
    def cache_telemetry_observed(self) -> bool:
        return self.cache_read_tokens is not None or self.cache_write_tokens is not None

    @property
    def cache_write_observed(self) -> bool:
        """AIPerf 0.13.0 has no cache-write metric, so this is raw-only."""
        return self.cache_write_tokens is not None

    @property
    def all_reasoning(self) -> bool:
        """True when the entire completion budget was reasoning tokens.

        A run in which this holds everywhere produced no visible answer content,
        so its output throughput describes reasoning emission, not answers.
        """
        if self.reasoning_tokens is None or self.visible_output_tokens is None:
            return False
        return self.reasoning_tokens > 0 and self.visible_output_tokens == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "visible_output_tokens": self.visible_output_tokens,
            "cache_telemetry_observed": self.cache_telemetry_observed,
            "cache_write_observed": self.cache_write_observed,
            "reasoning_only_completion": self.all_reasoning,
            "sources": dict(sorted(self.sources.items())),
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
    #: Terminal class derived from `finish_reason`. Success requires `natural`.
    finish_class: str = FINISH_ABSENT
    outcome_detail: str | None = None
    notes: list[str] = field(default_factory=list)
    #: Named gaps in the evidence. Enumerated rather than free-form so a report
    #: can count them instead of grepping prose.
    evidence_gaps: list[str] = field(default_factory=list)
    attribution: str = "unknown"
    token_accounting: TokenAccounting = field(default_factory=TokenAccounting)
    provider_telemetry: ProviderTelemetry = field(default_factory=ProviderTelemetry)
    #: The `max_completion_tokens` the request actually carried, when known. It
    #: makes a ceiling truncation attributable to our own configuration.
    requested_max_completion_tokens: int | None = None

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

    @property
    def observed_http_status(self) -> int | None:
        """The status of the last attempt that observed one."""
        for attempt in reversed(self.attempts):
            if attempt.http_status is not None:
                return attempt.http_status
        return None

    @property
    def http_status_successful(self) -> bool | None:
        """True for 2xx, False for any other observed status, None if unobserved."""
        status = self.observed_http_status
        if status is None:
            return None
        return 200 <= status < 300

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
            "completion": {
                "finish_reason": self.finish_reason,
                "finish_class": self.finish_class,
                "requested_max_completion_tokens": self.requested_max_completion_tokens,
            },
            "token_accounting": self.token_accounting.to_dict(),
            "provider_telemetry": self.provider_telemetry.to_dict(),
            "attribution": self.attribution,
            "evidence_gaps": list(self.evidence_gaps),
            "notes": list(self.notes),
        }

    def validate(self) -> None:
        """Reject internally inconsistent trial evidence.

        These are the invariants the plan requires to hold before any trial is
        counted. A violation means the harness is wrong, not the endpoint.
        """
        lifecycle = self.lifecycle
        status_ok = self.http_status_successful

        if self.outcome is OutcomeState.SUCCESS and lifecycle is not LifecycleState.PROTOCOL_COMPLETED:
            raise TrialSchemaError(
                f"trial `{self.trial_id}`: outcome `success` requires lifecycle "
                f"`protocol_completed`, observed `{lifecycle.value}`"
            )
        # A non-2xx response can never be a successful trial, no matter how
        # well-formed its body looked.
        if self.outcome is OutcomeState.SUCCESS and status_ok is False:
            raise TrialSchemaError(
                f"trial `{self.trial_id}`: outcome `success` contradicts observed "
                f"HTTP status {self.observed_http_status}"
            )
        # Success without a witnessed 2xx is an unverified claim, not evidence.
        if self.outcome is OutcomeState.SUCCESS and status_ok is None:
            raise TrialSchemaError(
                f"trial `{self.trial_id}`: outcome `success` has no observed 2xx "
                "HTTP status; success cannot be asserted from body content alone"
            )
        # Ceiling truncation is our own configured limit, never a success.
        if self.outcome is OutcomeState.SUCCESS and self.finish_class == FINISH_CEILING:
            raise TrialSchemaError(
                f"trial `{self.trial_id}`: outcome `success` with finish_reason "
                f"`{self.finish_reason}` (ceiling truncation)"
            )
        if self.finish_class != classify_finish_reason(self.finish_reason):
            raise TrialSchemaError(
                f"trial `{self.trial_id}`: finish_class contradicts finish_reason"
            )
        if self.outcome is OutcomeState.SUCCESS and self.finish_class != FINISH_NATURAL:
            raise TrialSchemaError(
                f"trial `{self.trial_id}`: success requires a natural finish reason"
            )
        if self.finish_class not in (
            FINISH_NATURAL,
            FINISH_CEILING,
            FINISH_CONTENT_FILTER,
            FINISH_ABSENT,
            FINISH_UNKNOWN,
        ):
            raise TrialSchemaError(
                f"trial `{self.trial_id}`: unknown finish_class `{self.finish_class}`"
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
    """Separate counters for each lifecycle stage, outcome class, and HTTP attempt.

    Percentiles are never computed from these; they are accounting only.

    `truncated` is kept apart from `failed` because ceiling truncation is caused
    by our own `max_completion_tokens`. Folding it into `failed` would report a
    harness configuration choice as an endpoint defect, and folding it into
    `succeeded` (as an earlier revision did) hides that the measured latency is
    time-to-ceiling rather than time-to-completion.
    """

    planned: int
    started: int
    transport_completed: int
    protocol_completed: int
    succeeded: int
    truncated: int
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
            "truncated_trials": self.truncated,
            "failed_trials": self.failed,
            "skipped_trials": self.skipped,
            "cancelled_trials": self.cancelled,
            "http_attempts": self.http_attempts,
        }


def count_trials(trials: list[Trial]) -> RunCounters:
    """Derive counters from trial evidence, enforcing the nesting invariants.

    `failed` is every started trial that neither succeeded, was ceiling-truncated,
    nor was cancelled. It is deliberately broader than "endpoint-attributable
    failure": a transport error is a failure of the trial whether or not the
    endpoint caused it. Per-outcome attribution lives in `outcome_summary`.
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
    truncated = sum(1 for trial in trials if trial.outcome is OutcomeState.OUTPUT_TRUNCATED)
    cancelled = lifecycles[LifecycleState.CANCELLED]
    counters = RunCounters(
        planned=planned,
        started=started,
        transport_completed=lifecycles[LifecycleState.TRANSPORT_COMPLETED]
        + lifecycles[LifecycleState.PROTOCOL_COMPLETED],
        protocol_completed=lifecycles[LifecycleState.PROTOCOL_COMPLETED],
        succeeded=succeeded,
        truncated=truncated,
        failed=started - succeeded - truncated,
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
    if counters.truncated > counters.protocol_completed:
        raise TrialSchemaError(
            f"counter nesting violated: truncated({counters.truncated}) exceeds "
            f"protocol_completed({counters.protocol_completed})"
        )
    if counters.failed < 0:
        raise TrialSchemaError("counter accounting produced a negative failure count")
    if counters.succeeded + counters.truncated + counters.failed != counters.started:
        raise TrialSchemaError(
            "outcome partition violated: require successful"
            f"({counters.succeeded}) + truncated({counters.truncated}) + "
            f"failed({counters.failed}) == started({counters.started})"
        )
    return counters


def outcome_summary(trials: list[Trial]) -> dict[str, int]:
    """Counts per outcome state, with only observed outcomes present."""
    counts = Counter(trial.outcome.value for trial in trials)
    return dict(sorted(counts.items()))


def completion_summary(trials: list[Trial]) -> dict[str, Any]:
    """How responses terminated, and whether the measured latency is censored.

    A trial that hit our output ceiling did not finish. Its `request_latency` is
    the time it took to reach the ceiling, which is a lower bound on the time a
    natural completion would have taken. Time-to-first-token is unaffected
    because the first token arrives long before any ceiling is reached.
    """
    classes = Counter(trial.finish_class for trial in trials if trial.lifecycle is LifecycleState.PROTOCOL_COMPLETED)
    finish_reasons = Counter(
        trial.finish_reason or "<absent>"
        for trial in trials
        if trial.lifecycle is LifecycleState.PROTOCOL_COMPLETED
    )
    completions = sum(classes.values())
    natural = classes.get(FINISH_NATURAL, 0)
    ceiling = classes.get(FINISH_CEILING, 0)
    return {
        "protocol_completed_trials": completions,
        "natural_completions": natural,
        "ceiling_truncated": ceiling,
        "content_filtered": classes.get(FINISH_CONTENT_FILTER, 0),
        "finish_reason_absent": classes.get(FINISH_ABSENT, 0),
        "finish_reason_unknown": classes.get(FINISH_UNKNOWN, 0),
        "finish_reason_counts": dict(sorted(finish_reasons.items())),
        # Latency is censored for the truncated share only. A report uses this to
        # decide whether request_latency may be read as time-to-completion.
        "latency_censored": ceiling > 0,
        "latency_censored_fraction": (ceiling / completions) if completions else None,
    }


def provider_telemetry_summary(trials: list[Trial]) -> dict[str, Any]:
    """Aggregate provider cache and reasoning telemetry.

    Absent values stay absent. A field is only summed over trials that actually
    reported it, and the reporting count is always published alongside the sum so
    a partial total cannot be mistaken for a complete one.
    """
    fields = ("cache_read_tokens", "cache_write_tokens", "reasoning_tokens", "visible_output_tokens")
    totals: dict[str, Any] = {}
    for name in fields:
        values = [getattr(trial.provider_telemetry, name) for trial in trials]
        observed = [value for value in values if value is not None]
        totals[name] = {
            "sum": sum(observed) if observed else None,
            "trials_reporting": len(observed),
            "trials_total": len(trials),
        }

    sources: set[str] = set()
    for trial in trials:
        sources.update(trial.provider_telemetry.sources.values())

    reasoning_only = sum(1 for trial in trials if trial.provider_telemetry.all_reasoning)
    return {
        "metrics": totals,
        "sources": sorted(sources),
        # Every field above is null-or-number. `cache_write_tokens` is recovered
        # from the raw stream because AIPerf 0.13.0 exposes no cache-write metric.
        "cache_write_source": (
            "raw_response_usage" if totals["cache_write_tokens"]["trials_reporting"] else None
        ),
        "reasoning_only_completions": reasoning_only,
    }


def token_sizing_summary(trials: list[Trial]) -> dict[str, Any]:
    """Whether token-normalized claims are supported by this evidence.

    Two independent comparisons are reported side by side, because they answer
    different questions and only one of them is meaningful in each accounting
    mode. Unverifiable sizing is not a pass. A single out-of-tolerance trial is
    enough to withhold support for token-normalized claims.
    """
    provider_vs_target = [trial.token_accounting.provider_target_conformance for trial in trials]
    client_vs_provider = [trial.token_accounting.client_provider_agreement for trial in trials]
    primary = [trial.token_accounting.primary_sizing_verdict for trial in trials]
    sources = Counter(trial.token_accounting.accounting_source for trial in trials)

    def tally(verdicts: list[str]) -> dict[str, int]:
        counts = Counter(verdicts)
        return {state: counts.get(state, 0) for state in SIZING_STATES}

    evaluated = [verdict for verdict in primary if verdict in (SIZING_VERIFIED, SIZING_FAILED)]
    # A client estimate that coincides exactly with the provider count is the
    # signature of provider accounting leaking into a client-labelled metric.
    # It is reported rather than assumed away.
    coincident = sum(
        1
        for trial in trials
        if trial.token_accounting.client_estimated_input_tokens is not None
        and trial.token_accounting.client_estimated_input_tokens
        == trial.token_accounting.provider_input_tokens
    )
    return {
        "accounting_sources": {source: count for source, count in sorted(sources.items())},
        "provider_vs_target": tally(provider_vs_target),
        "client_vs_provider": tally(client_vs_provider),
        "primary_checks": {
            check: sum(1 for trial in trials if trial.token_accounting.primary_check == check)
            for check in (CHECK_CLIENT_VS_PROVIDER, CHECK_PROVIDER_VS_TARGET)
        },
        "trials_evaluated": len(evaluated),
        "within_tolerance": sum(1 for verdict in evaluated if verdict == SIZING_VERIFIED),
        "outside_tolerance": sum(1 for verdict in evaluated if verdict == SIZING_FAILED),
        "unverifiable": sum(1 for verdict in primary if verdict in (SIZING_UNVERIFIABLE, SIZING_NOT_OBSERVED)),
        "client_estimates_coinciding_with_provider": coincident,
        "claims_supported": bool(evaluated)
        and all(verdict == SIZING_VERIFIED for verdict in evaluated)
        and len(evaluated) == len(trials),
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
