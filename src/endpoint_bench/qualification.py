"""Contract evaluation against synthetic evidence.

This is **not** a live qualification pipeline. No endpoint has been qualified. The
module exists so the contract's verdict rules can be exercised and regression-tested
with explicitly synthetic evidence, which is the only way to validate the rules
without making a claim about a real endpoint.

The rules it enforces, all of which were previously only prose:

- An unratified performance threshold cannot yield `qualified`. A missing threshold
  means the objective cannot be evaluated, so it cannot be counted as met.
- Performance objectives are evaluated on a **pooled** per-trial sample, never on a
  single window. Windows establish evidence validity; pooling establishes
  performance. This is what resolves the version 1.0 contradiction where a
  20-request window was asked to satisfy a p50 objective under a 30-sample floor.
- Pilot, calibration and exploratory evidence cannot be relabelled as qualification
  evidence.
- A latency statistic is published with its eligible population, so a group with no
  eligible responses yields no invented latency number.

Statistics are descriptive. Percentiles use linear interpolation of closest ranks,
which is recorded in the output so a reader knows exactly what was computed.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

SUPPORTED_CONTRACT_DOC_VERSION = "1.1"

VERDICT_QUALIFIED = "qualified"
VERDICT_FAILED = "failed"
VERDICT_INCONCLUSIVE = "inconclusive"

#: Evidence roles that may support a qualification verdict.
QUALIFICATION_ROLE = "qualification"
#: Roles that are permanently excluded from qualification evidence.
EXCLUDED_ROLES = ("pilot", "calibration", "exploratory", "screening")

#: Estimator name recorded with every percentile, so the number is reproducible.
PERCENTILE_ESTIMATOR = "linear_interpolation_of_closest_ranks"

#: Objective statistics this evaluator can compute.
SUPPORTED_STATISTICS = ("p50", "p95", "mean", "fraction")

#: The contract requires at least three evaluation windows per concurrency
#: level; one window never qualifies.
REQUIRED_WINDOWS_PER_LEVEL = 3

#: A non-null threshold is not ratification by itself. The record must name the
#: requirement's origin, date, scope and expiry so the pass mark cannot be
#: invented from the measurement it judges.
RATIFICATION_REQUIRED_FIELDS = (
    "origin",
    "date",
    "workload",
    "concurrency",
    "client_profile",
    "deployment_key",
    "region",
    "expiry",
)


class QualificationError(ValueError):
    """Raised when the contract or the evidence cannot be evaluated as supplied."""


@dataclass(frozen=True)
class WindowEvidence:
    """One window's evidence. A window is a validity unit, not a performance unit.

    `eligible_observations` holds per-trial values keyed by metric name. Keeping the
    raw per-trial values rather than a precomputed mean is what makes a pooled
    statistic possible without averaging window statistics.

    `window_id` and `trial_ids` give the evidence identity. `workload_id`,
    `client_profile`, `deployment_key` and `region` are the explicit pooling
    cohort; windows with different values are never pooled.
    """

    label: str
    concurrency: int
    eligible_observations: dict[str, list[float]] = field(default_factory=dict)
    planned_trials: int = 0
    http_attempts: int = 0
    started_trials: int = 0
    successful_trials: int = 0
    natural_completions: int = 0
    ceiling_truncated: int = 0
    endpoint_failures: int = 0
    provider_target_conformance_failures: int = 0
    accounting_provenance_consistent: bool = True
    schema_validated: bool = True
    raw_artifacts_present: bool = True
    evidence_role: str = QUALIFICATION_ROLE
    window_id: str = ""
    trial_ids: tuple[str, ...] = ()
    workload_id: str = ""
    client_profile: str = ""
    deployment_key: str = ""
    region: str = ""

    @property
    def censoring_rate(self) -> float | None:
        completed = self.natural_completions + self.ceiling_truncated
        if completed <= 0:
            return None
        return self.ceiling_truncated / completed

    @property
    def error_rate(self) -> float | None:
        if self.started_trials <= 0:
            return None
        return self.endpoint_failures / self.started_trials


def load_contract(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise QualificationError(f"contract does not exist: {path}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise QualificationError(f"contract `{path}` is not valid JSON: {error}") from error
    if not isinstance(document, dict):
        raise QualificationError(f"contract `{path}` is not a JSON object")
    version = document.get("doc_version")
    if version != SUPPORTED_CONTRACT_DOC_VERSION:
        raise QualificationError(
            f"contract doc_version `{version}` is not `{SUPPORTED_CONTRACT_DOC_VERSION}`; "
            "an evaluator must be updated rather than guessing at new rules"
        )
    return document


def percentile(values: list[float], q: float) -> float:
    """Percentile by linear interpolation of closest ranks.

    Chosen because it is the definition most readers assume, and named in the
    output so a different definition elsewhere is visible rather than implicit.
    """
    if not values:
        raise QualificationError("cannot compute a percentile of an empty sample")
    if not 0.0 <= q <= 1.0:
        raise QualificationError(f"quantile must be in [0, 1], got {q}")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = q * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def statistic(values: list[float], name: str) -> float:
    if name == "p50":
        return percentile(values, 0.50)
    if name == "p95":
        return percentile(values, 0.95)
    if name == "mean":
        return sum(values) / len(values)
    raise QualificationError(f"statistic `{name}` is not sample-computable")


def pooled(observations: dict[str, list[float]], metric: str) -> list[float]:
    """Pooled per-trial values for one metric across compatible windows."""
    return list(observations.get(metric, []))


#: Completion latency and throughput are only defined for natural completions.
_COMPLETION_METRICS = ("request_latency", "output_token_throughput")

#: Every one of these is a count of trials or calls, never a boolean or float.
_COUNTER_FIELDS = (
    "planned_trials",
    "http_attempts",
    "started_trials",
    "successful_trials",
    "natural_completions",
    "ceiling_truncated",
    "endpoint_failures",
    "provider_target_conformance_failures",
)

#: Flags that select a data-quality gate. A truthy non-boolean is not a flag.
_BOOL_FIELDS = (
    "accounting_provenance_consistent",
    "schema_validated",
    "raw_artifacts_present",
)

#: Identity fields whose values must be strings before they can be pooled.
_TEXT_FIELDS = (
    "label",
    "window_id",
    "workload_id",
    "client_profile",
    "deployment_key",
    "region",
    "evidence_role",
)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _input_problems(window: WindowEvidence) -> list[str]:
    """Reject values that cannot be counts/observations before any arithmetic."""
    problems: list[str] = []
    for name in _COUNTER_FIELDS:
        value = getattr(window, name)
        if not _is_int(value):
            problems.append(f"{name} must be an integer, got {value!r}")
        elif value < 0:
            problems.append(f"{name} must be nonnegative, got {value}")
    if not _is_int(window.concurrency):
        problems.append(f"concurrency must be an integer, got {window.concurrency!r}")
    elif window.concurrency < 1:
        problems.append(f"concurrency must be positive, got {window.concurrency}")
    observations = window.eligible_observations
    if not isinstance(observations, dict):
        problems.append("eligible_observations must be a mapping")
    else:
        for metric, values in observations.items():
            if not isinstance(metric, str) or not metric:
                problems.append(f"observation metric {metric!r} must be a non-empty string")
                continue
            if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
                problems.append(f"{metric} observations must be a list or tuple")
                continue
            for index, value in enumerate(values):
                if not _is_number(value):
                    problems.append(
                        f"{metric}[{index}] must be a finite number, got {value!r}"
                    )
                elif isinstance(value, float) and not math.isfinite(value):
                    problems.append(f"{metric}[{index}] must be finite, got {value!r}")
                elif value < 0:
                    problems.append(f"{metric}[{index}] must be nonnegative, got {value!r}")
    for name in _BOOL_FIELDS:
        value = getattr(window, name)
        if not isinstance(value, bool):
            problems.append(f"{name} must be a boolean, got {value!r}")
    for name in _TEXT_FIELDS:
        value = getattr(window, name)
        if not isinstance(value, str):
            problems.append(f"{name} must be a string, got {value!r}")
    if not isinstance(window.trial_ids, (tuple, list)) or not all(
        isinstance(trial_id, str) for trial_id in window.trial_ids
    ):
        problems.append("trial_ids must be a sequence of strings")
    return problems


def _count_problems(window: WindowEvidence) -> list[str]:
    """Reject count/cohort arithmetic that cannot describe one window."""
    problems: list[str] = []
    if window.started_trials > window.http_attempts:
        problems.append(
            f"started_trials {window.started_trials} exceeds http_attempts "
            f"{window.http_attempts}"
        )
    if window.successful_trials > window.started_trials:
        problems.append(
            f"successful_trials {window.successful_trials} exceeds started_trials "
            f"{window.started_trials}"
        )
    if window.natural_completions > window.successful_trials:
        problems.append(
            f"natural_completions {window.natural_completions} exceeds "
            f"successful_trials {window.successful_trials}"
        )
    if window.natural_completions + window.ceiling_truncated > window.started_trials:
        problems.append(
            "natural completions plus ceiling truncations exceed started trials"
        )
    if (
        window.successful_trials
        + window.endpoint_failures
        + window.ceiling_truncated
        != window.started_trials
    ):
        problems.append(
            "successful, failed and truncated cohorts do not reconcile to started trials"
        )
    for metric, values in window.eligible_observations.items():
        if metric in _COMPLETION_METRICS and len(values) > window.natural_completions:
            problems.append(
                f"{metric} has {len(values)} observations for "
                f"{window.natural_completions} natural completions"
            )
        if metric == "time_to_first_token":
            eligible = max(0, window.started_trials - window.endpoint_failures)
            if len(values) > eligible:
                problems.append(
                    f"time_to_first_token has {len(values)} observations for "
                    f"{eligible} responses that started"
                )
    return problems


def _cohort_identity(window: WindowEvidence) -> tuple[str, str, str, str]:
    return (
        window.workload_id,
        window.client_profile,
        window.deployment_key,
        window.region,
    )


def _identity_problems(windows: list[WindowEvidence]) -> list[str]:
    """Check duplicate window/trial identity and explicit cohort fields."""
    problems: list[str] = []
    window_ids: set[str] = set()
    trial_ids: set[str] = set()
    for window in windows:
        identity = window.window_id or window.label
        if not identity:
            problems.append("a window has no identity")
        elif identity in window_ids:
            raise QualificationError(f"duplicate window identity `{identity}`")
        window_ids.add(identity)
        if not window.trial_ids:
            problems.append(f"window `{window.label}` has no trial identities")
        seen_in_window: set[str] = set()
        for trial_id in window.trial_ids:
            if not trial_id:
                problems.append(f"window `{window.label}` has an empty trial identity")
                continue
            if trial_id in seen_in_window:
                raise QualificationError(
                    f"duplicate trial identity `{trial_id}` within window `{window.label}`"
                )
            if trial_id in trial_ids:
                raise QualificationError(
                    f"duplicate trial identity `{trial_id}` across windows"
                )
            seen_in_window.add(trial_id)
            trial_ids.add(trial_id)
        if len(window.trial_ids) != window.planned_trials:
            problems.append(
                f"window `{window.label}` has {len(window.trial_ids)} trial identities "
                f"for {window.planned_trials} planned trials"
            )
        if not all(_cohort_identity(window)):
            problems.append(f"window `{window.label}` has an incomplete pooling cohort")
    return problems


def _pooling_problems(
    windows: list[WindowEvidence],
    required_levels: set[int],
    objectives: list[dict[str, Any]],
) -> list[str]:
    """Check level coverage and that each level pools one explicit cohort."""
    problems: list[str] = []
    present_levels = {window.concurrency for window in windows}
    all_cohorts = {_cohort_identity(window) for window in windows}
    if len(all_cohorts) > 1:
        problems.append(
            f"windows mix incompatible pooling cohorts across levels: {sorted(all_cohorts)}"
        )
    for level in sorted(required_levels):
        level_windows = [window for window in windows if window.concurrency == level]
        identities = {window.window_id or window.label for window in level_windows}
        if len(identities) < REQUIRED_WINDOWS_PER_LEVEL:
            problems.append(
                f"concurrency level C{level} has {len(identities)} window(s); "
                f"{REQUIRED_WINDOWS_PER_LEVEL} are required"
            )
        cohorts = {_cohort_identity(window) for window in level_windows}
        if len(cohorts) > 1:
            problems.append(
                f"concurrency level C{level} mixes incompatible pooling cohorts: "
                f"{sorted(cohorts)}"
            )
        if not any(level in _levels(objective) for objective in objectives):
            problems.append(
                f"concurrency level C{level} has no applicable performance objective"
            )
    for level in sorted(present_levels - required_levels):
        problems.append(
            f"concurrency level C{level} has no applicable performance objective"
        )
    return problems


def _parse_instant(value: object, field: str) -> datetime:
    """Parse an ISO-8601 instant that must carry an explicit timezone."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=UTC)
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as error:
            raise QualificationError(
                f"{field} is not an ISO-8601 date/time: {value!r}"
            ) from error
    else:
        raise QualificationError(f"{field} must be an ISO-8601 date/time string")
    if parsed.tzinfo is None:
        raise QualificationError(f"{field} must include a timezone: {value!r}")
    return parsed.astimezone(UTC)


def _parse_requirement_date(
    value: object, field: str, *, end_of_day: bool = False
) -> datetime:
    """Parse a required date, treating a bare date as its whole UTC day."""
    if not isinstance(value, str) or not value.strip():
        raise QualificationError(f"{field} must be a date string")
    text = value.strip()
    try:
        parsed_date = date.fromisoformat(text)
    except ValueError:
        return _parse_instant(text, field)
    return datetime.combine(
        parsed_date,
        time.max if end_of_day else time.min,
        tzinfo=UTC,
    )


def _normalize_levels(value: object) -> tuple[int, ...] | None:
    if isinstance(value, bool):
        return None
    if _is_int(value) and value > 0:
        return (value,)
    if (
        isinstance(value, (list, tuple))
        and value
        and all(_is_int(level) and level > 0 for level in value)
    ):
        return tuple(sorted(set(value)))
    return None


def _ratification_problems(
    contract: dict[str, Any],
    windows: list[WindowEvidence],
    evaluated_at: datetime,
) -> list[str]:
    """Require a frozen requirement that applies to this evidence and instant."""
    thresholds = contract.get("performance_thresholds") or {}
    problems: list[str] = []
    if thresholds.get("status") != "ratified":
        problems.append(
            "performance thresholds are unratified "
            f"(status={thresholds.get('status')!r})"
        )
    if thresholds.get("frozen") is not True:
        problems.append("performance thresholds are not frozen")
    frozen_at: datetime | None = None
    frozen_text = thresholds.get("frozen_at_utc")
    if not frozen_text:
        problems.append("performance thresholds have no frozen_at_utc")
    else:
        try:
            frozen_at = _parse_instant(
                frozen_text, "performance_thresholds.frozen_at_utc"
            )
        except QualificationError as error:
            problems.append(str(error))
        else:
            if frozen_at > evaluated_at:
                problems.append(
                    "performance thresholds are frozen after the evaluation instant"
                )

    evidence_scope = {
        "workload": {window.workload_id for window in windows},
        "client_profile": {window.client_profile for window in windows},
        "deployment_key": {window.deployment_key for window in windows},
        "region": {window.region for window in windows},
    }

    for objective in thresholds.get("objectives") or []:
        objective_id = objective.get("id", "<unnamed>")
        if objective.get("threshold") is None:
            problems.append(f"{objective_id}: threshold is null and cannot be met")
            continue
        record = objective.get("ratification")
        if not isinstance(record, dict):
            problems.append(f"{objective_id}: no ratification record")
            continue
        missing = [
            field
            for field in RATIFICATION_REQUIRED_FIELDS
            if not record.get(field)
        ]
        if missing:
            problems.append(
                f"{objective_id}: ratification missing {', '.join(missing)}"
            )

        for field_name in (
            "origin",
            "date",
            "expiry",
            "workload",
            "client_profile",
            "deployment_key",
            "region",
        ):
            value = record.get(field_name)
            if value and not isinstance(value, str):
                problems.append(
                    f"{objective_id}: ratification {field_name} must be a string"
                )

        record_levels = _normalize_levels(record.get("concurrency"))
        objective_levels = _levels(objective)
        if record_levels is None:
            problems.append(f"{objective_id}: ratification concurrency is malformed")
        elif set(record_levels) != objective_levels:
            problems.append(
                f"{objective_id}: ratification concurrency {record_levels} does not "
                f"apply to objective levels {sorted(objective_levels)}"
            )

        for field_name, observed_values in evidence_scope.items():
            record_value = record.get(field_name)
            if not isinstance(record_value, str) or not record_value:
                continue
            if len(observed_values) != 1:
                problems.append(
                    f"{objective_id}: ratification {field_name} cannot apply to "
                    f"mixed evidence scope {sorted(observed_values)}"
                )
                continue
            observed = next(iter(observed_values))
            if record_value != observed:
                problems.append(
                    f"{objective_id}: ratification {field_name} `{record_value}` "
                    f"does not apply to evidence `{observed}`"
                )

        record_date: datetime | None = None
        expiry: datetime | None = None
        if isinstance(record.get("date"), str):
            try:
                record_date = _parse_requirement_date(
                    record["date"], f"{objective_id}.ratification.date"
                )
            except QualificationError as error:
                problems.append(str(error))
        if isinstance(record.get("expiry"), str):
            try:
                expiry = _parse_requirement_date(
                    record["expiry"],
                    f"{objective_id}.ratification.expiry",
                    end_of_day=True,
                )
            except QualificationError as error:
                problems.append(str(error))
        if record_date is not None and expiry is not None:
            if expiry < record_date:
                problems.append(f"{objective_id}: ratification expiry precedes its date")
        if record_date is not None and record_date > evaluated_at:
            problems.append(
                f"{objective_id}: ratification date is after the evaluation instant"
            )
        if expiry is not None and evaluated_at > expiry:
            problems.append(
                f"{objective_id}: ratification expired at {expiry.isoformat()} "
                f"before evaluation at {evaluated_at.isoformat()}"
            )
        if frozen_at is not None:
            if record_date is not None and frozen_at < record_date:
                problems.append(
                    f"{objective_id}: thresholds were frozen before the requirement date"
                )
            if expiry is not None and frozen_at > expiry:
                problems.append(
                    f"{objective_id}: thresholds were frozen after the requirement expiry"
                )
    return problems


def assess_window(window: WindowEvidence, contract: dict[str, Any]) -> dict[str, Any]:
    """Evaluate every data-quality gate for one window.

    A window passes when its evidence is trustworthy. Performance is never a
    window-level question here.
    """
    gates = {gate["id"]: gate for gate in contract["data_quality_gates"]["gates"]}
    failures: list[str] = []

    def fail(gate_id: str, detail: str) -> None:
        if gate_id in gates:
            failures.append(f"{gate_id}: {detail}")

    censor = window.censoring_rate
    if censor is not None and censor > 0:
        fail("dq.truncation_rate", f"censoring rate {censor:.1%} exceeds the contract's limit")

    if window.http_attempts != window.planned_trials:
        fail(
            "dq.request_accounting",
            f"{window.http_attempts} HTTP attempts against {window.planned_trials} planned",
        )

    if window.provider_target_conformance_failures:
        fail(
            "dq.sizing_conformance",
            f"{window.provider_target_conformance_failures} trial(s) outside tolerance",
        )

    if not window.accounting_provenance_consistent:
        fail("dq.accounting_provenance", "accounting provenance inconsistent")

    if not window.schema_validated:
        fail("dq.engine_schema", "engine schema unvalidated")

    if not window.raw_artifacts_present:
        fail("dq.raw_evidence", "raw artifacts absent")

    cohort_failures = _count_problems(window)
    return {
        "window": window.label,
        "window_id": window.window_id or window.label,
        "concurrency": window.concurrency,
        "evidence_role": window.evidence_role,
        "pooling_cohort": {
            "workload_id": window.workload_id,
            "client_profile": window.client_profile,
            "deployment_key": window.deployment_key,
            "region": window.region,
        },
        "gate_failures": failures,
        "cohort_failures": cohort_failures,
        "passes": not failures and not cohort_failures,
        "censoring_rate": censor,
        "error_rate": window.error_rate,
        "successful_trials": window.successful_trials,
        "natural_completions": window.natural_completions,
        "ceiling_truncated": window.ceiling_truncated,
        "endpoint_failures": window.endpoint_failures,
    }


def _required_levels(
    contract: dict[str, Any], objectives: list[dict[str, Any]]
) -> set[int]:
    levels: set[int] = set()
    scope = contract.get("scope") or {}
    for value in scope.get("concurrency_levels") or []:
        if isinstance(value, int) and not isinstance(value, bool):
            levels.add(value)
    for objective in objectives:
        levels.update(_levels(objective))
    return levels


def _level_pooling_identity(windows: list[WindowEvidence]) -> dict[str, Any] | None:
    if not windows:
        return None
    workload, client, deployment, region = _cohort_identity(windows[0])
    return {
        "workload_id": workload,
        "client_profile": client,
        "deployment_key": deployment,
        "region": region,
        "windows": [window.window_id or window.label for window in windows],
    }


def evaluate(
    contract: dict[str, Any],
    windows: list[WindowEvidence],
    *,
    evaluated_at: str | datetime | None = None,
) -> dict[str, Any]:
    """Return a verdict for synthetic evidence under the contract's rules.

    Precedence follows the contract: evidence problems, missing coverage,
    duplicate identity and unratified thresholds all produce `inconclusive`
    before any threshold comparison can produce `qualified`.

    `evaluated_at` makes expiry and freeze chronology reproducible. When it is
    omitted the current UTC instant is used; tests supply a fixed instant.
    """
    if not windows:
        raise QualificationError("no windows supplied; nothing to evaluate")
    evaluation_instant = _parse_instant(
        evaluated_at if evaluated_at is not None else datetime.now(UTC),
        "evaluated_at",
    )
    input_problems = [
        f"window `{window.label}`: {problem}"
        for window in windows
        for problem in _input_problems(window)
    ]
    if input_problems:
        return _verdict(
            VERDICT_INCONCLUSIVE,
            ["invalid window evidence: " + "; ".join(input_problems)],
            [],
            {},
        )

    assessments = [assess_window(window, contract) for window in windows]
    thresholds = contract.get("performance_thresholds") or {}
    objectives = thresholds.get("objectives") or []
    required_levels = _required_levels(contract, objectives)

    excluded = [w.evidence_role for w in windows if w.evidence_role != QUALIFICATION_ROLE]
    if excluded:
        roles = sorted(set(excluded))
        return _verdict(
            VERDICT_INCONCLUSIVE,
            [
                f"evidence role(s) {roles} are excluded from qualification evidence; "
                "pilot, calibration and exploratory results cannot be relabelled"
            ],
            assessments,
            {},
        )

    failed_windows = [
        a["window_id"] for a in assessments if not a["passes"]
    ]
    if failed_windows:
        return _verdict(
            VERDICT_INCONCLUSIVE,
            [f"data-quality gate or cohort failure in window(s) {failed_windows}"],
            assessments,
            {},
        )

    identity_problems = _identity_problems(windows)
    pooling_problems = _pooling_problems(windows, required_levels, objectives)
    ratification_problems = _ratification_problems(
        contract, windows, evaluation_instant
    )

    floor = _percentile_floor(contract)
    results: dict[str, Any] = {}
    missed: list[str] = []
    uncomputable: list[str] = []
    pooling_identity: dict[str, Any] = {}

    for level in sorted(required_levels):
        at_level = [w for w in windows if w.concurrency == level]
        pooling_identity[f"C{level}"] = _level_pooling_identity(at_level)
        for objective in objectives:
            if level not in _levels(objective):
                continue
            metric = objective["metric"]
            values: list[float] = []
            for window in at_level:
                values.extend(window.eligible_observations.get(metric, []))
            entry: dict[str, Any] = {
                "objective": objective["id"],
                "concurrency": level,
                "metric": metric,
                "statistic": objective["statistic"],
                "threshold": objective["threshold"],
                "pooled_sample_size": len(values),
                "windows_pooled": [
                    window.window_id or window.label for window in at_level
                ],
                "pooling_identity": pooling_identity[f"C{level}"],
            }

            if not at_level:
                entry["state"] = "no_windows"
                uncomputable.append(objective["id"])
                results[f"{objective['id']}@C{level}"] = entry
                continue

            if objective.get("threshold") is None:
                entry["state"] = "threshold_unratified"
                uncomputable.append(objective["id"])
                results[f"{objective['id']}@C{level}"] = entry
                continue

            # Ratio objectives divide two counts rather than sampling per-trial
            # values, so they neither need nor invent an observation list.
            if objective["statistic"] == "fraction":
                value = statistics_fraction(at_level, objective)
                denominator = sum(w.started_trials for w in at_level)
                entry.update(
                    {
                        "state": "evaluated",
                        "value": value,
                        "estimator": "count_ratio",
                        "denominator": denominator,
                    }
                )
                entry["meets_threshold"] = _meets(
                    value, objective["operator"], objective["threshold"]
                )
                if not entry["meets_threshold"]:
                    missed.append(f"{objective['id']}@C{level}")
                results[f"{objective['id']}@C{level}"] = entry
                continue

            if not values:
                # No invented number: an empty eligible population yields no statistic.
                entry["state"] = "no_eligible_observations"
                uncomputable.append(objective["id"])
                results[f"{objective['id']}@C{level}"] = entry
                continue
            if objective["statistic"] in ("p50", "p95") and len(values) < floor:
                entry["state"] = "pooled_sample_below_floor"
                entry["required_samples"] = floor
                uncomputable.append(objective["id"])
                results[f"{objective['id']}@C{level}"] = entry
                continue
            value = statistic(values, objective["statistic"])
            entry.update(
                {
                    "state": "evaluated",
                    "value": value,
                    "estimator": (
                        PERCENTILE_ESTIMATOR
                        if objective["statistic"] in ("p50", "p95")
                        else objective["statistic"]
                    ),
                    "denominator": len(values),
                }
            )
            entry["meets_threshold"] = _meets(
                value, objective["operator"], objective["threshold"]
            )
            if not entry["meets_threshold"]:
                missed.append(f"{objective['id']}@C{level}")
            results[f"{objective['id']}@C{level}"] = entry

    coverage_problems = identity_problems + pooling_problems
    blocking = ratification_problems + coverage_problems
    if blocking:
        return _verdict(
            VERDICT_INCONCLUSIVE,
            blocking
            + [
                "no qualified verdict is possible until every required cohort and "
                "ratified, frozen threshold is present"
            ],
            assessments,
            results,
            pooling_identity,
        )

    if uncomputable:
        return _verdict(
            VERDICT_INCONCLUSIVE,
            [
                "objective(s) could not be evaluated on the available evidence: "
                + ", ".join(sorted(set(uncomputable)))
                + "; no statistic is invented for an empty or undersized sample"
            ],
            assessments,
            results,
            pooling_identity,
        )

    if missed:
        return _verdict(
            VERDICT_FAILED,
            [f"threshold(s) missed: {', '.join(missed)}"],
            assessments,
            results,
            pooling_identity,
        )

    return _verdict(
        VERDICT_QUALIFIED,
        ["all gates passed, every required cohort is present and all thresholds are met"],
        assessments,
        results,
        pooling_identity,
    )


def statistics_fraction(windows: list[WindowEvidence], objective: dict[str, Any]) -> float:
    """Fraction for ratio objectives.

    Dispatched on the objective id, because the contract states this metric as a
    formula (`provider_or_protocol_failures / started_trials`) rather than as a
    metric key. Matching on the formula string would silently break whenever the
    wording changed.
    """
    if objective["id"] == "perf.endpoint_error_rate":
        failures = sum(w.endpoint_failures for w in windows)
        started = sum(w.started_trials for w in windows)
        if started <= 0:
            raise QualificationError("endpoint error rate requires started trials")
        return failures / started
    raise QualificationError(
        f"objective `{objective['id']}` declares a fraction but has no count-ratio implementation"
    )


def _levels(objective: dict[str, Any]) -> set[int]:
    concurrency = objective.get("concurrency")
    if isinstance(concurrency, (list, tuple)):
        return {int(value) for value in concurrency}
    if _is_int(concurrency):
        return {concurrency}
    return {1, 2}


def _percentile_floor(contract: dict[str, Any]) -> int:
    for gate in contract["data_quality_gates"]["gates"]:
        if gate["id"] == "dq.percentile_samples":
            return int(gate["threshold"])
    raise QualificationError("contract has no percentile sample floor gate")


def _meets(value: float, operator: str, threshold: float) -> bool:
    if operator == "<=":
        return value <= threshold
    if operator == ">=":
        return value >= threshold
    if operator == "==":
        return value == threshold
    raise QualificationError(f"operator `{operator}` is not supported")


def _verdict(
    verdict: str,
    reasons: list[str],
    assessments: list[dict[str, Any]],
    objectives: dict[str, Any],
    pooling_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "verdict": verdict,
        "reasons": reasons,
        "windows": assessments,
        "objectives": objectives,
        "pooling_identity": pooling_identity or {},
        "required_windows_per_level": REQUIRED_WINDOWS_PER_LEVEL,
        "evaluator": "synthetic_only_no_live_pipeline",
    }
