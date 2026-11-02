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
    ACCOUNTING_PROVIDER_USAGE,
    WORKLOAD_FRAMEWORK_AGENT,
    WORKLOAD_LIVE_TURN_CONFORMANCE,
    WORKLOAD_RECORDED_MULTI_TURN_REPLAY,
    WORKLOAD_SYNTHETIC_SINGLE_TURN,
    Trial,
    completion_summary,
    outcome_summary,
    provider_telemetry_summary,
    token_sizing_summary,
)

REPORT_SCHEMA_VERSION = "0.2.0"
#: Report schemas this reader can load. Older reports predate the separation of
#: provider-versus-target conformance from client-versus-provider agreement and
#: the completion classification, so they are loaded and labelled legacy rather
#: than reinterpreted.
LEGACY_REPORT_SCHEMA_VERSIONS = ("0.1.0",)
READABLE_REPORT_SCHEMA_VERSIONS = (REPORT_SCHEMA_VERSION, *LEGACY_REPORT_SCHEMA_VERSIONS)

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
    # Provider cache and reasoning telemetry. AIPerf 0.13.0 normalizes cache-read
    # and reasoning counts but exposes no cache-write metric.
    "usage_prompt_cache_read_tokens",
    "total_usage_prompt_cache_read_tokens",
    "overall_usage_prompt_cache_read_pct",
    "usage_reasoning_tokens",
    "total_usage_reasoning_tokens",
    "reasoning_token_count",
    "total_reasoning_tokens",
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
#: the endpoint, whenever the tokenizer does not match the served model. Under
#: provider accounting these metrics are derived from provider usage instead, so
#: they are reclassified rather than condemned.
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

#: Token counts the provider itself reported, or that AIPerf derived from them.
#: They are authoritative for the served model regardless of how workload sizing
#: was verified, so a sizing problem elsewhere does not invalidate them.
#:
#: `reasoning_token_count` is included because it equals `usage_reasoning_tokens`
#: exactly on every trial of the screening runs (including non-round values such
#: as 114 against a 222-token completion), so it is provider-sourced under
#: provider accounting rather than independently client-counted.
PROVIDER_REPORTED_METRICS = frozenset(
    {
        "usage_prompt_tokens",
        "usage_completion_tokens",
        "usage_total_tokens",
        "usage_prompt_cache_read_tokens",
        "total_usage_prompt_cache_read_tokens",
        "overall_usage_prompt_cache_read_pct",
        "usage_reasoning_tokens",
        "total_usage_reasoning_tokens",
        "reasoning_token_count",
        "total_reasoning_tokens",
    }
)

#: Request-level timing metrics whose value is a lower bound rather than a
#: completion time when the response stopped at the output ceiling.
CENSORED_BY_OUTPUT_CEILING = frozenset({"request_latency", "effective_latency"})

#: Below this sample count, percentile claims are not supported.
PERCENTILE_MIN_SAMPLES = 30

VALIDITY_STATES = (
    "valid",
    "valid_mean_only",
    "censored",
    "invalid_token_sizing",
    "invalid_client_tokenization",
    "not_observed",
)


def assess_metric_validity(
    *,
    metrics: dict[str, Any],
    sizing: dict[str, Any],
    successful_trials: int,
    accounting_source: str | None = None,
    completion: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Classify each metric's usability independently.

    A token-count problem must not invalidate latency evidence, and a latency
    problem must not invalidate provider-reported usage. Each metric states its
    own state and reason so a reader can use what is sound and ignore what is not.

    The reason always names the comparison that produced the verdict, because
    under provider accounting the client-versus-provider check did not run at all
    and a verdict that does not say so is misleading.
    """
    sizing_ok = bool(sizing.get("claims_supported"))
    provider_accounting = accounting_source == ACCOUNTING_PROVIDER_USAGE
    if provider_accounting:
        sizing_reason = "provider-reported input matches the requested workload size"
        sizing_failure_reason = (
            "provider-reported input does not match the requested workload size; "
            "requested-size claims are unsupported"
        )
    else:
        sizing_reason = "client tokenization agrees with provider-reported usage"
        sizing_failure_reason = (
            "client token counts disagree with provider usage; the denominator is unreliable"
        )

    censored = bool((completion or {}).get("latency_censored"))
    censored_fraction = (completion or {}).get("latency_censored_fraction")
    validity: dict[str, dict[str, Any]] = {}

    for name, entry in sorted(metrics.items()):
        if not isinstance(entry, dict):
            continue

        if name in PROVIDER_REPORTED_METRICS:
            state, reason = (
                "valid",
                "reported by, or derived directly from, provider usage for the served model",
            )
        elif name in TOKEN_NORMALIZED_METRICS:
            if provider_accounting:
                state, reason = (
                    "valid",
                    "provider-token throughput for the workload actually delivered; "
                    "requested-size comparability is assessed separately",
                )
            elif sizing_ok:
                state, reason = "valid", sizing_reason
            else:
                state, reason = "invalid_token_sizing", sizing_failure_reason
        elif name in CLIENT_TOKENIZED_METRICS:
            if provider_accounting:
                # Not a client tokenization at all under this mode: AIPerf filled
                # these from provider usage.
                state, reason = (
                    "valid",
                    "derived from provider usage, not an independent client tokenization; "
                    "requested-size comparability is assessed separately",
                )
            elif sizing_ok:
                state, reason = "valid", "client tokenization agrees with provider usage"
            else:
                state, reason = (
                    "invalid_client_tokenization",
                    "client tokenizer does not match the served model",
                )
        elif successful_trials == 0 and not isinstance(entry.get("count"), int):
            state, reason = "not_observed", "no completed trial produced this metric"
        else:
            # Presence in the metric snapshot already means AIPerf emitted it.
            # Whether a trial is classified successful is a separate question:
            # a ceiling-truncated response still measured real time-to-first-token.
            count_value = entry.get("count")
            if isinstance(count_value, int) and count_value <= 0:
                state, reason = "not_observed", "the engine emitted zero samples for this metric"
            else:
                state, reason = "valid", "measured directly from response timing"

        is_censored = censored and name in CENSORED_BY_OUTPUT_CEILING
        if is_censored and state in ("valid", "valid_mean_only"):
            share = f"{censored_fraction:.0%}" if isinstance(censored_fraction, float) else "some"
            reason = (
                f"{reason}; right-censored: {share} of completed trials stopped at the "
                "configured output ceiling, so this is a lower bound on time-to-completion"
            )

        count = entry.get("count")
        if state == "valid" and isinstance(count, int) and count < PERCENTILE_MIN_SAMPLES:
            state = "valid_mean_only"
            reason = (
                f"{count} sample(s) below the {PERCENTILE_MIN_SAMPLES}-sample minimum; "
                "mean is usable, percentile claims are not"
                + ("; value is right-censored by the output ceiling" if is_censored else "")
            )

        if is_censored and state in ("valid", "valid_mean_only"):
            state = "censored"
        validity[name] = {
            "state": state,
            "reason": reason,
            "percentiles_usable": state == "valid",
            "censored": is_censored,
            "statistic_scope": "time_to_termination" if is_censored else "observed_metric",
            "completion_mean_usable": not is_censored,
            "workload_claims_supported": sizing_ok,
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
            "requested_input_tokens": profile.input_tokens,
            "requested_max_output_tokens": profile.max_output_tokens,
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
        "completion": completion_summary(results.trials),
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
    if client_profile.use_server_token_count:
        limitations.append(
            "Token counts come from provider-reported usage, so the throughput "
            "denominator is authoritative for the served model. No independent "
            "client tokenizer ran, so client-versus-provider agreement was not "
            "observed. Workload sizing is instead checked as provider-versus-target "
            "conformance against the profile's requested input size."
        )

    if results.benchmark_failed:
        limitations.append(
            "The benchmark produced no successful responses. This is a benchmark "
            "failure, not a measured result."
        )

    # Workload claims require verified sizing; provider-token observations of
    # the delivered workload remain separate from that qualification.
    sizing = token_sizing_summary(trials)
    if sizing["outside_tolerance"]:
        limitations.append(
            "Workload token sizing failed its authoritative check for this run. "
            "Requested-size claims are unsupported; provider-token throughput remains "
            "an observation of the delivered workload. Client-tokenized throughput "
            "requires independent token agreement. Latency and other "
            "directly observed timings remain usable and are marked valid individually."
        )
    elif sizing["unverifiable"] and not sizing["within_tolerance"]:
        limitations.append(
            "Workload token sizing could not be verified. Requested-size and "
            "long-context claims are unsupported; provider-token observations retain "
            "their recorded provenance."
        )

    completion = completion_summary(trials)
    if completion["ceiling_truncated"]:
        censored = completion["latency_censored_fraction"]
        share = f"{censored:.0%}" if isinstance(censored, float) else "some"
        limitations.append(
            f"{completion['ceiling_truncated']} of {completion['protocol_completed_trials']} "
            f"completed trials ({share}) stopped at the configured output ceiling "
            "(`finish_reason: length`). Those trials did not finish naturally, so "
            "`request_latency` is a right-censored lower bound on time-to-completion and "
            "is marked as censored. Time-to-first-token is unaffected. Success counters "
            "exclude these trials and report them as `truncated_trials`."
        )

    telemetry = provider_telemetry_summary(trials)
    if telemetry["reasoning_only_completions"]:
        limitations.append(
            f"{telemetry['reasoning_only_completions']} completed trial(s) spent the entire "
            "completion budget on provider-reported reasoning tokens and emitted no visible "
            "answer content. Output throughput in this run describes reasoning-token "
            "emission, not answer generation."
        )
    if telemetry["metrics"]["cache_write_tokens"]["trials_reporting"]:
        limitations.append(
            "Cache-write counts are not exposed by AIPerf 0.13.0 as a metric. They were "
            "recovered from the raw streamed `usage.prompt_tokens_details."
            "created_cache_tokens` field and are labelled with that source. Cache-read "
            "counts come from AIPerf's normalized `usage_prompt_cache_read_tokens` metric."
        )

    sizing = token_sizing_summary(trials)
    completion = completion_summary(trials)
    metrics = _metric_snapshot(results.summary)
    accounting_source = (
        ACCOUNTING_PROVIDER_USAGE
        if client_profile.use_server_token_count
        else "client_tokenizer"
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
        "metrics": metrics,
        "token_accounting": sizing,
        "completion": completion,
        "provider_telemetry": provider_telemetry_summary(trials),
        "metric_validity": assess_metric_validity(
            metrics=metrics,
            sizing=sizing,
            successful_trials=results.counters.succeeded,
            accounting_source=accounting_source,
            completion=completion,
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
    """Load a report, refusing schemas this reader does not understand.

    Reports written before the provider-versus-target split are still readable so
    existing evidence is not orphaned, but they are labelled `legacy` because
    their `sizing_verified` field came from a comparison that was circular under
    provider accounting. A legacy report is never silently upgraded.
    """
    if not path.is_file():
        raise ReportError(f"report does not exist: {path}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ReportError(f"report `{path}` is not valid JSON: {error}") from error
    if not isinstance(document, dict):
        raise ReportError(f"report `{path}` is not a JSON object")
    schema = document.get("schema_version")
    if schema not in READABLE_REPORT_SCHEMA_VERSIONS:
        raise ReportError(
            f"report schema `{schema}` is not one of "
            f"{', '.join(READABLE_REPORT_SCHEMA_VERSIONS)}"
        )
    if schema in LEGACY_REPORT_SCHEMA_VERSIONS:
        document["legacy_schema"] = True
        document["legacy_reason"] = (
            f"report schema {schema} predates the separation of provider-versus-target "
            "conformance from client-versus-provider agreement; its `sizing_verified` "
            "field must not be read as an independent verification under provider "
            "accounting"
        )
    else:
        document.setdefault("legacy_schema", False)
    return document


def compare_reports(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Compare two reports, refusing comparisons that would be invalid.

    Different client profiles, deployments, or profiles are not comparable as
    performance evidence, and mismatched engine versions are refused outright.
    A differing token-accounting source is also refused: one report's denominators
    are provider-authoritative and the other's are client-tokenized, so their
    throughput figures answer different questions.
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
    if base_client.get("use_server_token_count") != cand_client.get("use_server_token_count"):
        raise ReportError(
            "cannot compare: token accounting source differs "
            f"({base_client.get('token_accounting_source')} vs "
            f"{cand_client.get('token_accounting_source')})"
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

    censoring = {
        "baseline": (baseline.get("completion") or {}).get("latency_censored_fraction"),
        "candidate": (candidate.get("completion") or {}).get("latency_censored_fraction"),
    }
    limitations = [
        "A delta is not a qualification verdict. Thresholds are profile-specific "
        "and require calibration.",
        "Comparisons remain valid only for the same deployment, profile, client "
        "profile, and engine version.",
    ]
    if any(value for value in censoring.values()):
        limitations.append(
            "At least one side has output-ceiling-censored latency. A latency delta "
            "between differently-censored runs compares two different truncation "
            "points, not two completion times."
        )
    if baseline.get("legacy_schema") or candidate.get("legacy_schema"):
        limitations.append(
            "At least one report uses a legacy schema whose token-sizing verdict is "
            "not independent under provider accounting."
        )

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
        "censoring": censoring,
        "metric_deltas": deltas,
        "limitations": limitations,
    }


def _sha256(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()
