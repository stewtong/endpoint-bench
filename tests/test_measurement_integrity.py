"""Regression fixtures for the measurement-integrity corrections.

Three defects are pinned here, because each one produced a published number that
was wrong in a way no existing test caught:

1. Token sizing was circular under provider accounting. AIPerf derives
   `input_sequence_length` from `usage.prompt_tokens` in that mode, so the
   client-versus-provider difference was always exactly zero and every trial
   reported `sizing_verified: true` no matter what the provider actually saw.
2. Every trial was recorded as `success` regardless of how the server terminated
   the stream. In the six screening cells, 117 of 120 responses ended at the
   configured output ceiling and all 120 were reported as clean completions.
3. Provider cache and reasoning token counts were discarded, and there was no
   path at all for cache-write because AIPerf 0.13.0 exposes no such metric.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from endpoint_bench.aiperf import parse_artifacts
from endpoint_bench.report import (
    REPORT_SCHEMA_VERSION,
    ReportError,
    assess_metric_validity,
    compare_reports,
    load_report,
)
from endpoint_bench.trials import (
    ACCOUNTING_CLIENT_TOKENIZER,
    ACCOUNTING_PROVIDER_USAGE,
    FINISH_CEILING,
    FINISH_NATURAL,
    LifecycleState,
    OutcomeState,
    ProviderTelemetry,
    TokenAccounting,
    Trial,
    TrialSchemaError,
    completion_summary,
    count_trials,
    provider_telemetry_summary,
    token_sizing_summary,
)

# -- helpers ----------------------------------------------------------------


def _trial(**kwargs) -> Trial:
    trial = Trial(trial_id=kwargs.pop("trial_id", "t-0"), deployment_key="d", profile_name="p")
    trial.observe(LifecycleState.STARTED)
    trial.observe(LifecycleState.TRANSPORT_COMPLETED)
    trial.observe(LifecycleState.PROTOCOL_COMPLETED)
    for key, value in kwargs.items():
        setattr(trial, key, value)
    return trial


def _frame(**overrides) -> dict:
    """A raw request record shaped like AIPerf 0.13.0's raw export."""
    request_id = overrides.pop("request_id", "req-0")
    status = overrides.pop("status", 200)
    finish_reason = overrides.pop("finish_reason", "stop")
    content = overrides.pop("content", "hello")
    usage = overrides.pop("usage", None)
    max_completion_tokens = overrides.pop("max_completion_tokens", 16)
    saw_done = overrides.pop("saw_done", True)
    requests_body = overrides.pop("messages", [{"role": "user", "content": "x"}])
    packets: list[dict] = []
    if content:
        packets.append(
            {
                "name": "data",
                "value": json.dumps(
                    {
                        "choices": [
                            {"index": 0, "delta": {"content": content}, "finish_reason": None}
                        ]
                    }
                ),
            }
        )
    final: dict = {
        "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}]
    }
    if usage is not None:
        final["usage"] = usage
    packets.append({"name": "data", "value": json.dumps(final)})
    if saw_done:
        packets.append({"name": "data", "value": "[DONE]"})
    payload: dict = {"messages": requests_body, "stream": True}
    if max_completion_tokens is not None:
        payload["max_completion_tokens"] = max_completion_tokens
    return {
        "metadata": {"x_request_id": request_id},
        "payload": payload,
        "status": status,
        "responses": [{"perf_ns": 1, "packets": packets}],
        **overrides,
    }


def _artifact(
    tmp_path: Path,
    *,
    records: list[dict],
    raw: list[dict],
    summary: bool = True,
) -> Path:
    artifact = tmp_path / "artifact"
    artifact.mkdir(parents=True, exist_ok=True)
    if summary:
        (artifact / "profile_export_aiperf.json").write_text(
            json.dumps({"schema_version": "1.4", "aiperf_version": "0.13.0"})
        )
    (artifact / "profile_export.jsonl").write_text(
        "\n".join(json.dumps(record) for record in records) + "\n"
    )
    if raw is not None:
        (artifact / "profile_export_raw.jsonl").write_text(
            "\n".join(json.dumps(frame) for frame in raw) + "\n"
        )
    return artifact


def _record(request_id: str, metrics: dict | None = None, error: dict | None = None) -> dict:
    record: dict = {
        "metadata": {
            "x_request_id": request_id,
            "was_cancelled": False,
            "context_overflow_skip": False,
        },
        "metrics": metrics or {},
    }
    if error is not None:
        record["error"] = error
    return record


# -- 1. token sizing is not circular --------------------------------------


def test_provider_accounting_cannot_carry_a_client_estimate() -> None:
    """The constructor is the guard: no fabricated client count can be attached."""
    with pytest.raises(TrialSchemaError, match="circular"):
        TokenAccounting(
            target_input_tokens=1024,
            client_estimated_input_tokens=1086,
            provider_input_tokens=1086,
            accounting_source=ACCOUNTING_PROVIDER_USAGE,
        )


def test_provider_accounting_does_not_report_zero_discrepancy() -> None:
    """The old defect produced discrepancy 0.0 and verified true. Absence is None."""
    accounting = TokenAccounting(
        target_input_tokens=1024,
        provider_input_tokens=1086,
        accounting_source=ACCOUNTING_PROVIDER_USAGE,
    )

    assert accounting.discrepancy_pct is None
    assert accounting.client_provider_agreement == "not_observed"
    assert accounting.client_provider_agreement_pct is None
    assert accounting.primary_check == "provider_vs_target"
    # 62/1024 = 6.05%, inside the 10% tolerance, so the authoritative check passes.
    assert accounting.provider_target_conformance_pct == pytest.approx(6.0546, rel=1e-3)
    assert accounting.sizing_verified is True


def test_provider_target_conformance_fails_when_provider_saw_the_wrong_size() -> None:
    """A provider that received a different workload size must fail the check."""
    accounting = TokenAccounting(
        target_input_tokens=1024,
        provider_input_tokens=4096,
        accounting_source=ACCOUNTING_PROVIDER_USAGE,
    )

    assert accounting.provider_target_conformance == "failed"
    assert accounting.sizing_verified is False
    assert token_sizing_summary([_trial(token_accounting=accounting)])["claims_supported"] is False


def test_provider_accounting_never_populates_client_metric_from_server_metric(
    tmp_path: Path,
) -> None:
    """End-to-end: the exact shape that produced the false verified verdict.

    AIPerf reports `input_sequence_length` equal to `usage_prompt_tokens` when
    server token counting is on. That equality is the signature of the defect, so
    the client estimate must stay absent.
    """
    artifact = _artifact(
        tmp_path,
        records=[
            _record(
                "req-0",
                metrics={
                    "input_sequence_length": {"value": 33180, "unit": "tokens"},
                    "usage_prompt_tokens": {"value": 33180, "unit": "tokens"},
                    "output_sequence_length": {"value": 254, "unit": "tokens"},
                    "usage_completion_tokens": {"value": 254, "unit": "tokens"},
                },
            )
        ],
        raw=[_frame(request_id="req-0", finish_reason="stop")],
    )

    results = parse_artifacts(
        artifact,
        deployment_key="d",
        profile_name="p",
        target_input_tokens=32768,
        accounting_source=ACCOUNTING_PROVIDER_USAGE,
    )
    accounting = results.trials[0].token_accounting

    assert accounting.client_estimated_input_tokens is None
    assert accounting.observed_output_tokens is None
    assert accounting.discrepancy_pct is None
    assert accounting.client_provider_agreement == "not_observed"
    assert accounting.client_estimate_note is not None
    # 412/32768 = 1.26%
    assert accounting.provider_target_conformance_pct == pytest.approx(1.2573, rel=1e-3)
    assert accounting.primary_check == "provider_vs_target"
    summary = token_sizing_summary(results.trials)
    assert summary["client_vs_provider"]["not_observed"] == 1
    assert summary["provider_vs_target"]["verified"] == 1
    assert summary["client_estimates_coinciding_with_provider"] == 0


def test_client_accounting_still_runs_the_independent_comparison(tmp_path: Path) -> None:
    artifact = _artifact(
        tmp_path,
        records=[
            _record(
                "req-0",
                metrics={
                    "input_sequence_length": {"value": 1024, "unit": "tokens"},
                    "usage_prompt_tokens": {"value": 1280, "unit": "tokens"},
                },
            )
        ],
        raw=[_frame(request_id="req-0")],
    )

    results = parse_artifacts(
        artifact, deployment_key="d", profile_name="p", target_input_tokens=1024
    )
    accounting = results.trials[0].token_accounting

    assert accounting.client_estimated_input_tokens == 1024
    assert accounting.primary_check == "client_vs_provider"
    assert accounting.client_provider_agreement_pct == pytest.approx(25.0)
    assert accounting.client_provider_agreement == "failed"
    assert accounting.sizing_verified is False


# -- 2. success classification ---------------------------------------------


def test_ceiling_truncation_is_not_success(tmp_path: Path) -> None:
    artifact = _artifact(
        tmp_path,
        records=[
            _record(
                "req-0",
                metrics={
                    "usage_prompt_tokens": {"value": 100, "unit": "tokens"},
                    "usage_completion_tokens": {"value": 256, "unit": "tokens"},
                },
            )
        ],
        raw=[
            _frame(
                request_id="req-0",
                finish_reason="length",
                max_completion_tokens=256,
                usage={"completion_tokens": 256, "prompt_tokens": 100},
            )
        ],
    )

    results = parse_artifacts(artifact, deployment_key="d", profile_name="p")
    trial = results.trials[0]

    assert trial.outcome is OutcomeState.OUTPUT_TRUNCATED
    assert trial.finish_class == FINISH_CEILING
    assert trial.attribution == "harness"
    assert trial.requested_max_completion_tokens == 256
    assert results.counters.succeeded == 0
    assert results.counters.truncated == 1
    assert results.counters.failed == 0
    assert results.counters.to_dict()["truncated_trials"] == 1
    # A truncated-but-complete response is still scoreable evidence, so this is
    # not a benchmark failure; the censoring is reported instead of hidden.
    assert results.benchmark_failed is False
    assert any("stopped at the configured output ceiling" in note for note in results.notes)
    assert completion_summary(results.trials)["latency_censored"] is True


def test_natural_completion_is_success(tmp_path: Path) -> None:
    artifact = _artifact(
        tmp_path,
        records=[_record("req-0", metrics={"usage_prompt_tokens": {"value": 100}})],
        raw=[_frame(request_id="req-0", finish_reason="stop")],
    )

    results = parse_artifacts(artifact, deployment_key="d", profile_name="p")

    assert results.trials[0].outcome is OutcomeState.SUCCESS
    assert results.trials[0].finish_class == FINISH_NATURAL
    assert results.counters.succeeded == 1
    assert results.benchmark_failed is False


@pytest.mark.parametrize(
    ("status", "expected", "attribution"),
    [
        (500, OutcomeState.SERVER_ERROR, "provider"),
        (503, OutcomeState.SERVER_ERROR, "provider"),
        (429, OutcomeState.RATE_LIMITED, "provider"),
        (400, OutcomeState.CLIENT_ERROR, "unknown"),
        (401, OutcomeState.CLIENT_ERROR, "unknown"),
    ],
)
def test_non_2xx_is_never_success(
    tmp_path: Path, status: int, expected: OutcomeState, attribution: str
) -> None:
    """A well-formed body cannot rescue a failed status."""
    artifact = _artifact(
        tmp_path,
        records=[_record("req-0", metrics={"usage_prompt_tokens": {"value": 100}})],
        raw=[_frame(request_id="req-0", status=status, finish_reason="stop")],
    )

    results = parse_artifacts(artifact, deployment_key="d", profile_name="p")
    trial = results.trials[0]

    assert trial.outcome is expected
    assert trial.attribution == attribution
    assert results.counters.succeeded == 0
    assert trial.observed_http_status == status


def test_context_rejection_is_distinguished_by_message(tmp_path: Path) -> None:
    artifact = _artifact(
        tmp_path,
        records=[_record("req-0")],
        raw=[
            _frame(
                request_id="req-0",
                status=400,
                finish_reason=None,
                content="",
                messages=[{"role": "user", "content": "x"}],
                body_hint="This model's maximum context length is 65536 tokens",
            )
        ],
    )
    # The hint lives inside the error body, which the parser reads from packets.
    raw_path = artifact / "profile_export_raw.jsonl"
    document = json.loads(raw_path.read_text())
    document["responses"][0]["packets"] = [
        {
            "name": "data",
            "value": json.dumps(
                {"error": {"message": "maximum context length is 65536 tokens"}}
            ),
        }
    ]
    raw_path.write_text(json.dumps(document) + "\n")

    results = parse_artifacts(artifact, deployment_key="d", profile_name="p")

    assert results.trials[0].outcome is OutcomeState.CONTEXT_REJECTED


def test_stream_without_finish_reason_is_interrupted(tmp_path: Path) -> None:
    artifact = _artifact(
        tmp_path,
        records=[_record("req-0")],
        raw=[_frame(request_id="req-0", finish_reason=None, saw_done=False)],
    )

    results = parse_artifacts(artifact, deployment_key="d", profile_name="p")

    assert results.trials[0].outcome is OutcomeState.STREAM_INTERRUPTED
    assert results.trials[0].attribution == "provider"


def test_unrecognized_finish_reason_is_not_assumed_natural(tmp_path: Path) -> None:
    artifact = _artifact(
        tmp_path,
        records=[_record("req-0")],
        raw=[_frame(request_id="req-0", finish_reason="some_new_reason")],
    )

    results = parse_artifacts(artifact, deployment_key="d", profile_name="p")

    assert results.trials[0].outcome is OutcomeState.UNKNOWN
    assert results.trials[0].outcome_detail == "unrecognized_finish_reason_some_new_reason"


def test_success_without_http_status_is_refused() -> None:
    """Body content alone cannot establish success."""
    trial = _trial(outcome=OutcomeState.SUCCESS, finish_class=FINISH_NATURAL)

    with pytest.raises(TrialSchemaError, match="no observed 2xx"):
        trial.validate()


def test_missing_raw_export_prevents_success_claims(tmp_path: Path) -> None:
    artifact = _artifact(
        tmp_path, records=[_record("req-0")], raw=None
    )

    results = parse_artifacts(artifact, deployment_key="d", profile_name="p")

    assert results.counters.succeeded == 0
    assert results.trials[0].outcome is OutcomeState.UNKNOWN
    assert "http_status_unobserved" in results.trials[0].evidence_gaps
    assert any("raw export absent" in note for note in results.notes)


def test_success_contradicting_status_is_refused() -> None:
    trial = _trial(
        outcome=OutcomeState.SUCCESS,
        finish_class=FINISH_NATURAL,
    )
    from endpoint_bench.trials import HttpAttempt

    trial.attempts.append(HttpAttempt(attempt_index=1, http_status=500))

    with pytest.raises(TrialSchemaError, match="contradicts observed HTTP status"):
        trial.validate()


def test_success_with_ceiling_finish_is_refused() -> None:
    """The exact regression: the old code set success on every completed stream."""
    from endpoint_bench.trials import HttpAttempt

    trial = _trial(outcome=OutcomeState.SUCCESS, finish_class=FINISH_CEILING, finish_reason="length")
    trial.attempts.append(HttpAttempt(attempt_index=1, http_status=200))

    with pytest.raises(TrialSchemaError, match="ceiling truncation"):
        trial.validate()


def test_counters_partition_started_trials() -> None:
    from endpoint_bench.trials import HttpAttempt

    trials = [
        _trial(trial_id="ok", outcome=OutcomeState.SUCCESS, finish_class=FINISH_NATURAL,
               finish_reason="stop"),
        _trial(
            trial_id="cut",
            outcome=OutcomeState.OUTPUT_TRUNCATED,
            finish_class=FINISH_CEILING,
            finish_reason="length",
        ),
        _trial(trial_id="err", outcome=OutcomeState.SERVER_ERROR),
    ]
    for trial in trials:
        trial.attempts.append(HttpAttempt(attempt_index=1, http_status=200))

    counters = count_trials(trials)

    assert counters.started == 3
    assert counters.succeeded == 1
    assert counters.truncated == 1
    assert counters.failed == 1
    assert counters.succeeded + counters.truncated + counters.failed == counters.started


# -- 3. cache and reasoning telemetry -------------------------------------


def test_telemetry_prefers_metrics_and_recovers_cache_write_from_raw(
    tmp_path: Path,
) -> None:
    artifact = _artifact(
        tmp_path,
        records=[
            _record(
                "req-0",
                metrics={
                    "usage_prompt_tokens": {"value": 1024},
                    "usage_completion_tokens": {"value": 256},
                    "usage_prompt_cache_read_tokens": {"value": 768},
                    "usage_reasoning_tokens": {"value": 200},
                },
            )
        ],
        raw=[
            _frame(
                request_id="req-0",
                usage={
                    "prompt_tokens": 1024,
                    "completion_tokens": 256,
                    "prompt_tokens_details": {"cached_tokens": 768, "created_cache_tokens": 256},
                    "completion_tokens_details": {"reasoning_tokens": 200},
                },
            )
        ],
    )

    results = parse_artifacts(artifact, deployment_key="d", profile_name="p")
    telemetry = results.trials[0].provider_telemetry

    assert telemetry.cache_read_tokens == 768
    assert telemetry.cache_write_tokens == 256
    assert telemetry.reasoning_tokens == 200
    assert telemetry.visible_output_tokens == 56
    assert telemetry.sources["cache_read_tokens"] == "aiperf_metric"
    assert telemetry.sources["cache_write_tokens"] == "raw_response_usage"
    assert telemetry.sources["reasoning_tokens"] == "aiperf_metric"


def test_absent_telemetry_stays_null_not_zero(tmp_path: Path) -> None:
    artifact = _artifact(
        tmp_path,
        records=[
            _record(
                "req-0",
                metrics={
                    "usage_prompt_tokens": {"value": 100},
                    "usage_completion_tokens": {"value": 10},
                },
            )
        ],
        raw=[_frame(request_id="req-0", usage={"prompt_tokens": 100, "completion_tokens": 10})],
    )

    results = parse_artifacts(artifact, deployment_key="d", profile_name="p")
    telemetry = results.trials[0].provider_telemetry
    summary = provider_telemetry_summary(results.trials)

    assert telemetry.cache_read_tokens is None
    assert telemetry.cache_write_tokens is None
    assert telemetry.reasoning_tokens is None
    assert telemetry.visible_output_tokens is None
    assert telemetry.cache_telemetry_observed is False
    assert summary["metrics"]["cache_read_tokens"]["sum"] is None
    assert summary["metrics"]["cache_read_tokens"]["trials_reporting"] == 0
    assert summary["cache_write_source"] is None


def test_partial_telemetry_reports_the_reporting_count() -> None:
    trials = [
        _trial(trial_id="a", provider_telemetry=ProviderTelemetry(cache_read_tokens=0)),
        _trial(trial_id="b", provider_telemetry=ProviderTelemetry()),
    ]

    summary = provider_telemetry_summary(trials)

    # Zero is a real observation and must survive as a sum, not collapse to null.
    assert summary["metrics"]["cache_read_tokens"]["sum"] == 0
    assert summary["metrics"]["cache_read_tokens"]["trials_reporting"] == 1
    assert summary["metrics"]["cache_read_tokens"]["trials_total"] == 2
    assert summary["metrics"]["reasoning_tokens"]["sum"] is None


def test_reasoning_only_completion_is_flagged() -> None:
    telemetry = ProviderTelemetry(
        reasoning_tokens=256, visible_output_tokens=0, cache_read_tokens=0
    )

    assert telemetry.all_reasoning is True
    summary = provider_telemetry_summary(
        [_trial(provider_telemetry=telemetry, outcome=OutcomeState.SUCCESS)]
    )
    assert summary["reasoning_only_completions"] == 1


# -- 4. report-level consequences -----------------------------------------


def _metrics(**overrides) -> dict:
    base = {
        "time_to_first_token": {"unit": "ms", "avg": 500.0, "count": 40},
        "request_latency": {"unit": "ms", "avg": 900.0, "count": 40},
        "output_token_throughput": {"unit": "tokens/sec", "avg": 40.0, "count": 40},
        "input_sequence_length": {"unit": "tokens", "avg": 1024.0, "count": 40},
        "usage_prompt_cache_read_tokens": {"unit": "tokens", "avg": 512.0, "count": 40},
    }
    base.update(overrides)
    return base


def test_ceiling_censoring_marks_latency_but_not_ttft() -> None:
    validity = assess_metric_validity(
        metrics=_metrics(),
        sizing={"claims_supported": True},
        successful_trials=40,
        accounting_source=ACCOUNTING_PROVIDER_USAGE,
        completion={"latency_censored": True, "latency_censored_fraction": 0.975},
    )

    assert validity["request_latency"]["censored"] is True
    assert "right-censored" in validity["request_latency"]["reason"]
    assert validity["time_to_first_token"]["censored"] is False
    assert validity["time_to_first_token"]["state"] == "valid"


def test_provider_reported_metrics_survive_a_sizing_failure() -> None:
    validity = assess_metric_validity(
        metrics=_metrics(),
        sizing={"claims_supported": False},
        successful_trials=40,
        accounting_source=ACCOUNTING_CLIENT_TOKENIZER,
    )

    assert validity["output_token_throughput"]["state"] == "invalid_token_sizing"
    assert validity["usage_prompt_cache_read_tokens"]["state"] == "valid"
    assert validity["time_to_first_token"]["state"] == "valid"


def test_timings_stay_observed_when_no_trial_completed_naturally() -> None:
    """A ceiling-truncated run still measured real time-to-first-token.

    Zero successful trials is not the same as zero observations, and treating it
    that way discarded usable latency evidence.
    """
    validity = assess_metric_validity(
        metrics=_metrics(),
        sizing={"claims_supported": True},
        successful_trials=0,
        accounting_source=ACCOUNTING_PROVIDER_USAGE,
        completion={"latency_censored": True, "latency_censored_fraction": 1.0},
    )

    assert validity["time_to_first_token"]["state"] == "valid"
    assert validity["request_latency"]["state"] == "censored"
    assert validity["request_latency"]["censored"] is True


def test_zero_sample_metric_is_not_observed() -> None:
    validity = assess_metric_validity(
        metrics=_metrics(inter_token_latency={"unit": "ms", "avg": 0.0, "count": 0}),
        sizing={"claims_supported": True},
        successful_trials=40,
        accounting_source=ACCOUNTING_PROVIDER_USAGE,
    )

    assert validity["inter_token_latency"]["state"] == "not_observed"


def test_provider_derived_length_metrics_are_not_called_client_tokenization() -> None:
    validity = assess_metric_validity(
        metrics=_metrics(),
        sizing={"claims_supported": False},
        successful_trials=40,
        accounting_source=ACCOUNTING_PROVIDER_USAGE,
    )

    # Under provider accounting this metric is provider-derived, so blaming the
    # client tokenizer would be wrong.
    assert validity["input_sequence_length"]["state"] == "valid"
    assert validity["input_sequence_length"]["workload_claims_supported"] is False
    assert "provider usage" in validity["input_sequence_length"]["reason"]


def test_legacy_report_is_readable_but_labelled(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    path.write_text(
        json.dumps({"schema_version": "0.1.0", "run_id": "old", "counters": {}})
    )

    document = load_report(path)

    assert document["legacy_schema"] is True
    assert "predates" in document["legacy_reason"]


def test_current_report_is_not_labelled_legacy(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    path.write_text(json.dumps({"schema_version": REPORT_SCHEMA_VERSION, "run_id": "new"}))

    document = load_report(path)

    assert document["legacy_schema"] is False


def test_unknown_report_schema_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    path.write_text(json.dumps({"schema_version": "9.0.0"}))

    with pytest.raises(ReportError, match="schema"):
        load_report(path)


def test_comparison_refuses_mismatched_token_accounting() -> None:
    base = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "run_id": "a",
        "deployment_key": "d",
        "profile_name": "p",
        "client_profile": {
            "name": "n",
            "identity": "x",
            "use_server_token_count": False,
            "token_accounting_source": "client_tokenizer",
        },
        "engine": {"version": "0.13.0", "schema_validated": True},
    }
    candidate = json.loads(json.dumps(base))
    candidate["client_profile"]["use_server_token_count"] = True
    candidate["client_profile"]["token_accounting_source"] = "provider_usage"

    with pytest.raises(ReportError, match="token accounting source differs"):
        compare_reports(base, candidate)


# -- 5. reprocessing published evidence ------------------------------------


def _legacy_run(tmp_path: Path, *, finish: str = "length", usage_accounting: bool = True) -> Path:
    """A completed run directory as version 0.1.0 wrote it.

    The stored report and trials carry the defect: 20 successes, no truncation
    counter, and a `sizing_verified` that came from the circular comparison.
    """
    run_dir = tmp_path / "screening" / "run-legacy00"
    artifact = run_dir / "aiperf"
    artifact.mkdir(parents=True)
    records = []
    raw = []
    for index in range(20):
        request_id = f"req-{index}"
        records.append(
            {
                "metadata": {
                    "x_request_id": request_id,
                    "was_cancelled": False,
                    "context_overflow_skip": False,
                },
                "metrics": {
                    "input_sequence_length": {"value": 1086},
                    "usage_prompt_tokens": {"value": 1086},
                    "output_sequence_length": {"value": 256},
                    "usage_completion_tokens": {"value": 256},
                    "usage_reasoning_tokens": {"value": 256},
                    "usage_prompt_cache_read_tokens": {"value": 0},
                    "time_to_first_token": {"value": 500.0, "unit": "ms"},
                    "request_latency": {"value": 1100.0, "unit": "ms"},
                },
            }
        )
        raw.append(
            _frame(
                request_id=request_id,
                finish_reason=finish,
                max_completion_tokens=256,
                usage={
                    "prompt_tokens": 1086,
                    "completion_tokens": 256,
                    "prompt_tokens_details": {"cached_tokens": 0, "created_cache_tokens": 1024},
                    "completion_tokens_details": {"reasoning_tokens": 256},
                },
            )
        )
    (artifact / "profile_export_aiperf.json").write_text(
        json.dumps({"schema_version": "1.4", "aiperf_version": "0.13.0"})
    )
    (artifact / "profile_export.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n"
    )
    (artifact / "profile_export_raw.jsonl").write_text(
        "\n".join(json.dumps(r) for r in raw) + "\n"
    )
    (run_dir / "aiperf-config.yaml").write_text("benchmark:\n  model: m\n")

    # Legacy trial records: every completed stream scored as a success.
    legacy_trials = [
        {
            "schema_version": "0.1.0",
            "trial_id": f"aiperf-{index:04d}",
            "outcome": "success",
            "outcome_detail": "completed",
            "attribution": "unknown",
            "finish_reason": None,
            "attempts": [{"attempt_index": 1, "http_status": 200}],
            "token_accounting": {
                "target_input_tokens": 1024,
                "client_estimated_input_tokens": 1086,
                "provider_input_tokens": 1086,
                "discrepancy_pct": 0.0,
                "tolerance_pct": 10.0,
                "sizing_verified": True,
            },
        }
        for index in range(20)
    ]
    (run_dir / "trials.jsonl").write_text(
        "\n".join(json.dumps(t) for t in legacy_trials) + "\n"
    )
    (run_dir / "report.json").write_text(
        json.dumps(
            {
                "schema_version": "0.1.0",
                "run_id": "run-legacy00",
                "deployment_key": "d",
                "profile_name": "screening-10k",
                "client_profile": {
                    "name": "baseline-normal-client-server-accounting",
                    "session_affinity": False,
                    "use_server_token_count": usage_accounting,
                    "token_accounting_source": (
                        "provider_usage" if usage_accounting else "client_tokenizer"
                    ),
                },
                "counters": {
                    "planned_trials": 20,
                    "successful_trials": 20,
                    "failed_trials": 0,
                    "http_attempts": 20,
                },
                "token_accounting": {"claims_supported": True, "within_tolerance": 20},
                "provenance": {"profile": {"name": "screening-10k", "planned_requests": 20}},
            }
        )
    )
    return run_dir


def test_reprocess_corrects_a_published_run_without_touching_it(tmp_path: Path) -> None:
    from endpoint_bench.reprocess import reprocess_run

    run_dir = _legacy_run(tmp_path)
    before = {
        name: (run_dir / name).read_bytes()
        for name in ("report.json", "trials.jsonl", "aiperf-config.yaml")
    }

    correction = reprocess_run(run_dir, tmp_path / "corrections" / "run-legacy00")

    # Source evidence is byte-identical afterwards.
    for name, content in before.items():
        assert (run_dir / name).read_bytes() == content

    assert correction["schema_version"] == "1.0"
    assert correction["accounting_source"] == ACCOUNTING_PROVIDER_USAGE
    assert correction["counters_before"]["successful_trials"] == 20
    assert correction["counters_after"]["successful_trials"] == 0
    assert correction["counters_after"]["truncated_trials"] == 20
    assert correction["completion"]["ceiling_truncated"] == 20
    assert correction["completion"]["latency_censored_fraction"] == 1.0
    assert correction["provider_telemetry"]["metrics"]["cache_write_tokens"]["sum"] == 20480

    changed = {item["field"]: item for item in correction["aggregate_field_changes"]}
    assert changed["counters.successful_trials"]["after"] == 0
    assert changed["counters.truncated_trials"]["before"] is None

    out = tmp_path / "corrections" / "run-legacy00"
    assert (out / "correction.json").is_file()
    stored = json.loads((out / "report.json").read_text())
    assert stored["schema_version"] == REPORT_SCHEMA_VERSION
    assert stored["reprocessed_from"]["source_report_schema_version"] == "0.1.0"
    assert stored["reprocessed_from"]["source_artifact_sha256"]["profile_export_raw.jsonl"]


def test_reprocess_lists_unrecoverable_client_estimate(tmp_path: Path) -> None:
    from endpoint_bench.reprocess import reprocess_run

    run_dir = _legacy_run(tmp_path)
    correction = reprocess_run(run_dir, tmp_path / "out")

    fields = " ".join(item["field"] for item in correction["unrecoverable"])
    assert "client_estimated_input_tokens" in fields
    assert "client_vs_provider" in fields
    # The corrected evidence must not invent the absent measure.
    assert correction["token_accounting"]["client_vs_provider"]["not_observed"] == 20
    assert correction["token_accounting"]["client_estimates_coinciding_with_provider"] == 0


def test_reprocess_refuses_to_write_inside_the_source_run(tmp_path: Path) -> None:
    from endpoint_bench.reprocess import ReprocessError, reprocess_run

    run_dir = _legacy_run(tmp_path)

    with pytest.raises(ReprocessError, match="refusing to write inside"):
        reprocess_run(run_dir, run_dir / "corrections")
    with pytest.raises(ReprocessError, match="refusing to write inside"):
        reprocess_run(run_dir, run_dir)


def test_reprocess_refuses_a_non_empty_output_directory(tmp_path: Path) -> None:
    from endpoint_bench.reprocess import ReprocessError, reprocess_run

    run_dir = _legacy_run(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    (out / "existing.txt").write_text("x")

    with pytest.raises(ReprocessError, match="not empty"):
        reprocess_run(run_dir, out)


def test_reprocess_requires_a_completed_run(tmp_path: Path) -> None:
    from endpoint_bench.reprocess import ReprocessError, reprocess_run

    incomplete = tmp_path / "incomplete"
    incomplete.mkdir()

    with pytest.raises(ReprocessError, match="no report.json"):
        reprocess_run(incomplete, tmp_path / "out")


# -- 6. plan and contract drift -------------------------------------------


def _plan_contract_module():
    """Load scripts/check_plan_contract.py without making scripts a package."""
    import importlib.util

    path = Path("scripts/check_plan_contract.py")
    spec = importlib.util.spec_from_file_location("check_plan_contract", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_committed_plan_and_contract_are_internally_consistent() -> None:
    module = _plan_contract_module()

    assert module.main() == 0


def test_plan_check_rejects_an_imbalanced_design() -> None:
    module = _plan_contract_module()
    plan = module._load(module.PLAN_PATH)
    profile = module._load(module.PROFILE_PATH)
    plan["runs"][0]["concurrency"] = plan["runs"][1]["concurrency"]

    with pytest.raises(module.CheckError, match="not balanced"):
        module.check_plan(plan, profile)


def test_plan_check_rejects_a_status_that_claims_execution() -> None:
    module = _plan_contract_module()
    plan = module._load(module.PLAN_PATH)
    profile = module._load(module.PROFILE_PATH)
    plan["status"] = "executed"

    with pytest.raises(module.CheckError, match="must not claim execution"):
        module.check_plan(plan, profile)


def test_contract_check_rejects_a_verdict_the_thresholds_do_not_support() -> None:
    module = _plan_contract_module()
    contract = module._load(module.CONTRACT_PATH)
    contract["performance_thresholds"]["objectives"][0]["threshold"] = 1000
    # One threshold filled, the rest still null, so still unratified overall.
    contract["verdict_logic"]["current_reachable_state"] = "qualified"

    with pytest.raises(module.CheckError, match=r"objective\(s\) unratified"):
        module.check_contract(contract)


def test_contract_check_rejects_a_schema_mismatch() -> None:
    module = _plan_contract_module()
    contract = module._load(module.CONTRACT_PATH)
    contract["evidence_requirements"]["report_schema_version"] = "0.0.1"

    with pytest.raises(module.CheckError, match="report schema"):
        module.check_contract(contract)


@pytest.mark.parametrize("packet", ["{broken-json", "not-json"])
def test_malformed_stream_followed_by_stop_is_not_success(tmp_path: Path, packet: str) -> None:
    raw = _frame()
    raw["responses"][0]["packets"].insert(1, {"name": "data", "value": packet})
    artifact = _artifact(tmp_path, records=[_record("req-0")], raw=[raw])
    results = parse_artifacts(artifact, deployment_key="d", profile_name="p")
    assert results.counters.succeeded == 0
    assert results.counters.failed == 1
    assert results.trials[0].outcome is OutcomeState.PROTOCOL_FAILURE
    assert results.trials[0].outcome_detail == "malformed_sse_json"
    assert results.counters.protocol_completed == 0
    assert completion_summary(results.trials)["natural_completions"] == 0
    assert any("malformed SSE" in note for note in results.trials[0].notes)


@pytest.mark.parametrize(
    ("reason", "finish_class"),
    [(None, "absent"), ("content_filter", "content_filter"),
     ("new_reason", "unknown"), ("length", "natural"), (None, "natural")],
)
def test_success_requires_consistent_natural_finish(reason: str | None, finish_class: str) -> None:
    from endpoint_bench.trials import HttpAttempt

    trial = _trial(outcome=OutcomeState.SUCCESS, finish_reason=reason, finish_class=finish_class)
    trial.attempts.append(HttpAttempt(1, http_status=200))
    with pytest.raises(TrialSchemaError, match="natural finish|contradicts finish"):
        count_trials([trial])


def test_provider_throughput_survives_target_mismatch_without_supporting_size_claims() -> None:
    validity = assess_metric_validity(
        metrics=_metrics(), sizing={"claims_supported": False}, successful_trials=40,
        accounting_source=ACCOUNTING_PROVIDER_USAGE,
    )
    assert validity["output_token_throughput"]["state"] == "valid"
    assert validity["output_token_throughput"]["workload_claims_supported"] is False
    assert "actually delivered" in validity["output_token_throughput"]["reason"]
    client = assess_metric_validity(
        metrics=_metrics(), sizing={"claims_supported": False}, successful_trials=40,
        accounting_source=ACCOUNTING_CLIENT_TOKENIZER,
    )
    assert client["output_token_throughput"]["state"] == "invalid_token_sizing"


@pytest.mark.parametrize("share", [0.025, 0.975, 1.0])
def test_censored_completion_percentiles_are_withheld_above_sample_floor(share: float) -> None:
    validity = assess_metric_validity(
        metrics=_metrics(), sizing={"claims_supported": True}, successful_trials=0,
        accounting_source=ACCOUNTING_PROVIDER_USAGE,
        completion={"latency_censored": True, "latency_censored_fraction": share},
    )
    latency = validity["request_latency"]
    assert latency["state"] == "censored"
    assert latency["percentiles_usable"] is False
    assert latency["completion_mean_usable"] is False
    assert latency["statistic_scope"] == "time_to_termination"
    assert validity["time_to_first_token"]["percentiles_usable"] is True


@pytest.mark.parametrize("mutation", ["spacing", "qualification", "pilot"])
def test_exploratory_plan_rejects_invalid_execution_design(mutation: str) -> None:
    import runpy

    check = runpy.run_path("scripts/check_plan_contract.py")
    plan = json.loads(Path("docs/gate3-plans/multiwindow-10k.json").read_text())
    profile = json.loads(Path("configs/profiles/qualification-10k.json").read_text())
    if mutation == "spacing":
        plan["design"]["window_start_offsets_minutes"] = [0, 1, 2, 3, 4, 5]
    elif mutation == "qualification":
        plan["purpose"] = "qualification"
    else:
        plan["pilot_gate"]["max_trials"] = 240
    with pytest.raises(check["CheckError"]):
        check["check_plan"](plan, profile)


def test_ratification_does_not_erase_other_inconclusive_reasons() -> None:
    import runpy

    check = runpy.run_path("scripts/check_plan_contract.py")
    contract = json.loads(Path("docs/qualification/10k-slo-contract.json").read_text())
    for objective in contract["performance_thresholds"]["objectives"]:
        objective["threshold"] = 1
    # The checker validates a specification, not actual evidence. Missing or
    # censored evidence may still prevent qualification after ratification.
    check["check_contract"](contract)


@pytest.mark.parametrize("streaming", [False, True])
def test_probe_output_ceiling_is_harness_attributed_and_counted(streaming: bool) -> None:
    import io
    import time

    from endpoint_bench.probes import _read_nonstreaming, _read_streaming
    from endpoint_bench.trials import HttpAttempt

    class Response(io.BytesIO):
        def getheader(self, name: str) -> str:
            return "text/event-stream"

    chunk = {"choices": [{"delta": {"content": "partial"}, "finish_reason": "length"}]}
    body = json.dumps(chunk).encode()
    if streaming:
        body = b"data: " + body + b"\n\ndata: [DONE]\n\n"
    trial = Trial(trial_id="probe-ceiling", deployment_key="d", profile_name="p")
    trial.observe(LifecycleState.STARTED)
    trial.observe(LifecycleState.TRANSPORT_COMPLETED)
    trial.attempts.append(HttpAttempt(1, http_status=200))
    reader = _read_streaming if streaming else _read_nonstreaming
    result = reader(trial, Response(body), time.perf_counter())
    assert result.outcome is OutcomeState.OUTPUT_TRUNCATED
    assert result.attribution == "harness"
    assert result.finish_class == FINISH_CEILING
    counters = count_trials([result])
    assert counters.truncated == 1
    assert counters.succeeded == 0
