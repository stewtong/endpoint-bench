"""Re-derive a run's normalized evidence from its original artifacts.

This exists so a harness defect can be corrected without re-spending money and
without overwriting the evidence that was published at the time. It reads a
completed run directory, re-parses the AIPerf artifacts with current logic, and
writes a new directory containing a corrected report plus an explicit record of
which fields changed, which did not, and which cannot be recovered at all.

Two rules govern everything here:

1. The source run directory is read-only. Nothing is written into it, and the
   output directory may not be the source directory or a path inside it.
2. A corrected field is only reported when the raw artifacts actually support
   it. Fields whose support never existed — a client tokenization that never ran,
   a provider counter that was never sent — are listed as unrecoverable rather
   than recomputed into something plausible.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import __version__
from .aiperf import (
    RAW_RECORDS_FILE,
    RECORDS_FILE,
    SUMMARY_FILE,
    ClientProfile,
    parse_artifacts,
)
from .report import REPORT_SCHEMA_VERSION, ReportError, build_report
from .trials import (
    ACCOUNTING_CLIENT_TOKENIZER,
    ACCOUNTING_PROVIDER_USAGE,
    Trial,
    completion_summary,
    provider_telemetry_summary,
    token_sizing_summary,
)

CORRECTION_SCHEMA_VERSION = "1.0"

#: Per-trial fields compared between the original and corrected evidence.
TRIAL_COMPARISON_FIELDS = (
    "outcome",
    "outcome_detail",
    "attribution",
    "finish_reason",
    "finish_class",
    "http_status",
    "sizing_verified",
    "primary_check",
    "provider_target_conformance",
    "client_provider_agreement",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
)


class ReprocessError(ValueError):
    """Raised when a run cannot be reprocessed from its own artifacts."""


@dataclass(frozen=True)
class SourceRun:
    """What a completed run directory must supply to be reprocessed."""

    run_dir: Path
    report: dict[str, Any]
    original_trials: dict[str, dict[str, Any]]


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_source(run_dir: Path) -> SourceRun:
    if not run_dir.is_dir():
        raise ReprocessError(f"run directory does not exist: {run_dir}")
    report_path = run_dir / "report.json"
    if not report_path.is_file():
        raise ReprocessError(
            f"`{run_dir}` has no report.json; only completed runs can be reprocessed"
        )
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ReprocessError(f"`{report_path}` is not valid JSON: {error}") from error

    trials_path = run_dir / "trials.jsonl"
    original: dict[str, dict[str, Any]] = {}
    if trials_path.is_file():
        for line in trials_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise ReprocessError(
                    f"`{trials_path}` is not valid JSONL: {error}"
                ) from error
            if isinstance(record, dict) and isinstance(record.get("trial_id"), str):
                original[record["trial_id"]] = record
    return SourceRun(run_dir=run_dir, report=report, original_trials=original)


def _client_from_report(report: dict[str, Any]) -> ClientProfile:
    stored = report.get("client_profile")
    if not isinstance(stored, dict):
        raise ReprocessError(
            "report has no client_profile block, so the token accounting source is unknown"
        )
    try:
        return ClientProfile(
            name=str(stored["name"]),
            session_affinity=bool(stored["session_affinity"]),
            connection_reuse=str(stored.get("connection_reuse", "pooled")),
            request_timeout_s=float(stored.get("request_timeout_s", 60.0)),
            tokenizer=str(stored.get("tokenizer", "builtin")),
            random_seed=int(stored.get("random_seed", 42)),
            enable_server_metrics=bool(stored.get("enable_server_metrics", False)),
            enable_gpu_telemetry=bool(stored.get("enable_gpu_telemetry", False)),
            wait_for_model_timeout_s=float(stored.get("wait_for_model_timeout_s", 0.0)),
            use_server_token_count=bool(stored.get("use_server_token_count", False)),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ReprocessError(f"report client_profile is not usable: {error}") from error


def _accounting_source(client: ClientProfile) -> str:
    return (
        ACCOUNTING_PROVIDER_USAGE
        if client.use_server_token_count
        else ACCOUNTING_CLIENT_TOKENIZER
    )


def _original_target_and_tolerance(
    original_trials: dict[str, dict[str, Any]],
) -> tuple[int | None, float]:
    """Recover the requested workload size from the evidence that recorded it.

    The original trial records carry the target and tolerance that the run used,
    so a reprocess does not need the profile file to still exist.
    """
    target: int | None = None
    tolerance = 10.0
    for record in original_trials.values():
        accounting = record.get("token_accounting") or {}
        candidate = accounting.get("target_input_tokens")
        if target is None and isinstance(candidate, int) and not isinstance(candidate, bool):
            target = candidate
        value = accounting.get("tolerance_pct")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            tolerance = float(value)
    return target, tolerance


def _trial_view(trial: Trial) -> dict[str, Any]:
    accounting = trial.token_accounting
    telemetry = trial.provider_telemetry
    return {
        "outcome": trial.outcome.value,
        "outcome_detail": trial.outcome_detail,
        "attribution": trial.attribution,
        "finish_reason": trial.finish_reason,
        "finish_class": trial.finish_class,
        "http_status": trial.observed_http_status,
        "sizing_verified": accounting.sizing_verified,
        "primary_check": accounting.primary_check,
        "provider_target_conformance": accounting.provider_target_conformance,
        "client_provider_agreement": accounting.client_provider_agreement,
        "cache_read_tokens": telemetry.cache_read_tokens,
        "cache_write_tokens": telemetry.cache_write_tokens,
        "reasoning_tokens": telemetry.reasoning_tokens,
    }


def _original_view(record: dict[str, Any]) -> dict[str, Any]:
    """Normalize a legacy trial record into the comparison shape."""
    accounting = record.get("token_accounting") or {}
    attempts = record.get("attempts") or []
    status = None
    for attempt in reversed(attempts):
        if isinstance(attempt, dict) and attempt.get("http_status") is not None:
            status = attempt["http_status"]
            break
    # Schema 0.1.0 exposed a single `sizing_verified` derived from the
    # client-versus-provider difference regardless of accounting source.
    return {
        "outcome": record.get("outcome"),
        "outcome_detail": record.get("outcome_detail"),
        "attribution": record.get("attribution"),
        "finish_reason": record.get("finish_reason"),
        "finish_class": None,
        "http_status": status,
        "sizing_verified": accounting.get("sizing_verified"),
        "primary_check": None,
        "provider_target_conformance": None,
        "client_provider_agreement": None,
        "cache_read_tokens": None,
        "cache_write_tokens": None,
        "reasoning_tokens": None,
    }


def _diff_trials(
    original: dict[str, dict[str, Any]], corrected: list[Trial]
) -> tuple[dict[str, dict[str, int]], list[dict[str, Any]]]:
    """Per-field change counts plus a bounded sample of concrete changes."""
    counts: dict[str, dict[str, int]] = {
        field: {"changed": 0, "unchanged": 0, "new_trial": 0} for field in TRIAL_COMPARISON_FIELDS
    }
    samples: list[dict[str, Any]] = []
    for trial in corrected:
        before = original.get(trial.trial_id)
        after = _trial_view(trial)
        if before is None:
            for field in TRIAL_COMPARISON_FIELDS:
                counts[field]["new_trial"] += 1
            continue
        prior = _original_view(before)
        for field in TRIAL_COMPARISON_FIELDS:
            if prior.get(field) == after.get(field):
                counts[field]["unchanged"] += 1
            else:
                counts[field]["changed"] += 1
                if len(samples) < 12:
                    samples.append(
                        {
                            "trial_id": trial.trial_id,
                            "field": field,
                            "before": prior.get(field),
                            "after": after.get(field),
                        }
                    )
    return counts, samples


def _unrecoverable_fields(
    *,
    client: ClientProfile,
    corrected: list[Trial],
    artifact_dir: Path,
) -> list[dict[str, str]]:
    """Fields the original evidence cannot support, with the reason.

    These are not failures of the reprocess. They are observations the original
    run never made, and inventing them would be fabrication.
    """
    gaps: list[dict[str, str]] = []
    if client.use_server_token_count:
        gaps.append(
            {
                "field": "client_estimated_input_tokens",
                "reason": (
                    "the original run used provider token accounting, so AIPerf never "
                    "applied a client tokenizer. No independent client count exists in "
                    "the artifacts and none can be reconstructed without re-running "
                    "the workload with a client tokenizer."
                ),
            }
        )
        gaps.append(
            {
                "field": "client_vs_provider agreement verdict",
                "reason": (
                    "requires the absent client count above, so this run can never "
                    "support a tokenizer-mismatch claim in either direction."
                ),
            }
        )
    if not (artifact_dir / RAW_RECORDS_FILE).is_file():
        gaps.append(
            {
                "field": "http_status, finish_reason, cache_write_tokens",
                "reason": "the raw export was not captured, and no other artifact records these",
            }
        )
        return gaps

    missing_finish = sum(1 for trial in corrected if trial.finish_reason is None)
    if missing_finish:
        gaps.append(
            {
                "field": "finish_reason",
                "reason": (
                    f"{missing_finish} trial(s) carried no terminal finish_reason in the "
                    "raw stream, so natural completion is unverifiable for them"
                ),
            }
        )
    missing_cache_write = sum(
        1 for trial in corrected if trial.provider_telemetry.cache_write_tokens is None
    )
    if missing_cache_write:
        gaps.append(
            {
                "field": "cache_write_tokens",
                "reason": (
                    f"{missing_cache_write} trial(s) carried no "
                    "`prompt_tokens_details.created_cache_tokens` in the provider usage, "
                    "so cache-write is unobserved for them rather than zero"
                ),
            }
        )
    return gaps


def _aggregate_diff(
    original_report: dict[str, Any],
    corrected: dict[str, Any],
) -> list[dict[str, Any]]:
    """Aggregate-level changes between the stored report and the correction."""

    def pick(source: dict[str, Any], path: str) -> Any:
        current: Any = source
        for part in path.split("."):
            if not isinstance(current, dict):
                return None
            current = current.get(part)
        return current

    comparisons = (
        ("counters.successful_trials", "counters.successful_trials"),
        ("counters.failed_trials", "counters.failed_trials"),
        ("counters.truncated_trials", "counters.truncated_trials"),
        ("token_accounting.claims_supported", "token_accounting.claims_supported"),
        ("token_accounting.within_tolerance", "token_accounting.within_tolerance"),
        ("token_accounting.outside_tolerance", "token_accounting.outside_tolerance"),
        ("token_accounting.unverifiable", "token_accounting.unverifiable"),
    )
    changes: list[dict[str, Any]] = []
    for label, path in comparisons:
        before = pick(original_report, path)
        after = pick(corrected, path)
        if before != after:
            changes.append({"field": label, "before": before, "after": after})
    return changes


def reprocess_run(run_dir: Path, out_dir: Path) -> dict[str, Any]:
    """Re-derive one run's evidence into `out_dir` and return the correction record."""
    source = _load_source(run_dir)
    artifact_dir = run_dir / "aiperf"
    if not artifact_dir.is_dir():
        raise ReprocessError(f"`{artifact_dir}` does not exist; nothing to reprocess")

    resolved_source = run_dir.resolve()
    resolved_out = out_dir.resolve()
    if resolved_out == resolved_source or resolved_source in resolved_out.parents:
        raise ReprocessError(
            "refusing to write inside the source run directory; original evidence is immutable"
        )
    if out_dir.exists() and any(out_dir.iterdir()):
        raise ReprocessError(f"output directory is not empty: {out_dir}")

    client = _client_from_report(source.report)
    accounting_source = _accounting_source(client)
    target, tolerance = _original_target_and_tolerance(source.original_trials)

    try:
        results = parse_artifacts(
            artifact_dir,
            deployment_key=str(source.report.get("deployment_key") or "unknown"),
            profile_name=str(source.report.get("profile_name") or "unknown"),
            target_input_tokens=target,
            token_tolerance_pct=tolerance,
            accounting_source=accounting_source,
        )
    except Exception as error:  # noqa: BLE001 - surfaced as a reprocess failure
        raise ReprocessError(f"could not re-parse artifacts: {error}") from error

    provenance = dict(source.report.get("provenance") or {})
    provenance["reprocessed"] = {
        "reprocessor_version": __version__,
        "source_run_id": source.report.get("run_id"),
        "source_report_schema_version": source.report.get("schema_version"),
        "source_artifacts_read_only": True,
        "recovered_from": [
            name
            for name in (SUMMARY_FILE, RECORDS_FILE, RAW_RECORDS_FILE)
            if (artifact_dir / name).is_file()
        ],
    }
    provenance["profile"] = {
        **(provenance.get("profile") or {}),
        "requested_input_tokens": target,
    }

    corrected = build_report(
        run_id=str(source.report.get("run_id") or run_dir.name),
        deployment_key=str(source.report.get("deployment_key") or "unknown"),
        profile_name=str(source.report.get("profile_name") or "unknown"),
        client_profile=client,
        results=results,
        trials=results.trials,
        provenance=provenance,
    )

    trial_counts, trial_samples = _diff_trials(source.original_trials, results.trials)
    aggregate = _aggregate_diff(source.report, corrected)
    unrecoverable = _unrecoverable_fields(
        client=client, corrected=results.trials, artifact_dir=artifact_dir
    )

    corrected["reprocessed_from"] = {
        "reprocessor_version": __version__,
        "source_run_id": source.report.get("run_id"),
        "source_report_schema_version": source.report.get("schema_version"),
        "corrected_report_schema_version": REPORT_SCHEMA_VERSION,
        "source_artifact_sha256": {
            name: _sha256(artifact_dir / name)
            for name in (SUMMARY_FILE, RECORDS_FILE, RAW_RECORDS_FILE)
        },
        "source_config_sha256": _sha256(run_dir / "aiperf-config.yaml"),
        "source_trials_sha256": _sha256(run_dir / "trials.jsonl"),
    }
    corrected["reprocess"] = {
        "per_trial_field_changes": trial_counts,
        "aggregate_field_changes": aggregate,
        "unrecoverable": unrecoverable,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    correction = {
        "schema_version": CORRECTION_SCHEMA_VERSION,
        "correction_type": "artifact_reprocess",
        "reprocessor_version": __version__,
        "source_run_id": source.report.get("run_id"),
        "source_run_dir_name": run_dir.name,
        "source_report_schema_version": source.report.get("schema_version"),
        "corrected_report_schema_version": REPORT_SCHEMA_VERSION,
        "accounting_source": accounting_source,
        "requested_input_tokens": target,
        "tolerance_pct": tolerance,
        "source_artifact_sha256": corrected["reprocessed_from"]["source_artifact_sha256"],
        "engine": corrected.get("engine"),
        "counters_before": source.report.get("counters"),
        "counters_after": corrected.get("counters"),
        "aggregate_field_changes": aggregate,
        "per_trial_field_changes": trial_counts,
        "per_trial_samples": trial_samples,
        "completion": completion_summary(results.trials),
        "token_accounting": token_sizing_summary(results.trials),
        "provider_telemetry": provider_telemetry_summary(results.trials),
        "unrecoverable": unrecoverable,
        "limitations": corrected.get("limitations"),
    }

    (out_dir / "report.json").write_text(
        json.dumps(corrected, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (out_dir / "correction.json").write_text(
        json.dumps(correction, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return correction


def load_correction(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ReportError(f"correction does not exist: {path}")
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ReportError(f"correction `{path}` is not a JSON object")
    if document.get("schema_version") != CORRECTION_SCHEMA_VERSION:
        raise ReportError(
            f"correction schema `{document.get('schema_version')}` is not "
            f"`{CORRECTION_SCHEMA_VERSION}`"
        )
    return document
