"""Pool per-trial latency across windows and report bounded statistics.

Each run is supplied as `LEVEL:PATH`, for example:

    uv run python scripts/analyze_bounded.py \
      --run 1:runs/run-aaa-w1-c1 --run 2:runs/run-aaa-w1-c2 ...

A natural completion contributes its observed latency. A ceiling-truncated
completion contributes its cap-hit time as a per-trial lower bound. The output
is explicitly a bound: it can prove an upper-bound objective is missed, but it
cannot prove one is met.

This script is fail-closed about identity. Before it computes a statistic it
verifies that:

- every run directory is distinct after path resolution;
- every run's declared concurrency equals the concurrency encoded in its
  AIPerf configuration and the caller's `LEVEL:` label;
- the trial file hash matches the report that normalized it;
- all pooled runs share deployment, model, protocol, client configuration,
  workload kind, and requested input/output sizes.

A run supplied twice used to be accepted as a 40-trial sample. That would double
every denominator and silently narrow the bound, so it is now rejected.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from endpoint_bench.bounds import (
    LatencySample,
    evaluate_upper_bound,
    samples_from_trial_records,
)
from endpoint_bench.qualification import QualificationError


class AnalysisInputError(ValueError):
    """Raised when supplied evidence cannot be pooled without invalid inference."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AnalysisInputError(f"could not read `{path}`: {error}") from error
    if not isinstance(document, dict):
        raise AnalysisInputError(f"`{path}` must contain a JSON object")
    return document


def _load_trials(path: Path) -> list[dict[str, Any]]:
    trials_path = path / "trials.jsonl"
    if not trials_path.is_file():
        raise AnalysisInputError(f"missing trials.jsonl in {path}")
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        trials_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise AnalysisInputError(
                f"{trials_path}: line {line_number} is not valid JSON: {error}"
            ) from error
        if not isinstance(record, dict):
            raise AnalysisInputError(f"{trials_path}: line {line_number} is not an object")
        records.append(record)
    return records


def _phase_value(config_path: Path, key: str) -> int:
    """Read a scalar from the `phases:` block of the generated AIPerf YAML.

    The repository deliberately has no YAML dependency for offline analysis. The
    generated configuration has a fixed two-space shape, so an indentation-aware
    scan is sufficient and stricter than a global regex: it cannot match a
    same-named key outside the phases block.
    """
    if not config_path.is_file():
        raise AnalysisInputError(f"missing AIPerf configuration: {config_path}")
    lines = config_path.read_text(encoding="utf-8").splitlines()
    in_phases = False
    for line in lines:
        if not in_phases:
            if line.strip() == "phases:":
                in_phases = True
            continue
        if line and not line.startswith(" "):
            break
        match = re.fullmatch(rf"\s+{re.escape(key)}:\s*(\d+)\s*", line)
        if match:
            return int(match.group(1))
    raise AnalysisInputError(f"`{config_path}` has no integer phases.{key}")


@dataclass(frozen=True)
class RunEvidence:
    level: int
    path: Path
    run_id: str
    deployment_key: str
    model_id: str
    protocol: str
    client_identity: str
    client_name: str
    affinity: bool
    request_timeout_s: float
    workload_type: str
    profile_name: str
    requested_input_tokens: int
    requested_max_output_tokens: int
    planned_requests: int
    concurrency: int
    trials: list[dict[str, Any]]
    trials_sha256: str

    @property
    def pooling_identity(self) -> dict[str, Any]:
        return {
            "deployment_key": self.deployment_key,
            "model_id": self.model_id,
            "protocol": self.protocol,
            "client_identity": self.client_identity,
            "client_name": self.client_name,
            "affinity": self.affinity,
            "request_timeout_s": self.request_timeout_s,
            "workload_type": self.workload_type,
            "profile_name": self.profile_name,
            "requested_input_tokens": self.requested_input_tokens,
            "requested_max_output_tokens": self.requested_max_output_tokens,
        }


def _required(mapping: dict[str, Any], key: str, context: str) -> Any:
    value = mapping.get(key)
    if value is None:
        raise AnalysisInputError(f"{context}: missing `{key}`")
    return value


def load_run(level: int, path: Path, *, resolve: bool = True) -> RunEvidence:
    """Load and validate one run before it can enter a pooled sample."""
    resolved = path.resolve() if resolve else path
    report_path = resolved / "report.json"
    if not report_path.is_file():
        raise AnalysisInputError(
            f"{resolved}: missing report.json; identity and configuration cannot be "
            "verified, so the run is not poolable"
        )
    report = _load_json(report_path)
    context = str(report_path)

    deployment = _required(report, "provenance", context).get("deployment")
    if not isinstance(deployment, dict):
        raise AnalysisInputError(f"{context}: provenance.deployment must be an object")
    profile = _required(report["provenance"], "profile", context)
    if not isinstance(profile, dict):
        raise AnalysisInputError(f"{context}: provenance.profile must be an object")
    client = _required(report, "client_profile", context)
    if not isinstance(client, dict):
        raise AnalysisInputError(f"{context}: client_profile must be an object")
    provenance = report["provenance"]
    workload_type = provenance.get("workload_type")
    if not isinstance(workload_type, str) or not workload_type:
        raise AnalysisInputError(
            f"{context}: provenance.workload_type is missing; replay and live-turn "
            "workloads are not interchangeable"
        )
    affinity = provenance.get("affinity") or {}
    if not isinstance(affinity, dict):
        raise AnalysisInputError(f"{context}: provenance.affinity must be an object")

    run_id = _required(report, "run_id", context)
    if not isinstance(run_id, str) or not run_id:
        raise AnalysisInputError(f"{context}: run_id must be a non-empty string")
    if run_id != resolved.name:
        raise AnalysisInputError(
            f"{context}: report run_id `{run_id}` does not match directory `{resolved.name}`"
        )

    config_concurrency = _phase_value(resolved / "aiperf-config.yaml", "concurrency")
    if config_concurrency != level:
        raise AnalysisInputError(
            f"{resolved}: caller supplied concurrency {level}, but the run configuration "
            f"records {config_concurrency}"
        )
    planned_requests = _phase_value(resolved / "aiperf-config.yaml", "requests")
    declared_planned = profile.get("planned_requests")
    if declared_planned != planned_requests:
        raise AnalysisInputError(
            f"{resolved}: report declares {declared_planned} planned requests, "
            f"configuration records {planned_requests}"
        )
    counters = _required(report, "counters", context)
    if not isinstance(counters, dict):
        raise AnalysisInputError(f"{context}: counters must be an object")
    if counters.get("planned_trials") != planned_requests:
        raise AnalysisInputError(
            f"{resolved}: counters.planned_trials {counters.get('planned_trials')} does not "
            f"match configured requests {planned_requests}"
        )

    trials_path = resolved / "trials.jsonl"
    if not trials_path.is_file():
        raise AnalysisInputError(f"{resolved}: missing trials.jsonl")
    declared_trials_sha = report.get("trials_sha256")
    if not isinstance(declared_trials_sha, str) or not declared_trials_sha:
        raise AnalysisInputError(
            f"{context}: report has no trials_sha256; normalized evidence is unverified"
        )
    actual_trials_sha = _sha256(trials_path)
    if actual_trials_sha != declared_trials_sha:
        raise AnalysisInputError(
            f"{resolved}: trials.jsonl SHA-256 {actual_trials_sha} does not match the "
            f"report's declared {declared_trials_sha}"
        )

    trials = _load_trials(resolved)
    seen_ids: set[str] = set()
    for index, trial in enumerate(trials):
        trial_id = trial.get("trial_id")
        if not isinstance(trial_id, str) or not trial_id:
            raise AnalysisInputError(
                f"{resolved}: trial record {index} has no trial_id; request identity is "
                "not established"
            )
        if trial_id in seen_ids:
            raise AnalysisInputError(
                f"{resolved}: duplicate trial identity `{trial_id}`"
            )
        seen_ids.add(trial_id)
    if len(trials) != planned_requests:
        raise AnalysisInputError(
            f"{resolved}: {len(trials)} trial records for {planned_requests} configured requests"
        )
    if counters.get("http_attempts") != sum(
        len(trial.get("attempts") or []) for trial in trials
    ):
        raise AnalysisInputError(
            f"{resolved}: counters.http_attempts does not equal the sum of recorded attempts"
        )

    return RunEvidence(
        level=level,
        path=resolved,
        run_id=run_id,
        deployment_key=str(_required(deployment, "deployment_key", context)),
        model_id=str(_required(deployment, "model_id", context)),
        protocol=str(_required(deployment, "protocol", context)),
        client_identity=str(_required(client, "identity", context)),
        client_name=str(_required(client, "name", context)),
        affinity=bool(client.get("session_affinity")),
        request_timeout_s=float(_required(client, "request_timeout_s", context)),
        workload_type=workload_type,
        profile_name=str(_required(profile, "name", context)),
        requested_input_tokens=int(_required(profile, "requested_input_tokens", context)),
        requested_max_output_tokens=int(
            _required(profile, "requested_max_output_tokens", context)
        ),
        planned_requests=planned_requests,
        concurrency=config_concurrency,
        trials=trials,
        trials_sha256=actual_trials_sha,
    )


def _summary(sample: LatencySample, totals: dict[str, int], runs: list[str]) -> dict[str, Any]:
    def entry(statistic: str) -> dict[str, Any]:
        return evaluate_upper_bound(
            sample,
            metric="request_latency",
            statistic=statistic,
            threshold=None,
        ).to_dict()

    return {
        "runs": runs,
        "sample_size": sample.sample_size,
        "natural_completions": totals["natural"],
        "ceiling_truncated": totals["censored"],
        "failed_trials": totals["failed"],
        "excluded_trials": totals["excluded"],
        "records_total": totals["records"],
        "censoring_fraction": sample.censoring_fraction,
        "request_latency": {
            "p50": entry("p50"),
            "p95": entry("p95"),
            "mean": entry("mean"),
        },
    }


def _check_pooling(runs: list[RunEvidence]) -> dict[str, Any]:
    if not runs:
        raise AnalysisInputError("no runs supplied")
    identity = runs[0].pooling_identity
    differing: list[dict[str, Any]] = []
    for run in runs[1:]:
        for key, value in identity.items():
            if run.pooling_identity[key] != value:
                differing.append(
                    {
                        "run": run.run_id,
                        "field": key,
                        "expected": value,
                        "observed": run.pooling_identity[key],
                    }
                )
    if differing:
        details = "; ".join(
            f"{item['run']}.{item['field']}={item['observed']!r} != {item['expected']!r}"
            for item in differing
        )
        raise AnalysisInputError(
            "runs are not poolable as one measurement identity: " + details
        )
    return identity


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run",
        action="append",
        required=True,
        metavar="LEVEL:PATH",
        help="concurrency level and run directory, repeatable",
    )
    parser.add_argument("--out", type=Path, default=None, help="write JSON here instead of stdout")
    parser.add_argument(
        "--require-level",
        type=int,
        action="append",
        default=None,
        help="fail unless this concurrency level is present; repeatable",
    )
    parser.add_argument(
        "--require-windows",
        type=int,
        default=None,
        help=(
            "fail unless every level has window labels 1..N exactly once; "
            "window labels are parsed from run IDs like `-w2-`"
        ),
    )
    arguments = parser.parse_args(argv)

    by_level: dict[int, list[RunEvidence]] = {}
    seen_paths: set[Path] = set()
    seen_run_ids: set[str] = set()
    try:
        for item in arguments.run:
            level_text, separator, path_text = item.partition(":")
            if not separator or not level_text.isdigit() or not path_text:
                parser.error(f"expected LEVEL:PATH, got {item!r}")
            level = int(level_text)
            if level <= 0:
                parser.error(f"concurrency level must be positive, got {item!r}")
            run = load_run(level, Path(path_text))
            if run.path in seen_paths:
                raise AnalysisInputError(
                    f"run directory `{run.path}` was supplied more than once; "
                    "duplicate evidence would inflate every denominator"
                )
            if run.run_id in seen_run_ids:
                raise AnalysisInputError(
                    f"run identity `{run.run_id}` was supplied more than once"
                )
            seen_paths.add(run.path)
            seen_run_ids.add(run.run_id)
            by_level.setdefault(level, []).append(run)

        required_levels = sorted(set(arguments.require_level or []))
        missing_levels = [level for level in required_levels if level not in by_level]
        if missing_levels:
            raise AnalysisInputError(
                f"required concurrency levels have no runs: {missing_levels}"
            )
        coverage_by_level: dict[str, Any] = {}
        for level, runs in sorted(by_level.items()):
            labels: list[int] = []
            for run in runs:
                match = re.search(r"-w(\d+)-", run.run_id)
                if match:
                    labels.append(int(match.group(1)))
            labels_sorted = sorted(labels)
            duplicates = sorted(
                value for value in set(labels_sorted) if labels_sorted.count(value) > 1
            )
            missing_windows = (
                [value for value in range(1, arguments.require_windows + 1) if value not in labels_sorted]
                if arguments.require_windows
                else []
            )
            if arguments.require_windows and (missing_windows or duplicates):
                raise AnalysisInputError(
                    f"level {level}: required windows 1..{arguments.require_windows} are "
                    f"incomplete; observed={labels_sorted}, missing={missing_windows}, "
                    f"duplicates={duplicates}. Window labels are parsed from run IDs."
                )
            coverage_by_level[str(level)] = {
                "run_count": len(runs),
                "windows_observed": labels_sorted,
                "window_labels_available": bool(labels),
                "missing_windows": missing_windows,
                "duplicate_windows": duplicates,
            }

        levels: dict[str, Any] = {}
        for level, runs in sorted(by_level.items()):
            identity = _check_pooling(runs)
            pooled_latency: list[tuple[float, bool]] = []
            pooled_ttft: list[tuple[float, bool]] = []
            totals = {"natural": 0, "censored": 0, "failed": 0, "excluded": 0, "records": 0}
            per_window: list[dict[str, Any]] = []
            for run in runs:
                samples = samples_from_trial_records(run.trials)
                latency, ttft = samples.latency, samples.ttft
                counts = {
                    "natural": samples.natural,
                    "censored": samples.censored,
                    "failed": samples.failed,
                    "excluded": samples.excluded,
                    "records": samples.records_total,
                }
                pooled_latency.extend(zip(latency.values_ms, latency.censored, strict=True))
                pooled_ttft.extend(zip(ttft.values_ms, ttft.censored, strict=True))
                for key in totals:
                    totals[key] += counts[key]
                per_window.append(
                    {
                        "run_id": run.run_id,
                        "path": str(run.path),
                        "trials_sha256": run.trials_sha256,
                        **samples.to_dict(),
                        "censoring_fraction": latency.censoring_fraction,
                    }
                )

            sample = LatencySample(
                tuple(value for value, _ in pooled_latency),
                tuple(flag for _, flag in pooled_latency),
            )
            summary = _summary(sample, totals, [run.run_id for run in runs])
            summary["identity"] = identity
            summary["denominators"] = {
                "planned_requests": sum(run.planned_requests for run in runs),
                "records_total": totals["records"],
                "natural_completions": totals["natural"],
                "ceiling_truncated": totals["censored"],
                "failed_trials": totals["failed"],
                "excluded_trials": totals["excluded"],
                "latency_sample_size": sample.sample_size,
                "ttft_sample_size": len(pooled_ttft),
                "partition_consistent": (
                    totals["natural"] + totals["censored"] + totals["failed"]
                    + totals["excluded"]
                    == totals["records"]
                ),
            }
            summary["windows"] = per_window
            ttft_sample = LatencySample(
                tuple(value for value, _ in pooled_ttft),
                tuple(flag for _, flag in pooled_ttft),
            )
            summary["time_to_first_token"] = {
                "p50": evaluate_upper_bound(
                    ttft_sample, metric="time_to_first_token", statistic="p50", threshold=None
                ).to_dict(),
                "p95": evaluate_upper_bound(
                    ttft_sample, metric="time_to_first_token", statistic="p95", threshold=None
                ).to_dict(),
            }
            levels[str(level)] = summary
    except (AnalysisInputError, QualificationError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    document = {
        "analysis": "bounded_completion_latency",
        "schema_version": "1.1.0",
        "estimator": "lower_bounds_pooled_across_windows",
        "interpretation": (
            "Censored trials contribute cap-hit times as per-trial lower bounds. "
            "A lower bound above an upper-bound threshold proves failure; a lower "
            "bound below it stays inconclusive. No threshold is ratified here. "
            "These are finite-sample observations, not a population SLO or a "
            "universal capacity conclusion."
        ),
        "coverage": {
            "levels_observed": sorted(by_level),
            "required_levels": required_levels,
            "require_windows": arguments.require_windows,
            "by_level": coverage_by_level,
            "window_labels_available": all(
                item["window_labels_available"] for item in coverage_by_level.values()
            ),
        },
        "objectives": {
            "thresholds_ratified": False,
            "objectives": [],
            "note": (
                "No performance threshold or objective is supplied by this analysis. "
                "A bound can prove a supplied upper-bound objective is missed; it "
                "cannot invent or ratify one."
            ),
        },
        "levels": levels,
    }
    text = json.dumps(document, indent=2, sort_keys=True) + "\n"
    if arguments.out:
        arguments.out.parent.mkdir(parents=True, exist_ok=True)
        arguments.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
