"""Bounded completion-latency analysis for right-censored trials.

A response that stops at the configured output ceiling did not finish, so its
completion latency is unknown. Its cap-hit time is still a rigorous per-trial
lower bound: the response was demonstrably still generating at that instant.

If `L_i <= T_i` for every trial, then every order statistic of the lower bounds
is a lower bound on the corresponding order statistic of the true completion
times. A sample percentile of the lower bounds is therefore a lower bound on the
true percentile.

That asymmetry is the whole point:

- A lower bound **above** an upper-bound objective (`p50 <= X`) proves failure:
  the true value is at least the bound, so it is also above `X`.
- A lower bound **below** the objective cannot prove success. The true value may
  be above `X`, so the result stays `inconclusive`.
- This module never returns `qualified` from a lower bound alone.

It is deliberately separate from the contract evaluator. The 1.1 contract still
treats any censoring as a data-quality failure; this module is the bounded
extension that can describe what the censored sample supports without changing
that contract's verdict rules.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .qualification import PERCENTILE_ESTIMATOR, QualificationError, percentile

VERDICT_FAILED = "failed"
VERDICT_INCONCLUSIVE = "inconclusive"
VERDICT_NOT_OBSERVED = "not_observed"

SUPPORTED_STATISTICS = ("p50", "p95", "mean")

#: Outcomes that denote a completed response. Everything else is either an
#: endpoint outcome (counted failed) or a non-observation (counted excluded).
_COMPLETION_OUTCOMES = frozenset({"success", "output_truncated"})

#: Terminal failure outcomes the harness can produce, plus the legacy generic
#: `failed` label. Anything outside this vocabulary is rejected rather than
#: silently classified as a failure.
_FAILURE_OUTCOMES = frozenset(
    {
        "timeout",
        "rate_limited",
        "server_error",
        "client_error",
        "protocol_failure",
        "stream_interrupted",
        "context_rejected",
        "budget_exceeded",
        "unknown",
        "failed",
    }
)

_NON_OBSERVATION_OUTCOMES = frozenset({"skipped", "cancelled"})

_KNOWN_FINISH_CLASSES = frozenset({"natural", "ceiling", "content_filter", "absent", "unknown"})
_KNOWN_LIFECYCLES = frozenset(
    {"planned", "started", "transport_completed", "protocol_completed", "cancelled", "skipped"}
)


def _finite_number(value: object, name: str) -> float:
    """Return a finite float or raise. Booleans are not numbers here."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise QualificationError(f"{name} must be a finite number, got {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise QualificationError(f"{name} must be finite, got {value!r}")
    return number


@dataclass(frozen=True)
class LatencySample:
    """Per-trial latency values and whether each is a lower bound.

    `censored[i]` means the response stopped at the output ceiling, so
    `values_ms[i]` is a cap-hit lower bound rather than a completion time.
    """

    values_ms: tuple[float, ...]
    censored: tuple[bool, ...]

    def __post_init__(self) -> None:
        if len(self.values_ms) != len(self.censored):
            raise QualificationError(
                "latency sample needs one censoring flag per value: "
                f"{len(self.values_ms)} values, {len(self.censored)} flags"
            )
        for index, value in enumerate(self.values_ms):
            number = _finite_number(value, f"latency value[{index}]")
            if number < 0:
                raise QualificationError("latency values must be non-negative")
        for index, flag in enumerate(self.censored):
            if not isinstance(flag, bool):
                raise QualificationError(
                    f"censoring flag[{index}] must be a boolean, got {flag!r}"
                )

    @property
    def sample_size(self) -> int:
        return len(self.values_ms)

    @property
    def censored_count(self) -> int:
        return sum(1 for flag in self.censored if flag)

    @property
    def censoring_fraction(self) -> float | None:
        if not self.values_ms:
            return None
        return self.censored_count / len(self.values_ms)

    @property
    def all_censored(self) -> bool:
        return bool(self.values_ms) and self.censored_count == len(self.values_ms)


@dataclass(frozen=True)
class TrialSamples:
    """Latency and TTFT samples plus the accounting that came with them."""

    latency: LatencySample
    ttft: LatencySample
    natural: int
    censored: int
    failed: int
    excluded: int
    records_total: int = 0

    @property
    def denominators_consistent(self) -> bool:
        return self.natural + self.censored + self.failed + self.excluded == self.records_total

    def to_dict(self) -> dict[str, int | bool]:
        return {
            "records_total": self.records_total,
            "natural_completions": self.natural,
            "ceiling_truncated": self.censored,
            "failed_trials": self.failed,
            "excluded_trials": self.excluded,
            "latency_sample_size": self.latency.sample_size,
            "ttft_sample_size": self.ttft.sample_size,
            "denominators_consistent": self.denominators_consistent,
        }


def _http_statuses(record: dict[str, Any]) -> list[int]:
    attempts = record.get("attempts")
    if not isinstance(attempts, list):
        return []
    statuses: list[int] = []
    for attempt in attempts:
        if not isinstance(attempt, dict):
            raise QualificationError("trial attempt record must be an object")
        status = attempt.get("http_status")
        if status is None:
            continue
        if isinstance(status, bool) or not isinstance(status, int):
            raise QualificationError(f"http_status must be an integer, got {status!r}")
        statuses.append(status)
    return statuses


def _validate_completion_record(record: dict[str, Any], identifier: str) -> tuple[str, str | None]:
    """Return (outcome, finish_class) after rejecting inconsistent metadata.

    The review counterexamples were an output-truncated record with a missing
    finish class counted as natural, and a NaN latency accepted as a value. Both
    would change a published denominator or percentile, so both fail closed here
    before any statistic is computed.
    """
    outcome = record.get("outcome")
    if not isinstance(outcome, str):
        raise QualificationError(f"{identifier}: `outcome` must be a string")

    lifecycle = record.get("lifecycle")
    if lifecycle is not None:
        if not isinstance(lifecycle, str) or lifecycle not in _KNOWN_LIFECYCLES:
            raise QualificationError(
                f"{identifier}: unknown lifecycle {lifecycle!r}; expected one of "
                f"{', '.join(sorted(_KNOWN_LIFECYCLES))}"
            )
        if lifecycle == "skipped" and outcome != "skipped":
            raise QualificationError(
                f"{identifier}: lifecycle `skipped` contradicts outcome `{outcome}`"
            )
        if lifecycle == "planned" and outcome not in ("unknown", "skipped"):
            raise QualificationError(
                f"{identifier}: lifecycle `planned` cannot carry outcome `{outcome}`"
            )
        if outcome in _COMPLETION_OUTCOMES and lifecycle != "protocol_completed":
            raise QualificationError(
                f"{identifier}: outcome `{outcome}` requires lifecycle "
                f"`protocol_completed`, observed `{lifecycle}`"
            )

    completion = record.get("completion")
    if completion is None:
        completion = {}
    if not isinstance(completion, dict):
        raise QualificationError(f"{identifier}: `completion` must be an object")

    finish_class = completion.get("finish_class")
    if finish_class is not None and (
        not isinstance(finish_class, str) or finish_class not in _KNOWN_FINISH_CLASSES
    ):
        raise QualificationError(
            f"{identifier}: unknown finish_class {finish_class!r}; "
            f"expected one of {', '.join(sorted(_KNOWN_FINISH_CLASSES))}"
        )

    if outcome in _NON_OBSERVATION_OUTCOMES:
        return outcome, finish_class
    if outcome in _COMPLETION_OUTCOMES:
        if finish_class is None:
            raise QualificationError(
                f"{identifier}: outcome `{outcome}` requires an explicit finish_class"
            )
        if outcome == "success" and finish_class != "natural":
            raise QualificationError(
                f"{identifier}: success requires finish_class `natural`, got `{finish_class}`"
            )
        if outcome == "output_truncated" and finish_class != "ceiling":
            raise QualificationError(
                f"{identifier}: output_truncated requires finish_class `ceiling`, "
                f"got `{finish_class}`"
            )
        statuses = _http_statuses(record)
        if not statuses:
            raise QualificationError(
                f"{identifier}: outcome `{outcome}` has no observed HTTP status; "
                "body content alone cannot establish a 2xx completion"
            )
        if not 200 <= statuses[-1] < 300:
            raise QualificationError(
                f"{identifier}: outcome `{outcome}` contradicts HTTP status "
                f"{statuses[-1]}"
            )
        if outcome == "output_truncated":
            requested = completion.get("requested_max_completion_tokens")
            if requested is not None and (
                isinstance(requested, bool)
                or not isinstance(requested, int)
                or requested <= 0
            ):
                raise QualificationError(
                    f"{identifier}: requested_max_completion_tokens must be a positive integer"
                )
        return outcome, finish_class
    if outcome in _FAILURE_OUTCOMES:
        return outcome, finish_class
    raise QualificationError(
        f"{identifier}: unknown outcome {outcome!r}; refusing to classify it"
    )


def samples_from_trial_records(trials: Iterable[dict[str, Any]]) -> TrialSamples:
    """Split trial records into exact and lower-bounded latency observations.

    Every record is validated before any statistic is computed. A failed trial is
    an endpoint outcome, not a completion observation, so it is counted and
    excluded from both samples. A protocol-completed trial without a duration is
    counted as excluded rather than silently treated as zero. TTFT is recorded
    even for a trial that later hits the output ceiling; it happens before the
    cap can apply.

    The four counts (`natural`, `censored`, `failed`, `excluded`) partition the
    input records. A caller must publish all four; none may be folded away.
    """
    records = list(trials)
    latency: list[tuple[float, bool]] = []
    ttft: list[tuple[float, bool]] = []
    counts = {"natural": 0, "censored": 0, "failed": 0, "excluded": 0}
    seen_ids: set[str] = set()

    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise QualificationError(f"trial record[{index}] must be an object")
        trial_id = record.get("trial_id")
        if trial_id is not None:
            if not isinstance(trial_id, str) or not trial_id:
                raise QualificationError(f"trial record[{index}]: trial_id must be a string")
            if trial_id in seen_ids:
                raise QualificationError(
                    f"duplicate trial identity `{trial_id}` in the supplied evidence"
                )
            seen_ids.add(trial_id)
        identifier = f"trial `{trial_id}`" if trial_id else f"trial record[{index}]"

        outcome, finish_class = _validate_completion_record(record, identifier)
        if outcome not in _COMPLETION_OUTCOMES:
            if outcome in _FAILURE_OUTCOMES:
                counts["failed"] += 1
            else:
                # Skipped and cancelled trials are not endpoint failures, but they
                # are not completion observations either.
                counts["excluded"] += 1
            continue

        timing = record.get("timing")
        if timing is None:
            timing = {}
        if not isinstance(timing, dict):
            raise QualificationError(f"{identifier}: `timing` must be an object")

        duration = timing.get("duration_ms")
        if duration is None:
            counts["excluded"] += 1
            continue
        duration_value = _finite_number(duration, f"{identifier}: duration_ms")
        if duration_value < 0:
            raise QualificationError(f"{identifier}: duration_ms must be non-negative")

        censored = finish_class == "ceiling"
        latency.append((duration_value, censored))
        counts["censored" if censored else "natural"] += 1

        first_token = timing.get("ttft_ms")
        if first_token is not None:
            ttft.append((_finite_number(first_token, f"{identifier}: ttft_ms"), False))

    samples = TrialSamples(
        latency=LatencySample(
            tuple(value for value, _ in latency), tuple(flag for _, flag in latency)
        ),
        ttft=LatencySample(tuple(value for value, _ in ttft), tuple(flag for _, flag in ttft)),
        natural=counts["natural"],
        censored=counts["censored"],
        failed=counts["failed"],
        excluded=counts["excluded"],
        records_total=len(records),
    )
    if not samples.denominators_consistent:
        raise QualificationError(
            "trial denominators do not partition the input: "
            f"natural({samples.natural}) + censored({samples.censored}) + "
            f"failed({samples.failed}) + excluded({samples.excluded}) != "
            f"records({samples.records_total})"
        )
    return samples


@dataclass(frozen=True)
class BoundedStatistic:
    metric: str
    statistic: str
    estimator: str
    lower_bound: float | None
    sample_size: int
    censored_count: int
    censoring_fraction: float | None
    threshold: float | None
    operator: str
    verdict: str
    note: str

    def to_dict(self) -> dict[str, object]:
        return {
            "metric": self.metric,
            "statistic": self.statistic,
            "estimator": self.estimator,
            "lower_bound": self.lower_bound,
            "sample_size": self.sample_size,
            "censored_count": self.censored_count,
            "censoring_fraction": self.censoring_fraction,
            "threshold": self.threshold,
            "operator": self.operator,
            "verdict": self.verdict,
            "note": self.note,
        }


def bounded_value(sample: LatencySample, statistic: str) -> float:
    """The requested statistic computed over the lower-bound vector.

    Every input is a lower bound, so the result is a lower bound on the same
    statistic of the true completion times whenever the statistic is monotone in
    its order statistics. `mean`, `p50`, and `p95` all satisfy that.
    """
    if statistic not in SUPPORTED_STATISTICS:
        raise QualificationError(
            f"statistic `{statistic}` is not supported for bounded latency; "
            f"choose one of {', '.join(SUPPORTED_STATISTICS)}"
        )
    if not sample.values_ms:
        raise QualificationError("cannot compute a bounded statistic of an empty sample")
    values = list(sample.values_ms)
    if statistic == "p50":
        return percentile(values, 0.50)
    if statistic == "p95":
        return percentile(values, 0.95)
    return sum(values) / len(values)


def evaluate_upper_bound(
    sample: LatencySample,
    *,
    metric: str,
    statistic: str,
    threshold: float | None,
    operator: str = "<=",
) -> BoundedStatistic:
    """Evaluate an upper-bound latency objective against lower-bounded data.

    Returns `failed` only when the optimistic lower bound already exceeds the
    threshold. A lower bound below the threshold stays `inconclusive`.
    """
    if operator != "<=":
        raise QualificationError(
            "bounded completion latency only evaluates upper-bound (`<=`) objectives; "
            f"got `{operator}`"
        )
    if threshold is not None:
        threshold = _finite_number(threshold, "threshold")
        if threshold < 0:
            raise QualificationError("threshold must be non-negative")
    if not sample.values_ms:
        return BoundedStatistic(
            metric=metric,
            statistic=statistic,
            estimator=PERCENTILE_ESTIMATOR if statistic in ("p50", "p95") else statistic,
            lower_bound=None,
            sample_size=0,
            censored_count=0,
            censoring_fraction=None,
            threshold=threshold,
            operator=operator,
            verdict=VERDICT_NOT_OBSERVED,
            note="no eligible trials; no latency number is invented",
        )

    bound = bounded_value(sample, statistic)
    estimator = PERCENTILE_ESTIMATOR if statistic in ("p50", "p95") else statistic
    base = {
        "metric": metric,
        "statistic": statistic,
        "estimator": estimator,
        "lower_bound": bound,
        "sample_size": sample.sample_size,
        "censored_count": sample.censored_count,
        "censoring_fraction": sample.censoring_fraction,
        "threshold": threshold,
        "operator": operator,
    }

    if threshold is None:
        return BoundedStatistic(
            **base,
            verdict=VERDICT_INCONCLUSIVE,
            note="threshold is unratified; a bound cannot supply a pass mark",
        )

    if bound > threshold:
        return BoundedStatistic(
            **base,
            verdict=VERDICT_FAILED,
            note=(
                "even the lower bound exceeds the threshold; the true completion "
                "statistic is at least this value, so the objective is missed"
            ),
        )

    return BoundedStatistic(
        **base,
        verdict=VERDICT_INCONCLUSIVE,
        note=(
            "the lower bound is below the threshold, but the true value may be "
            "higher; censored trials prevent a success claim"
        ),
    )


def evaluate_upper_bound_by_concurrency(
    samples_by_level: dict[int, LatencySample],
    *,
    metric: str,
    statistic: str,
    threshold: float | None,
    operator: str = "<=",
) -> dict[int, BoundedStatistic]:
    """Evaluate the same objective independently at each concurrency level."""
    return {
        level: evaluate_upper_bound(
            sample,
            metric=metric,
            statistic=statistic,
            threshold=threshold,
            operator=operator,
        )
        for level, sample in sorted(samples_by_level.items())
    }
