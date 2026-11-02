from __future__ import annotations

import json
from pathlib import Path

import pytest

from endpoint_bench.aiperf import BASELINE_CLIENT, AiperfResults, AiperfRun, TerminationReport
from endpoint_bench.profiles import load_profile
from endpoint_bench.registry import load_registry
from endpoint_bench.report import (
    WORKLOAD_KINDS,
    ReportError,
    build_provenance,
    build_report,
)
from endpoint_bench.trials import (
    WORKLOAD_LIVE_TURN_CONFORMANCE,
    WORKLOAD_RECORDED_MULTI_TURN_REPLAY,
    WORKLOAD_SYNTHETIC_SINGLE_TURN,
    HttpAttempt,
    LifecycleState,
    OutcomeState,
    TokenAccounting,
    Trial,
    count_trials,
    token_sizing_summary,
)


def _deployment():
    return load_registry(Path("configs/registry.example.json")).deployments["offline-mock-smoke"]


def _profile():
    return load_profile(Path("configs/profiles/smoke.json"), "smoke")


def _success_trial(index: int = 0, accounting: TokenAccounting | None = None) -> Trial:
    trial = Trial(trial_id=f"t{index}", deployment_key="d", profile_name="p")
    for state in (
        LifecycleState.PLANNED,
        LifecycleState.STARTED,
        LifecycleState.TRANSPORT_COMPLETED,
        LifecycleState.PROTOCOL_COMPLETED,
    ):
        trial.observe(state)
    trial.attempts.append(HttpAttempt(1, http_status=200))
    trial.outcome = OutcomeState.SUCCESS
    trial.finish_reason = "stop"
    trial.finish_class = "natural"
    trial.duration_ms = 1.0
    if accounting is not None:
        trial.token_accounting = accounting
    return trial


def _results(trials: list[Trial], *, validated: bool = True) -> AiperfResults:
    return AiperfResults(
        aiperf_version="0.13.0",
        summary_schema_version="1.4",
        schema_validated=validated,
        summary={} if validated else None,
        trials=trials,
        counters=count_trials(trials),
        benchmark_failed=not validated,
    )


# -- token accounting --------------------------------------------------------


def test_matching_token_counts_are_verified() -> None:
    accounting = TokenAccounting(
        target_input_tokens=128,
        client_estimated_input_tokens=128,
        provider_input_tokens=130,
        tolerance_pct=10.0,
    )

    assert accounting.discrepancy_pct == pytest.approx(1.5625, rel=1e-6)
    assert accounting.sizing_verified is True


def test_large_discrepancy_blocks_token_normalized_claims() -> None:
    accounting = TokenAccounting(
        target_input_tokens=128,
        client_estimated_input_tokens=128,
        provider_input_tokens=12,
        tolerance_pct=10.0,
    )

    assert accounting.discrepancy_pct == pytest.approx(90.625, rel=1e-6)
    assert accounting.sizing_verified is False


def test_missing_provider_usage_is_unverifiable_not_a_pass() -> None:
    accounting = TokenAccounting(
        target_input_tokens=128,
        client_estimated_input_tokens=128,
        provider_input_tokens=None,
    )

    assert accounting.sizing_verified is None
    assert accounting.to_dict()["sizing_verified"] is None


def test_summary_withholds_support_when_any_trial_is_unverified() -> None:
    good = _success_trial(
        0,
        TokenAccounting(client_estimated_input_tokens=100, provider_input_tokens=100),
    )
    bad = _success_trial(
        1,
        TokenAccounting(client_estimated_input_tokens=100, provider_input_tokens=1),
    )
    unknown = _success_trial(2, TokenAccounting(client_estimated_input_tokens=100))

    summary = token_sizing_summary([good, bad, unknown])

    assert summary["within_tolerance"] == 1
    assert summary["outside_tolerance"] == 1
    assert summary["unverifiable"] == 1
    assert summary["claims_supported"] is False


def test_all_matching_trials_support_claims() -> None:
    trials = [
        _success_trial(
            index,
            TokenAccounting(client_estimated_input_tokens=100, provider_input_tokens=102),
        )
        for index in range(3)
    ]

    assert token_sizing_summary(trials)["claims_supported"] is True


def test_no_trials_at_all_is_not_support() -> None:
    assert token_sizing_summary([])["claims_supported"] is False


# -- provenance --------------------------------------------------------------


def test_provenance_records_required_identity(tmp_path: Path) -> None:
    deployment = _deployment()
    profile = _profile()
    trials = [_success_trial()]
    run_dir = tmp_path / "run"

    provenance = build_provenance(
        deployment=deployment,
        profile=profile,
        client=BASELINE_CLIENT,
        results=_results(trials),
        workload_type=WORKLOAD_SYNTHETIC_SINGLE_TURN,
        run_dir=run_dir,
        config_path=run_dir / "aiperf-config.yaml",
        config_sha256="a" * 64,
        trials_path=run_dir / "trials.jsonl",
        artifact_dir=run_dir / "aiperf",
        process_deadline_s=900.0,
    )

    assert provenance["workload_type"] == WORKLOAD_SYNTHETIC_SINGLE_TURN
    assert provenance["engine"]["version"] == "0.13.0"
    assert provenance["engine"]["result_schema_version"] == "1.4"
    assert provenance["deployment"]["deployment_key"] == "offline-mock-smoke"
    assert provenance["deployment"]["model_id"] == "offline-mock-model"
    assert provenance["profile"]["sha256"] == profile.source_sha256
    assert provenance["client"]["identity"] == BASELINE_CLIENT.identity()
    assert provenance["affinity"]["mode"] == "baseline_no_affinity"
    assert provenance["timeouts"]["request_timeout_s"] == BASELINE_CLIENT.request_timeout_s
    assert provenance["timeouts"]["process_deadline_s"] == 900.0
    assert provenance["retry_policy"]["automatic_retries"] is False
    assert provenance["artifacts"]["config_sha256"] == "a" * 64


def test_provenance_reconciles_planned_and_actual_requests(tmp_path: Path) -> None:
    matched = build_provenance(
        deployment=_deployment(),
        profile=_profile(),
        client=BASELINE_CLIENT,
        results=_results([_success_trial(0), _success_trial(1)]),
        workload_type=WORKLOAD_SYNTHETIC_SINGLE_TURN,
        run_dir=tmp_path,
    )
    # smoke.json plans 2 requests; two single-attempt trials match.
    assert matched["requests"] == {
        "planned_trials": 2,
        "actual_http_attempts": 2,
        "reconciliation": "match",
        "difference": 0,
    }

    extra = _success_trial(2)
    extra.attempts.append(HttpAttempt(2, http_status=200))
    mismatched = build_provenance(
        deployment=_deployment(),
        profile=_profile(),
        client=BASELINE_CLIENT,
        results=_results([_success_trial(0), _success_trial(1), extra]),
        workload_type=WORKLOAD_SYNTHETIC_SINGLE_TURN,
        run_dir=tmp_path,
    )
    assert mismatched["requests"]["reconciliation"] == "mismatch"
    assert mismatched["requests"]["difference"] == 2


def test_artifact_locations_do_not_leak_absolute_paths(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "run-abc"

    provenance = build_provenance(
        deployment=_deployment(),
        profile=_profile(),
        client=BASELINE_CLIENT,
        results=_results([_success_trial()]),
        workload_type=WORKLOAD_SYNTHETIC_SINGLE_TURN,
        run_dir=run_dir,
        config_path=run_dir / "aiperf-config.yaml",
        trials_path=run_dir / "trials.jsonl",
        artifact_dir=run_dir / "aiperf",
    )

    artifacts = provenance["artifacts"]
    assert artifacts["config_path"] == "aiperf-config.yaml"
    assert artifacts["trials_path"] == "trials.jsonl"
    assert artifacts["engine_artifacts_dir"] == "aiperf"
    serialized = json.dumps(provenance)
    assert str(tmp_path) not in serialized


def test_provenance_records_termination(tmp_path: Path) -> None:
    run = AiperfRun(
        exit_code=-15,
        timed_out=True,
        duration_ms=6000.0,
        argv=["aiperf", "profile"],
        artifact_dir=tmp_path,
        stdout_path=tmp_path / "o.log",
        stderr_path=tmp_path / "e.log",
        client_identity=BASELINE_CLIENT.identity(),
        config_sha256="b" * 64,
        termination=TerminationReport(reason="deadline", sigterm_sent=True),
    )

    provenance = build_provenance(
        deployment=_deployment(),
        profile=_profile(),
        client=BASELINE_CLIENT,
        results=_results([_success_trial()]),
        workload_type=WORKLOAD_SYNTHETIC_SINGLE_TURN,
        run_dir=tmp_path,
        run=run,
    )

    assert provenance["termination"]["reason"] == "deadline"
    assert provenance["termination"]["sigterm_sent"] is True


def test_unknown_workload_type_is_rejected(tmp_path: Path) -> None:
    try:
        build_provenance(
            deployment=_deployment(),
            profile=_profile(),
            client=BASELINE_CLIENT,
            results=_results([_success_trial()]),
            workload_type="not_a_workload",
            run_dir=tmp_path,
        )
    except ReportError as error:
        assert "unknown workload type" in str(error)
    else:  # pragma: no cover - defensive
        raise AssertionError("expected ReportError")


# -- workload labelling ------------------------------------------------------


def test_replay_is_never_described_as_agent_execution() -> None:
    label = WORKLOAD_KINDS[WORKLOAD_RECORDED_MULTI_TURN_REPLAY].lower()

    assert "not live agent execution" in label


def test_conformance_is_not_described_as_intelligence() -> None:
    label = WORKLOAD_KINDS[WORKLOAD_LIVE_TURN_CONFORMANCE].lower()

    assert "not an intelligence" in label


# -- report integration ------------------------------------------------------


def test_report_carries_provenance_and_token_accounting(tmp_path: Path) -> None:
    trials = [
        _success_trial(
            0,
            TokenAccounting(
                target_input_tokens=128,
                client_estimated_input_tokens=128,
                provider_input_tokens=12,
            ),
        )
    ]
    results = _results(trials)
    provenance = build_provenance(
        deployment=_deployment(),
        profile=_profile(),
        client=BASELINE_CLIENT,
        results=results,
        workload_type=WORKLOAD_SYNTHETIC_SINGLE_TURN,
        run_dir=tmp_path,
    )

    report = build_report(
        run_id="run-test",
        deployment_key="offline-mock-smoke",
        profile_name="smoke",
        client_profile=BASELINE_CLIENT,
        results=results,
        trials=trials,
        provenance=provenance,
    )

    assert report["provenance"]["workload_type"] == WORKLOAD_SYNTHETIC_SINGLE_TURN
    assert report["token_accounting"]["claims_supported"] is False
    # The limitation must name the check that failed, not a generic message.
    assert any("authoritative check" in limit for limit in report["limitations"])
    # Both comparisons are published even when only one governs the verdict.
    assert report["token_accounting"]["client_vs_provider"]["failed"] == 1
    assert report["token_accounting"]["provider_vs_target"]["failed"] == 1


# Metric-specific validity: one problem must not erase unrelated evidence.


def test_latency_stays_valid_when_token_sizing_fails() -> None:
    from endpoint_bench.report import assess_metric_validity

    metrics = {
        "time_to_first_token": {"unit": "ms", "avg": 500.0, "count": 40},
        "request_latency": {"unit": "ms", "avg": 600.0, "count": 40},
        "output_token_throughput": {"unit": "tokens/sec", "avg": 40.0, "count": 40},
        "input_sequence_length": {"unit": "tokens", "avg": 128.0, "count": 40},
        "usage_prompt_tokens": {"unit": "tokens", "avg": 160.0, "count": 40},
    }
    sizing = {"claims_supported": False}

    validity = assess_metric_validity(metrics=metrics, sizing=sizing, successful_trials=40)

    assert validity["time_to_first_token"]["state"] == "valid"
    assert validity["request_latency"]["state"] == "valid"
    assert validity["output_token_throughput"]["state"] == "invalid_token_sizing"
    assert validity["input_sequence_length"]["state"] == "invalid_client_tokenization"
    # Provider-reported usage is a direct observation, not a derived metric.
    assert validity["usage_prompt_tokens"]["state"] == "valid"


def test_token_normalized_metrics_become_valid_when_sizing_is_verified() -> None:
    from endpoint_bench.report import assess_metric_validity

    metrics = {"output_token_throughput": {"unit": "tokens/sec", "avg": 40.0, "count": 40}}

    validity = assess_metric_validity(
        metrics=metrics, sizing={"claims_supported": True}, successful_trials=40
    )

    assert validity["output_token_throughput"]["state"] == "valid"
    assert validity["output_token_throughput"]["percentiles_usable"] is True


def test_small_samples_allowed_for_mean_but_not_percentiles() -> None:
    from endpoint_bench.report import assess_metric_validity

    metrics = {"time_to_first_token": {"unit": "ms", "avg": 500.0, "count": 2}}

    validity = assess_metric_validity(metrics=metrics, sizing={}, successful_trials=2)

    assert validity["time_to_first_token"]["state"] == "valid_mean_only"
    assert validity["time_to_first_token"]["percentiles_usable"] is False


def test_no_successful_trials_marks_metrics_not_observed() -> None:
    from endpoint_bench.report import assess_metric_validity

    validity = assess_metric_validity(
        metrics={"request_latency": {"unit": "ms", "avg": 0.0, "count": 0}},
        sizing={},
        successful_trials=0,
    )

    assert validity["request_latency"]["state"] == "not_observed"
