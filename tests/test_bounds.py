"""Bounded completion latency: lower bounds may prove failure, never success."""

from __future__ import annotations

import hashlib
import json
import random
import sys
from pathlib import Path

import pytest

from endpoint_bench.bounds import (
    VERDICT_FAILED,
    VERDICT_INCONCLUSIVE,
    VERDICT_NOT_OBSERVED,
    LatencySample,
    bounded_value,
    evaluate_upper_bound,
    evaluate_upper_bound_by_concurrency,
    samples_from_trial_records,
)
from endpoint_bench.qualification import QualificationError


def _sample(pairs: list[tuple[float, bool]]) -> LatencySample:
    return LatencySample(
        values_ms=tuple(value for value, _ in pairs),
        censored=tuple(flag for _, flag in pairs),
    )


def _record(
    trial_id: str,
    *,
    outcome: str = "success",
    finish_class: str | None = "natural",
    duration_ms: float | None = 100.0,
    ttft_ms: float | None = 10.0,
    status: int | None = 200,
    requested_max: int | None = 8192,
) -> dict:
    attempts = [] if status is None else [
        {"attempt_index": 1, "http_status": status, "error_type": None}
    ]
    return {
        "schema_version": "0.2.0",
        "trial_id": trial_id,
        "outcome": outcome,
        "lifecycle": "protocol_completed",
        "attempts": attempts,
        "timing": {"duration_ms": duration_ms, "ttft_ms": ttft_ms},
        "completion": {
            "finish_reason": "stop" if finish_class == "natural" else "length",
            "finish_class": finish_class,
            "requested_max_completion_tokens": requested_max,
        },
    }



def test_uncensored_sample_matches_the_ordinary_percentile() -> None:
    sample = _sample([(100.0, False), (200.0, False), (300.0, False)])

    assert bounded_value(sample, "p50") == 200.0
    assert bounded_value(sample, "mean") == 200.0
    assert sample.censoring_fraction == 0.0


def test_censored_cap_hit_is_used_as_a_lower_bound() -> None:
    sample = _sample([(100.0, False), (200.0, True), (300.0, True)])

    assert bounded_value(sample, "p50") == 200.0
    assert sample.censored_count == 2
    assert sample.censoring_fraction == pytest.approx(2 / 3)


def test_lower_bound_above_threshold_proves_failure() -> None:
    sample = _sample([(1_000.0, False), (5_000.0, True), (6_000.0, True)])

    result = evaluate_upper_bound(
        sample, metric="request_latency", statistic="p50", threshold=2_000.0
    )

    assert result.verdict == VERDICT_FAILED
    assert result.lower_bound == 5_000.0  # p50 of [1000, 5000, 6000]
    assert "true completion statistic is at least" in result.note


def test_lower_bound_below_threshold_stays_inconclusive() -> None:
    sample = _sample([(100.0, False), (200.0, True), (300.0, True)])

    result = evaluate_upper_bound(
        sample, metric="request_latency", statistic="p95", threshold=10_000.0
    )

    assert result.verdict == VERDICT_INCONCLUSIVE
    assert "true value may be higher" in result.note


def test_unratified_threshold_cannot_be_met() -> None:
    sample = _sample([(100.0, False), (200.0, True)])

    result = evaluate_upper_bound(
        sample, metric="request_latency", statistic="p50", threshold=None
    )

    assert result.verdict == VERDICT_INCONCLUSIVE
    assert "unratified" in result.note


def test_empty_sample_is_not_observed() -> None:
    result = evaluate_upper_bound(
        LatencySample((), ()), metric="request_latency", statistic="p50", threshold=1.0
    )

    assert result.verdict == VERDICT_NOT_OBSERVED
    assert result.lower_bound is None
    assert result.sample_size == 0


def test_lower_bound_never_exceeds_the_true_statistic() -> None:
    """Property check: L_i <= T_i implies every sample quantile is a lower bound."""
    rng = random.Random(42)
    for _ in range(200):
        truths = [rng.uniform(1.0, 10_000.0) for _ in range(8)]
        lows = [value - rng.uniform(0.0, value) for value in truths]
        censored = [low < value for low, value in zip(lows, truths, strict=True)]
        bounded = LatencySample(tuple(lows), tuple(censored))
        exact = LatencySample(tuple(truths), tuple(False for _ in truths))

        for statistic in ("p50", "p95", "mean"):
            assert bounded_value(bounded, statistic) <= bounded_value(exact, statistic)


def test_only_upper_bound_objectives_are_supported() -> None:
    sample = _sample([(100.0, False)])

    with pytest.raises(QualificationError, match="upper-bound"):
        evaluate_upper_bound(
            sample,
            metric="request_latency",
            statistic="p50",
            threshold=100.0,
            operator=">=",
        )


def test_value_and_flag_lengths_must_match() -> None:
    with pytest.raises(QualificationError, match="one censoring flag per value"):
        LatencySample((1.0, 2.0), (False,))


def test_negative_latency_is_rejected() -> None:
    with pytest.raises(QualificationError, match="non-negative"):
        LatencySample((-1.0,), (False,))


def test_unknown_statistic_is_rejected() -> None:
    sample = _sample([(100.0, False)])

    with pytest.raises(QualificationError, match="not supported"):
        bounded_value(sample, "p99")


def test_concurrency_levels_are_evaluated_independently() -> None:
    samples = {
        1: _sample([(2_000.0, True), (3_000.0, True)]),
        2: _sample([(500.0, False), (600.0, False)]),
    }

    results = evaluate_upper_bound_by_concurrency(
        samples, metric="request_latency", statistic="p50", threshold=1_500.0
    )

    assert results[1].verdict == VERDICT_FAILED
    assert results[2].verdict == VERDICT_INCONCLUSIVE
    assert results[1].lower_bound == 2_500.0
    assert results[2].lower_bound == 550.0


def test_trial_records_split_natural_censored_and_failed() -> None:
    trials = [
        _record("t-1"),
        _record(
            "t-2",
            outcome="output_truncated",
            finish_class="ceiling",
            duration_ms=200.0,
            ttft_ms=20.0,
        ),
        {
            "trial_id": "t-3",
            "outcome": "failed",
            "completion": {},
            "timing": {},
        },
        _record("t-4", duration_ms=None, ttft_ms=None),
    ]

    samples = samples_from_trial_records(trials)

    assert samples.latency.values_ms == (100.0, 200.0)
    assert samples.latency.censored == (False, True)
    assert samples.ttft.values_ms == (10.0, 20.0)
    assert (samples.natural, samples.censored, samples.failed, samples.excluded) == (
        1,
        1,
        1,
        1,
    )
    assert samples.records_total == 4
    assert samples.denominators_consistent


def test_skipped_trial_is_excluded_not_counted_as_failure() -> None:
    samples = samples_from_trial_records(
        [{"trial_id": "t-skip", "outcome": "skipped", "completion": {}, "timing": {}}]
    )

    assert samples.failed == 0
    assert samples.excluded == 1
    assert samples.records_total == 1
    assert samples.latency.values_ms == ()


def test_nan_latency_is_rejected() -> None:
    with pytest.raises(QualificationError, match="must be finite"):
        samples_from_trial_records([_record("t-nan", duration_ms=float("nan"))])


def test_infinite_latency_is_rejected() -> None:
    with pytest.raises(QualificationError, match="must be finite"):
        samples_from_trial_records([_record("t-inf", duration_ms=float("inf"))])


def test_boolean_latency_is_rejected() -> None:
    with pytest.raises(QualificationError, match="must be a finite number"):
        samples_from_trial_records([_record("t-bool", duration_ms=True)])


def test_truncated_record_with_missing_finish_class_is_rejected() -> None:
    with pytest.raises(QualificationError, match="requires an explicit finish_class"):
        samples_from_trial_records(
            [_record("t-missing", outcome="output_truncated", finish_class=None)]
        )


def test_truncated_record_with_natural_finish_class_is_rejected() -> None:
    with pytest.raises(QualificationError, match="requires finish_class `ceiling`"):
        samples_from_trial_records(
            [_record("t-mixed", outcome="output_truncated", finish_class="natural")]
        )


def test_unknown_finish_class_is_rejected() -> None:
    with pytest.raises(QualificationError, match="unknown finish_class"):
        samples_from_trial_records([_record("t-unknown", finish_class="mystery")])


def test_success_without_observed_http_status_is_rejected() -> None:
    with pytest.raises(QualificationError, match="no observed HTTP status"):
        samples_from_trial_records([_record("t-nostatus", status=None)])


def test_success_with_non_2xx_status_is_rejected() -> None:
    with pytest.raises(QualificationError, match="contradicts HTTP status 500"):
        samples_from_trial_records([_record("t-500", status=500)])


def test_duplicate_trial_identity_is_rejected() -> None:
    with pytest.raises(QualificationError, match="duplicate trial identity"):
        samples_from_trial_records([_record("t-dup"), _record("t-dup")])


def _write_run(
    root: Path,
    name: str,
    *,
    level: int = 1,
    deployment: str = "endpoint-a",
    client_identity: str = "client-a",
    profile_name: str = "qualification-10k-8192",
    records: list[dict] | None = None,
) -> Path:
    run_dir = root / name
    run_dir.mkdir(parents=True)
    records = records or [
        _record("aiperf-0000", duration_ms=100.0, ttft_ms=10.0),
        _record("aiperf-0001", duration_ms=300.0, ttft_ms=30.0),
        _record(
            "aiperf-0002",
            outcome="output_truncated",
            finish_class="ceiling",
            duration_ms=500.0,
            ttft_ms=50.0,
        ),
    ]
    trials_text = "\n".join(json.dumps(record) for record in records) + "\n"
    (run_dir / "trials.jsonl").write_text(trials_text, encoding="utf-8")
    trials_sha = hashlib.sha256(trials_text.encode()).hexdigest()
    report = {
        "run_id": name,
        "deployment_key": deployment,
        "profile_name": profile_name,
        "client_profile": {
            "identity": client_identity,
            "name": "baseline-normal-client-server-accounting",
            "session_affinity": False,
            "request_timeout_s": 60.0,
        },
        "counters": {
            "planned_trials": len(records),
            "http_attempts": sum(len(record["attempts"]) for record in records),
        },
        "trials_sha256": trials_sha,
        "provenance": {
            "deployment": {
                "deployment_key": deployment,
                "model_id": "model-a",
                "protocol": "openai-chat-completions",
            },
            "profile": {
                "name": profile_name,
                "planned_requests": len(records),
                "requested_input_tokens": 10240,
                "requested_max_output_tokens": 8192,
            },
            "workload_type": "synthetic_single_turn",
            "affinity": {"mode": "baseline_no_affinity"},
        },
    }
    (run_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
    (run_dir / "aiperf-config.yaml").write_text(
        "benchmark:\n  phases:\n    type: \"concurrency\"\n"
        f"    concurrency: {level}\n    requests: {len(records)}\n",
        encoding="utf-8",
    )
    return run_dir


def _load_analyzer():
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    sys.path.insert(0, str(scripts))
    try:
        import analyze_bounded
    finally:
        sys.path.remove(str(scripts))
    return analyze_bounded


def test_analysis_script_pools_a_run_and_writes_json(tmp_path: Path) -> None:
    analyze_bounded = _load_analyzer()
    run_dir = _write_run(tmp_path, "run-w1-c1")
    output = tmp_path / "analysis.json"

    assert analyze_bounded.main(["--run", f"1:{run_dir}", "--out", str(output)]) == 0

    document = json.loads(output.read_text(encoding="utf-8"))
    level = document["levels"]["1"]
    assert level["sample_size"] == 3
    assert level["natural_completions"] == 2
    assert level["ceiling_truncated"] == 1
    assert level["censoring_fraction"] == pytest.approx(1 / 3)
    assert level["request_latency"]["p50"]["lower_bound"] == 300.0
    assert level["time_to_first_token"]["p50"]["lower_bound"] == 30.0
    assert level["denominators"]["records_total"] == 3
    assert level["denominators"]["partition_consistent"] is True


def test_analysis_script_rejects_the_same_run_twice(tmp_path: Path) -> None:
    analyze_bounded = _load_analyzer()
    run_dir = _write_run(tmp_path, "run-w1-c1")

    exit_code = analyze_bounded.main(["--run", f"1:{run_dir}", "--run", f"1:{run_dir}"])

    assert exit_code == 2


def test_analysis_script_rejects_caller_concurrency_mismatch(tmp_path: Path) -> None:
    analyze_bounded = _load_analyzer()
    run_dir = _write_run(tmp_path, "run-w1-c1", level=2)

    assert (
        analyze_bounded.main(["--run", f"1:{run_dir}", "--out", str(tmp_path / "a.json")])
        == 2
    )


def test_analysis_script_rejects_incompatible_pooling(tmp_path: Path) -> None:
    analyze_bounded = _load_analyzer()
    first = _write_run(tmp_path, "run-w1-c1", client_identity="client-a")
    second = _write_run(tmp_path, "run-w2-c1", client_identity="client-b")

    assert (
        analyze_bounded.main(
            ["--run", f"1:{first}", "--run", f"1:{second}", "--out", str(tmp_path / "a.json")]
        )
        == 2
    )


def test_analysis_script_rejects_tampered_trials(tmp_path: Path) -> None:
    analyze_bounded = _load_analyzer()
    run_dir = _write_run(tmp_path, "run-w1-c1")
    (run_dir / "trials.jsonl").write_text(
        json.dumps(_record("aiperf-9999")) + "\n", encoding="utf-8"
    )

    assert (
        analyze_bounded.main(["--run", f"1:{run_dir}", "--out", str(tmp_path / "a.json")])
        == 2
    )


def test_analysis_requires_declared_levels_and_windows(tmp_path: Path) -> None:
    analyze_bounded = _load_analyzer()
    first = _write_run(tmp_path, "run-w1-c1")
    second = _write_run(tmp_path, "run-w2-c1")

    assert (
        analyze_bounded.main(
            [
                "--run",
                f"1:{first}",
                "--run",
                f"1:{second}",
                "--require-level",
                "1",
                "--require-windows",
                "2",
                "--out",
                str(tmp_path / "coverage-ok.json"),
            ]
        )
        == 0
    )
    assert (
        analyze_bounded.main(
            [
                "--run",
                f"1:{first}",
                "--run",
                f"1:{second}",
                "--require-level",
                "2",
                "--out",
                str(tmp_path / "missing-level.json"),
            ]
        )
        == 2
    )
    assert (
        analyze_bounded.main(
            [
                "--run",
                f"1:{first}",
                "--run",
                f"1:{second}",
                "--require-windows",
                "3",
                "--out",
                str(tmp_path / "missing-window.json"),
            ]
        )
        == 2
    )


def test_lifecycle_contradicting_outcome_is_rejected() -> None:
    record = _record("t-lifecycle")
    record["lifecycle"] = "started"
    with pytest.raises(QualificationError, match="requires lifecycle `protocol_completed`"):
        samples_from_trial_records([record])


def test_skipped_lifecycle_with_success_outcome_is_rejected() -> None:
    record = _record("t-skip-mismatch")
    record["lifecycle"] = "skipped"
    with pytest.raises(QualificationError, match="contradicts outcome"):
        samples_from_trial_records([record])
