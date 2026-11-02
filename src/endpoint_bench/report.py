from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .aiperf import AiperfResults, AiperfRun, ClientProfile
from .cost import cost_preflight
from .profiles import Profile
from .registry import Deployment
from .trials import (
    WORKLOAD_FRAMEWORK_AGENT,
    WORKLOAD_LIVE_TURN_CONFORMANCE,
    WORKLOAD_RECORDED_MULTI_TURN_REPLAY,
    WORKLOAD_SYNTHETIC_SINGLE_TURN,
    Trial,
    outcome_summary,
    token_sizing_summary,
)

REPORT_SCHEMA_VERSION = "0.1.0"

#: Every workload kind, with the label the report uses to describe it. Replay is
#: never described as autonomous agent execution.
WORKLOAD_KINDS: dict[str, str] = {
    WORKLOAD_SYNTHETIC_SINGLE_TURN: "Synthetic single-turn traffic generated from a fixed seed.",
    WORKLOAD_RECORDED_MULTI_TURN_REPLAY: (
        "Recorded trace replay. This is not live agent execution."
    ),
    WORKLOAD_LIVE_TURN_CONFORMANCE: (
        "Live-turn protocol conformance probe. Deterministic protocol checks, "
        "not an intelligence or capability evaluation."
    ),
    WORKLOAD_FRAMEWORK_AGENT: (
        "Framework-based agent execution. Not implemented in this phase."
    ),
}

#: Metrics carried into a report with their AIPerf units preserved.
REPORTED_METRICS = (
    "time_to_first_token",
    "request_latency",
    "output_token_throughput",
    "inter_token_latency",
    "input_sequence_length",
    "output_sequence_length",
    "usage_prompt_tokens",
    "usage_completion_tokens",
    "usage_prompt_tokens_diff_pct",
)


class ReportError(ValueError):
    """Raised when a report cannot be produced or two reports are incomparable."""


def _metric_snapshot(summary: dict[str, Any] | None) -> dict[str, Any]:
    """Extract metrics with units intact. Absent metrics stay absent, never zero."""
    if not summary:
        return {}
    snapshot: dict[str, Any] = {}
    for name in REPORTED_METRICS:
        entry = summary.get(name)
        if not isinstance(entry, dict):
            continue
        snapshot[name] = {
            key: entry[key]
            for key in ("unit", "avg", "p50", "p95", "min", "max", "count")
            if key in entry
        }
    return snapshot


#: Metrics whose denominator or numerator is a token count. They are only
#: meaningful when workload token sizing has been verified against the provider.
TOKEN_NORMALIZED_METRICS = frozenset(
    {
        "output_token_throughput",
        "input_token_throughput",
        "total_token_throughput",
        "prefill_throughput_per_user",
        "e2e_output_token_throughput",
        "effective_decode_throughput",
        "effective_prefill_throughput",
        "effective_total_throughput",
        "effective_decode_throughput_per_user",
        "effective_prefill_throughput_per_user",
        "active_decode_throughput",
        "active_prefill_throughput",
        "active_decode_throughput_per_user",
        "active_prefill_throughput_per_user",
        "active_total_throughput",
    }
)

#: Metrics produced by client-side tokenization. They measure the tokenizer, not
#: the endpoint, whenever the tokenizer does not match the served model.
CLIENT_TOKENIZED_METRICS = frozenset(
    {
        "input_sequence_length",
        "output_sequence_length",
        "output_token_count",
        "total_isl",
        "total_osl",
        "usage_prompt_tokens_diff_pct",
        "usage_completion_tokens_diff_pct",
    }
)

#: Below this sample count, percentile claims are not supported.
PERCENTILE_MIN_SAMPLES = 30

VALIDITY_STATES = (
    "valid",
    "valid_mean_only",
    "invalid_token_sizing",
    "invalid_client_tokenization",
    "not_observed",
)


def assess_metric_validity(
    *,
    metrics: dict[str, Any],
    sizing: dict[str, Any],
    successful_trials: int,
) -> dict[str, dict[str, Any]]:
    """Classify each metric's usability independently.

    A token-count problem must not invalidate latency evidence, and a latency
    problem must not invalidate provider-reported usage. Each metric states its
    own state and reason so a reader can use what is sound and ignore what is not.
    """
    sizing_ok = bool(sizing.get("claims_supported"))
    validity: dict[str, dict[str, Any]] = {}

    for name, entry in sorted(metrics.items()):
        if not isinstance(entry, dict):
            continue

        if name in TOKEN_NORMALIZED_METRICS:
            if sizing_ok:
                state, reason = "valid", "workload token sizing verified within tolerance"
            else:
                state, reason = (
                    "invalid_token_sizing",
                    "token counts disagree with provider usage; the denominator is unreliable",
                )
        elif name in CLIENT_TOKENIZED_METRICS:
            if sizing_ok:
                state, reason = "valid", "client tokenization agrees with provider usage"
            else:
                state, reason = (
                    "invalid_client_tokenization",
                    "client tokenizer does not match the served model",
                )
        elif successful_trials == 0:
            state, reason = "not_observed", "no successful trial produced this metric"
        else:
            state, reason = "valid", "measured directly from response timing"

        count = entry.get("count")
        if state == "valid" and isinstance(count, int) and count < PERCENTILE_MIN_SAMPLES:
            state = "valid_mean_only"
            reason = (
                f"{count} sample(s) below the {PERCENTILE_MIN_SAMPLES}-sample minimum; "
                "mean is usable, percentile claims are not"
            )

        validity[name] = {
            "state": state,
            "reason": reason,
            "percentiles_usable": state == "valid",
        }

    return validity


def _host_only(url: str) -> str:
    """Host and port only. Never a path, query, or embedded credential."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    return f"{host}:{parts.port}" if parts.port else host


def _relative(path: Path | None, root: Path) -> str | None:
    """Artifact location relative to the run directory.

    Absolute paths would leak usernames and machine layout into a report that may
    be shared, so only run-relative locations are recorded.
    """
    if path is None:
        return None
    try:
        return str(path.relative_to(root))
    except ValueError:
        return path.name


def build_provenance(
    *,
    deployment: Deployment,
    profile: Profile,
    client: ClientProfile,
    results: AiperfResults,
    workload_type: str,
    run_dir: Path,
    run: AiperfRun | None = None,
    config_path: Path | None = None,
    config_sha256: str = "",
    trials_path: Path | None = None,
    artifact_dir: Path | None = None,
    process_deadline_s: float | None = None,
    max_usd_ceiling: float | None = None,
    planned_requests: int | None = None,
) -> dict[str, Any]:
    """Record everything needed to interpret or reproduce this run.

    Includes planned-versus-actual request reconciliation, which is what makes a
    silent retry or an unintended probe request visible.
    """
    if workload_type not in WORKLOAD_KINDS:
        raise ReportError(f"unknown workload type `{workload_type}`")

    planned = profile.request_count if planned_requests is None else planned_requests
    actual = results.counters.http_attempts
    tokens = token_sizing_summary(results.trials)

    return {
        "workload_type": workload_type,
        "workload_label": WORKLOAD_KINDS[workload_type],
        "engine": {
            "name": "aiperf",
            "version": results.aiperf_version,
            "result_schema_version": results.summary_schema_version,
            "schema_validated": results.schema_validated,
        },
        "deployment": {
            "deployment_key": deployment.deployment_key,
            "provider_name": deployment.provider_name,
            "model_id": deployment.model_id,
            "model_revision": deployment.model_revision,
            "protocol": deployment.protocol,
            "region": deployment.region,
            "destination_host": _host_only(deployment.base_url),
        },
        "profile": {
            "name": profile.name,
            "schema_version": profile.schema_version,
            "sha256": profile.source_sha256,
            "planned_requests": planned,
        },
        "client": {
            **client.to_dict(),
            "identity": client.identity(),
        },
        "affinity": {
            "mode": "affinity_enabled" if client.session_affinity else "baseline_no_affinity",
            "affinity_header_expected": client.session_affinity,
        },
        "timeouts": {
            "request_timeout_s": client.request_timeout_s,
            "process_deadline_s": process_deadline_s,
            "readiness_probe_timeout_s": client.wait_for_model_timeout_s,
        },
        "retry_policy": {
            "automatic_retries": False,
            "note": (
                "The harness issues no automatic retries; one trial is one HTTP attempt. "
                "AIPerf 0.13.0 does not retry inference requests either."
            ),
        },
        "requests": {
            "planned_trials": planned,
            "actual_http_attempts": actual,
            "reconciliation": "match" if actual == planned else "mismatch",
            "difference": actual - planned,
        },
        "tokens": tokens,
        "cost_preflight": cost_preflight(
            deployment, profile, ceiling_usd=max_usd_ceiling
        ),
        "termination": run.termination.to_dict() if run else None,
        "artifacts": {
            "config_path": _relative(config_path, run_dir),
            "config_sha256": config_sha256 or None,
            "trials_path": _relative(trials_path, run_dir),
            "engine_artifacts_dir": _relative(artifact_dir, run_dir),
            "raw_artifacts_present": bool(
                artifact_dir is not None and (artifact_dir / "profile_export_raw.jsonl").is_file()
            ),
        },
    }


def build_report(
    *,
    run_id: str,
    deployment_key: str,
    profile_name: str,
    client_profile: ClientProfile,
    results: AiperfResults,
    trials: list[Trial],
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic report from parsed evidence.

    The report never upgrades absence into a number. A metric AIPerf did not
    emit is missing here too, and unvalidated schema is stated explicitly.
    """
    limitations = [
        "This report describes client-observed behavior for one endpoint "
        "configuration, not model capability.",
        "Metrics are reported with AIPerf's own units; denominator changes "
        "invalidate comparison.",
    ]
    if not results.schema_validated:
        limitations.append(
            "AIPerf schema was not validated for this run; only error evidence "
            "is trustworthy and no performance claim is supported."
        )
    if results.benchmark_failed:
        limitations.append(
            "The benchmark produced no successful responses. This is a benchmark "
            "failure, not a measured result."
        )

    # Invariant: workload token sizing must be verified against provider usage
    # before any token-normalized claim. Driven by the accounting verdict rather
    # than by parsing note strings.
    sizing = token_sizing_summary(trials)
    if sizing["outside_tolerance"]:
        limitations.append(
            "Client-side and provider-reported input token counts disagree materially. "
            "Token-normalized metrics are marked invalid for this run. Latency and other "
            "directly observed timings remain usable and are marked valid individually."
        )
    elif sizing["unverifiable"] and not sizing["within_tolerance"]:
        limitations.append(
            "Workload token sizing could not be verified against provider-reported usage. "
            "Token-normalized performance and long-context claims are not supported."
        )

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "run_id": run_id,
        "deployment_key": deployment_key,
        "profile_name": profile_name,
        "client_profile": {
            **dict(sorted(client_profile.to_dict().items())),
            "identity": client_profile.identity(),
        },
        "engine": {
            "name": "aiperf",
            "version": results.aiperf_version,
            "summary_schema_version": results.summary_schema_version,
            "schema_validated": results.schema_validated,
        },
        "benchmark_failed": results.benchmark_failed,
        "counters": results.counters.to_dict(),
        "outcomes": outcome_summary(trials),
        "metrics": _metric_snapshot(results.summary),
        "token_accounting": token_sizing_summary(trials),
        "metric_validity": assess_metric_validity(
            metrics=_metric_snapshot(results.summary),
            sizing=token_sizing_summary(trials),
            successful_trials=results.counters.succeeded,
        ),
        "provenance": provenance,
        "notes": list(results.notes),
        "limitations": limitations,
    }


def write_report(path: Path, report: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8")
    return _sha256(text)


def load_report(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ReportError(f"report does not exist: {path}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ReportError(f"report `{path}` is not valid JSON: {error}") from error
    if not isinstance(document, dict):
        raise ReportError(f"report `{path}` is not a JSON object")
    if document.get("schema_version") != REPORT_SCHEMA_VERSION:
        raise ReportError(
            f"report schema `{document.get('schema_version')}` is not "
            f"`{REPORT_SCHEMA_VERSION}`"
        )
    return document


def compare_reports(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Compare two reports, refusing comparisons that would be invalid.

    Different client profiles, deployments, or profiles are not comparable as
    performance evidence, and mismatched engine versions are refused outright.
    """
    for field in ("deployment_key", "profile_name"):
        if baseline.get(field) != candidate.get(field):
            raise ReportError(
                f"cannot compare: `{field}` differs "
                f"({baseline.get(field)!r} vs {candidate.get(field)!r})"
            )
    base_client = baseline.get("client_profile", {})
    cand_client = candidate.get("client_profile", {})
    if base_client.get("identity") != cand_client.get("identity") or base_client.get(
        "name"
    ) != cand_client.get("name"):
        raise ReportError(
            "cannot compare: client profiles differ; baseline and affinity-enabled "
            "experiments are separate result identities"
        )
    base_engine = baseline.get("engine", {})
    cand_engine = candidate.get("engine", {})
    if not base_engine.get("schema_validated") or not cand_engine.get("schema_validated"):
        raise ReportError("cannot compare: at least one run has unvalidated engine schema")
    if base_engine.get("version") != cand_engine.get("version"):
        raise ReportError(
            f"cannot compare: engine versions differ "
            f"({base_engine.get('version')} vs {cand_engine.get('version')})"
        )

    deltas: dict[str, Any] = {}
    base_metrics = baseline.get("metrics", {})
    cand_metrics = candidate.get("metrics", {})
    for name in sorted(set(base_metrics) | set(cand_metrics)):
        base_entry = base_metrics.get(name)
        cand_entry = cand_metrics.get(name)
        if base_entry is None or cand_entry is None:
            deltas[name] = {
                "status": "incomparable",
                "reason": "metric present in only one report",
            }
            continue
        if base_entry.get("unit") != cand_entry.get("unit"):
            deltas[name] = {
                "status": "incomparable",
                "reason": f"unit changed ({base_entry.get('unit')} vs {cand_entry.get('unit')})",
            }
            continue
        base_avg = base_entry.get("avg")
        cand_avg = cand_entry.get("avg")
        if base_avg is None or cand_avg is None:
            deltas[name] = {"status": "incomparable", "reason": "missing average"}
            continue
        deltas[name] = {
            "status": "compared",
            "unit": base_entry.get("unit"),
            "baseline_avg": base_avg,
            "candidate_avg": cand_avg,
            "absolute_delta": cand_avg - base_avg,
            "relative_delta_pct": (
                ((cand_avg - base_avg) / base_avg * 100.0) if base_avg else None
            ),
        }

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "baseline_run_id": baseline.get("run_id"),
        "candidate_run_id": candidate.get("run_id"),
        "deployment_key": baseline.get("deployment_key"),
        "profile_name": baseline.get("profile_name"),
        "counters": {
            "baseline": baseline.get("counters"),
            "candidate": candidate.get("counters"),
        },
        "metric_deltas": deltas,
        "limitations": [
            "A delta is not a qualification verdict. Thresholds are profile-specific "
            "and require calibration.",
            "Comparisons remain valid only for the same deployment, profile, client "
            "profile, and engine version.",
        ],
    }


def _sha256(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()
