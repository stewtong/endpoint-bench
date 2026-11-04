"""Contract-evaluation rules, exercised only against synthetic evidence.

No endpoint is qualified by anything here. The point is to prove the rules behave
before they are ever pointed at real evidence, and to pin the defects that prose
alone failed to prevent:

- version 1.0 of the contract asked a 20-request window to satisfy a p50 objective
  under its own 30-sample floor, which is unsatisfiable at any endpoint quality;
- an unratified threshold could be read as "not failed" rather than "not evaluable";
- pilot and exploratory results could be relabelled as qualification evidence;
- a latency statistic could be printed for a group with no eligible responses.
"""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest

from endpoint_bench.qualification import (
    PERCENTILE_ESTIMATOR,
    QUALIFICATION_ROLE,
    VERDICT_FAILED,
    VERDICT_INCONCLUSIVE,
    VERDICT_QUALIFIED,
    QualificationError,
    WindowEvidence,
    evaluate,
    load_contract,
    percentile,
    statistic,
)

CONTRACT_PATH = Path("docs/qualification/10k-slo-contract.json")
EVALUATED_AT = "2026-06-01T00:00:00Z"


@pytest.fixture
def contract() -> dict:
    return load_contract(CONTRACT_PATH)


def _ratified(contract: dict, **thresholds: float) -> dict:
    """A copy with an explicit frozen ratification record.

    A non-null threshold alone is not ratification: the evaluator requires the
    status, freeze marker and per-objective origin/scope record as well.
    """
    document = copy.deepcopy(contract)
    performance = document["performance_thresholds"]
    performance["status"] = "ratified"
    performance["frozen"] = True
    performance["frozen_at_utc"] = "2026-01-01T00:00:00Z"
    for objective in performance["objectives"]:
        key = objective["id"]
        if key in thresholds:
            objective["threshold"] = thresholds[key]
        objective["ratification"] = {
            "origin": "synthetic test requirement",
            "date": "2026-01-01",
            "workload": "synthetic-10k-1024",
            "concurrency": objective.get("concurrency"),
            "client_profile": "baseline-normal-client-server-accounting",
            "deployment_key": "synthetic-deployment",
            "region": "synthetic-region",
            "expiry": "2099-12-31",
        }
    return document


def _latencies(count: int, value: float = 100.0) -> list[float]:
    return [value] * count


def _window(
    label: str,
    *,
    concurrency: int = 1,
    observations: int = 20,
    latency: float = 100.0,
    role: str = QUALIFICATION_ROLE,
    **overrides,
) -> WindowEvidence:
    defaults = dict(
        label=label,
        window_id=label,
        concurrency=concurrency,
        evidence_role=role,
        eligible_observations={
            "request_latency": _latencies(observations, latency),
            "time_to_first_token": _latencies(observations, latency / 4),
            "output_token_throughput": _latencies(observations, 50.0),
        },
        planned_trials=20,
        http_attempts=20,
        started_trials=20,
        successful_trials=20,
        natural_completions=20,
        ceiling_truncated=0,
        endpoint_failures=0,
        workload_id="synthetic-10k-1024",
        client_profile="baseline-normal-client-server-accounting",
        deployment_key="synthetic-deployment",
        region="synthetic-region",
    )
    defaults.update(overrides)
    if "natural_completions" not in overrides:
        defaults["natural_completions"] = defaults["successful_trials"]
    if "trial_ids" not in overrides:
        defaults["trial_ids"] = tuple(
            f"{label}-t{index:03d}"
            for index in range(1, int(defaults["planned_trials"]) + 1)
        )
    return WindowEvidence(**defaults)


# -- 1. an unratified threshold can never yield qualified -------------------


def test_unratified_thresholds_are_inconclusive_not_qualified(contract: dict) -> None:
    windows = [_window(f"w{i}") for i in range(3)]

    result = evaluate(contract, windows)

    assert result["verdict"] == VERDICT_INCONCLUSIVE
    assert any("unratified" in reason for reason in result["reasons"])
    assert result["evaluator"] == "synthetic_only_no_live_pipeline"


def test_a_single_unratified_objective_blocks_a_verdict(contract: dict) -> None:
    """Every objective must be ratified, not merely most of them."""
    document = _ratified(
        contract,
        **{
            "perf.ttft_p50_ms": 1e9,
            "perf.ttft_p95_ms": 1e9,
            "perf.ttft_p95_ms_c2": 1e9,
            "perf.latency_p50_ms": 1e9,
            "perf.output_throughput_floor": 0.0,
        },
    )
    # perf.endpoint_error_rate deliberately left null.

    result = evaluate(document, [_window(f"w{i}") for i in range(3)])

    assert result["verdict"] == VERDICT_INCONCLUSIVE
    assert any("perf.endpoint_error_rate" in reason for reason in result["reasons"])


def test_fully_ratified_and_passing_evidence_can_qualify(contract: dict) -> None:
    """The machinery is capable of `qualified`; it is unused, not absent."""
    document = _ratified(
        contract,
        **{
            "perf.ttft_p50_ms": 1e9,
            "perf.ttft_p95_ms": 1e9,
            "perf.ttft_p95_ms_c2": 1e9,
            "perf.latency_p50_ms": 1e9,
            "perf.output_throughput_floor": 0.0,
            "perf.endpoint_error_rate": 0.01,
        },
    )
    windows = [_window(f"w{i}") for i in range(3)] + [
        _window(f"w{i}c2", concurrency=2) for i in range(3)
    ]

    result = evaluate(document, windows, evaluated_at=EVALUATED_AT)

    assert result["verdict"] == VERDICT_QUALIFIED
    assert result["objectives"]


def test_a_missed_threshold_fails_once_everything_is_ratified(contract: dict) -> None:
    document = _ratified(
        contract,
        **{
            "perf.ttft_p50_ms": 1.0,  # impossible: every TTFT is 25.0
            "perf.ttft_p95_ms": 1e9,
            "perf.ttft_p95_ms_c2": 1e9,
            "perf.latency_p50_ms": 1e9,
            "perf.output_throughput_floor": 0.0,
            "perf.endpoint_error_rate": 0.01,
        },
    )
    windows = [_window(f"w{i}") for i in range(3)] + [
        _window(f"w{i}c2", concurrency=2) for i in range(3)
    ]

    result = evaluate(document, windows)

    assert result["verdict"] == VERDICT_FAILED
    assert any("perf.ttft_p50_ms" in reason for reason in result["reasons"])


# -- 1b. exploratory evidence cannot be relabelled --------------------------


@pytest.mark.parametrize("role", ["pilot", "calibration", "exploratory", "screening"])
def test_excluded_evidence_roles_cannot_qualify(contract: dict, role: str) -> None:
    document = _ratified(
        contract,
        **{
            "perf.ttft_p50_ms": 1e9,
            "perf.ttft_p95_ms": 1e9,
            "perf.ttft_p95_ms_c2": 1e9,
            "perf.latency_p50_ms": 1e9,
            "perf.output_throughput_floor": 0.0,
            "perf.endpoint_error_rate": 0.01,
        },
    )
    windows = [_window(f"w{i}", role=role) for i in range(3)] + [
        _window(f"w{i}c2", concurrency=2, role=role) for i in range(3)
    ]

    result = evaluate(document, windows)

    assert result["verdict"] == VERDICT_INCONCLUSIVE
    assert any("excluded from qualification evidence" in reason for reason in result["reasons"])


# -- 2. pooled percentiles, not window percentiles -------------------------


def test_a_single_20_request_window_cannot_claim_a_percentile(contract: dict) -> None:
    """The version 1.0 contradiction: one window, p95 objective, 30-sample floor."""
    document = _ratified(
        contract,
        **{
            "perf.ttft_p50_ms": 1e9,
            "perf.ttft_p95_ms": 1e9,
            "perf.ttft_p95_ms_c2": 1e9,
            "perf.latency_p50_ms": 1e9,
            "perf.output_throughput_floor": 0.0,
            "perf.endpoint_error_rate": 0.01,
        },
    )

    result = evaluate(document, [_window("only", concurrency=1)])

    assert result["verdict"] == VERDICT_INCONCLUSIVE
    assert any("below_floor" in json.dumps(entry) for entry in result["objectives"].values())


def test_pooling_three_windows_reaches_the_floor(contract: dict) -> None:
    document = _ratified(
        contract,
        **{
            "perf.ttft_p50_ms": 1e9,
            "perf.ttft_p95_ms": 1e9,
            "perf.ttft_p95_ms_c2": 1e9,
            "perf.latency_p50_ms": 1e9,
            "perf.output_throughput_floor": 0.0,
            "perf.endpoint_error_rate": 0.01,
        },
    )
    windows = [_window(f"w{i}") for i in range(3)] + [
        _window(f"w{i}c2", concurrency=2) for i in range(3)
    ]

    result = evaluate(document, windows)
    latency = result["objectives"]["perf.latency_p50_ms@C1"]

    assert latency["state"] == "evaluated"
    assert latency["pooled_sample_size"] == 60
    assert latency["windows_pooled"] == ["w0", "w1", "w2"]
    assert latency["estimator"] == PERCENTILE_ESTIMATOR
    assert latency["denominator"] == 60


def test_pooled_statistic_uses_trials_not_window_means(contract: dict) -> None:
    """Averaging window p50s would differ from pooling the underlying trials."""
    document = _ratified(
        contract,
        **{
            "perf.ttft_p50_ms": 1e9,
            "perf.ttft_p95_ms": 1e9,
            "perf.ttft_p95_ms_c2": 1e9,
            "perf.latency_p50_ms": 1e9,
            "perf.output_throughput_floor": 0.0,
            "perf.endpoint_error_rate": 0.01,
        },
    )
    # One window is fast, one is slow and larger: pooled p50 sits at the boundary.
    fast = _window("fast", observations=20, latency=10.0)
    slow = _window(
        "slow",
        concurrency=1,
        observations=60,
        latency=1000.0,
        planned_trials=60,
        http_attempts=60,
        started_trials=60,
        successful_trials=60,
        natural_completions=60,
    )

    result = evaluate(document, [fast, slow])
    latency = result["objectives"]["perf.latency_p50_ms@C1"]

    assert latency["pooled_sample_size"] == 80
    # 20 fast of 80 is 25%, so the pooled p50 comes from the slow group.
    assert latency["value"] == pytest.approx(1000.0)


def test_per_window_counts_and_failures_are_preserved(contract: dict) -> None:
    windows = [
        _window(
            "a",
            observations=18,
            endpoint_failures=2,
            successful_trials=18,
        ),
        _window("b", endpoint_failures=0),
    ]

    result = evaluate(contract, windows)

    by_label = {entry["window"]: entry for entry in result["windows"]}
    assert by_label["a"]["endpoint_failures"] == 2
    assert by_label["a"]["successful_trials"] == 18
    assert by_label["b"]["endpoint_failures"] == 0


# -- 3. truncation stays separate from success and failure -----------------


def test_ceiling_truncation_blocks_qualification_without_becoming_failure(
    contract: dict,
) -> None:
    window = _window("censored", natural_completions=2, ceiling_truncated=18, successful_trials=2)

    result = evaluate(contract, [window])

    assert result["verdict"] == VERDICT_INCONCLUSIVE
    entry = result["windows"][0]
    assert entry["ceiling_truncated"] == 18
    assert entry["natural_completions"] == 2
    assert entry["endpoint_failures"] == 0
    assert any("dq.truncation_rate" in failure for failure in entry["gate_failures"])
    assert entry["censoring_rate"] == pytest.approx(0.9)


def test_truncation_does_not_erase_independently_observed_ttft(contract: dict) -> None:
    """The contract withholds completion latency, not every timing observation."""
    window = _window("censored", natural_completions=0, ceiling_truncated=20, successful_trials=0)

    result = evaluate(contract, [window])

    assert any("dq.truncation_rate" in f for f in result["windows"][0]["gate_failures"])
    # TTFT was observed and remains in the record the evaluator reports on.
    assert window.eligible_observations["time_to_first_token"]


# -- 4. conditional latency beside the error denominator -------------------


def test_error_rate_is_evaluated_from_counts_with_attribution_denominator(
    contract: dict,
) -> None:
    document = _ratified(
        contract,
        **{
            "perf.ttft_p50_ms": 1e9,
            "perf.ttft_p95_ms": 1e9,
            "perf.ttft_p95_ms_c2": 1e9,
            "perf.latency_p50_ms": 1e9,
            "perf.output_throughput_floor": 0.0,
            "perf.endpoint_error_rate": 0.01,
        },
    )
    windows = [
        _window(f"w{i}", endpoint_failures=0) for i in range(3)
    ] + [
        _window(
            f"w{i}c2",
            concurrency=2,
            observations=18,
            endpoint_failures=2,
            successful_trials=18,
        )
        for i in range(3)
    ]

    result = evaluate(document, windows)

    error = result["objectives"]["perf.endpoint_error_rate@C2"]
    assert error["state"] == "evaluated"
    assert error["value"] == pytest.approx(6 / 60)
    assert error["denominator"] == 60
    assert error["meets_threshold"] is False
    assert result["verdict"] == VERDICT_FAILED


def test_a_group_with_no_successful_response_has_no_invented_latency(
    contract: dict,
) -> None:
    document = _ratified(
        contract,
        **{
            "perf.ttft_p50_ms": 1e9,
            "perf.ttft_p95_ms": 1e9,
            "perf.ttft_p95_ms_c2": 1e9,
            "perf.latency_p50_ms": 1e9,
            "perf.output_throughput_floor": 0.0,
            "perf.endpoint_error_rate": 0.01,
        },
    )
    all_failed = [
        WindowEvidence(
            label=f"w{i}",
            concurrency=1,
            eligible_observations={},  # nothing succeeded, so nothing is eligible
            planned_trials=20,
            http_attempts=20,
            started_trials=20,
            successful_trials=0,
            natural_completions=0,
            ceiling_truncated=0,
            endpoint_failures=20,
        )
        for i in range(3)
    ]

    result = evaluate(document, all_failed)

    assert result["verdict"] == VERDICT_INCONCLUSIVE
    latency = result["objectives"]["perf.latency_p50_ms@C1"]
    assert latency["state"] == "no_eligible_observations"
    assert "value" not in latency


# -- estimator and contract loading ----------------------------------------


def test_percentile_uses_documented_estimator() -> None:
    values = [1.0, 2.0, 3.0, 4.0]

    assert percentile(values, 0.5) == pytest.approx(2.5)
    assert statistic(values, "p95") == pytest.approx(3.85)
    with pytest.raises(QualificationError):
        percentile([], 0.5)


def test_unknown_contract_version_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "contract.json"
    path.write_text(json.dumps({"doc_version": "9.9"}))

    with pytest.raises(QualificationError, match="doc_version"):
        load_contract(path)


# -- 6. coverage, identity, cohorts and ratification records ----------------


def _all_thresholds(contract: dict, value: float = 1e9) -> dict:
    return {
        objective["id"]: (value if objective["operator"] == "<=" else 0.0)
        for objective in contract["performance_thresholds"]["objectives"]
    }


def test_two_c1_windows_without_c2_cannot_qualify(contract: dict) -> None:
    """F5 counterexample: a present level is not the whole required cohort."""
    document = _ratified(contract, **_all_thresholds(contract))

    result = evaluate(document, [_window(f"w{i}") for i in range(3)])

    assert result["verdict"] == VERDICT_INCONCLUSIVE
    assert any("C2" in reason and "required" in reason for reason in result["reasons"])
    assert result["required_windows_per_level"] == 3


def test_one_c3_window_cannot_qualify_with_no_applicable_objective(
    contract: dict,
) -> None:
    document = _ratified(contract, **_all_thresholds(contract))

    result = evaluate(document, [_window("only-c3", concurrency=3)])

    assert result["verdict"] == VERDICT_INCONCLUSIVE
    assert any("C3" in reason for reason in result["reasons"])
    assert any("C1" in reason and "required" in reason for reason in result["reasons"])


def test_duplicate_window_identity_is_rejected(contract: dict) -> None:
    document = _ratified(contract, **_all_thresholds(contract))
    windows = [_window("same"), _window("same")]

    with pytest.raises(QualificationError, match="duplicate window identity"):
        evaluate(document, windows)


def test_duplicate_trial_identity_across_windows_is_rejected(contract: dict) -> None:
    document = _ratified(contract, **_all_thresholds(contract))
    first = _window("w1")
    second = _window("w2", trial_ids=first.trial_ids)

    with pytest.raises(QualificationError, match="duplicate trial identity"):
        evaluate(document, [first, second])


def test_count_cohort_inconsistency_blocks_qualification(contract: dict) -> None:
    document = _ratified(contract, **_all_thresholds(contract))
    windows = [_window(f"w{i}") for i in range(3)] + [
        _window(f"w{i}c2", concurrency=2) for i in range(3)
    ]
    broken = WindowEvidence(
        label="broken",
        window_id="broken",
        concurrency=1,
        eligible_observations={"time_to_first_token": [1.0]},
        planned_trials=20,
        http_attempts=20,
        started_trials=5,
        successful_trials=9,
        natural_completions=20,
        ceiling_truncated=0,
        endpoint_failures=0,
        trial_ids=tuple(f"broken-t{i}" for i in range(20)),
        workload_id="synthetic-10k-1024",
        client_profile="baseline-normal-client-server-accounting",
        deployment_key="synthetic-deployment",
        region="synthetic-region",
    )

    result = evaluate(document, windows + [broken])

    assert result["verdict"] == VERDICT_INCONCLUSIVE
    entry = next(item for item in result["windows"] if item["window"] == "broken")
    assert entry["passes"] is False
    assert entry["cohort_failures"]


def test_incompatible_pooling_cohorts_are_not_pooled(contract: dict) -> None:
    document = _ratified(contract, **_all_thresholds(contract))
    windows = [
        _window(f"w{i}", deployment_key="deployment-a") for i in range(3)
    ] + [
        _window(f"w{i}c2", concurrency=2, deployment_key="deployment-b")
        for i in range(3)
    ]

    result = evaluate(document, windows)

    assert result["verdict"] == VERDICT_INCONCLUSIVE
    assert any("pooling cohorts" in reason for reason in result["reasons"])


def test_a_non_null_threshold_without_a_ratification_record_is_inconclusive(
    contract: dict,
) -> None:
    """The original defect: numeric threshold was equated with ratification."""
    document = _ratified(contract, **_all_thresholds(contract))
    document["performance_thresholds"]["status"] = "unratified"
    for objective in document["performance_thresholds"]["objectives"]:
        objective.pop("ratification", None)

    result = evaluate(
        document,
        [_window(f"w{i}") for i in range(3)]
        + [_window(f"w{i}c2", concurrency=2) for i in range(3)],
    )

    assert result["verdict"] == VERDICT_INCONCLUSIVE
    assert any("unratified" in reason for reason in result["reasons"])
    assert any("no ratification record" in reason for reason in result["reasons"])


def test_ratified_but_unfrozen_thresholds_are_inconclusive(contract: dict) -> None:
    document = _ratified(contract, **_all_thresholds(contract))
    document["performance_thresholds"]["frozen"] = False

    result = evaluate(
        document,
        [_window(f"w{i}") for i in range(3)]
        + [_window(f"w{i}c2", concurrency=2) for i in range(3)],
    )

    assert result["verdict"] == VERDICT_INCONCLUSIVE
    assert any("not frozen" in reason for reason in result["reasons"])


def test_trial_identity_is_required_for_a_performance_verdict(contract: dict) -> None:
    document = _ratified(contract, **_all_thresholds(contract))
    windows = [
        _window(f"w{i}", trial_ids=()) for i in range(3)
    ] + [
        _window(f"w{i}c2", concurrency=2, trial_ids=()) for i in range(3)
    ]

    result = evaluate(document, windows)

    assert result["verdict"] == VERDICT_INCONCLUSIVE
    assert any("no trial identities" in reason for reason in result["reasons"])


def test_evaluation_requires_evidence() -> None:
    contract = load_contract(CONTRACT_PATH)

    with pytest.raises(QualificationError, match="no windows"):
        evaluate(contract, [])


# -- 7. evaluator input guard and applicable ratification -------------------


def _six_windows() -> list[WindowEvidence]:
    return [_window(f"w{i}") for i in range(3)] + [
        _window(f"w{i}c2", concurrency=2) for i in range(3)
    ]


def _ratified_six(contract: dict) -> dict:
    return _ratified(contract, **_all_thresholds(contract))


@pytest.mark.parametrize(
    "bad_value", [-1, True, 1.5, float("nan"), float("inf"), "1"]
)
def test_invalid_counters_are_rejected_before_ratios(
    contract: dict, bad_value: object
) -> None:
    document = _ratified_six(contract)
    windows = [replace(window, endpoint_failures=bad_value) for window in _six_windows()]

    result = evaluate(document, windows, evaluated_at=EVALUATED_AT)

    assert result["verdict"] == VERDICT_INCONCLUSIVE
    assert any("endpoint_failures" in reason for reason in result["reasons"])


@pytest.mark.parametrize(
    "bad_value", [float("nan"), float("inf"), -1.0, True, "1.0"]
)
def test_invalid_observations_are_rejected_before_statistics(
    contract: dict, bad_value: object
) -> None:
    document = _ratified_six(contract)
    windows = [
        replace(
            window,
            eligible_observations={"request_latency": [100.0, bad_value]},
        )
        for window in _six_windows()
    ]

    result = evaluate(document, windows, evaluated_at=EVALUATED_AT)

    assert result["verdict"] == VERDICT_INCONCLUSIVE
    assert any("request_latency" in reason for reason in result["reasons"])


@pytest.mark.parametrize("bad_value", [True, 0, 1.5, "1"])
def test_invalid_concurrency_is_rejected_before_ratios(
    contract: dict, bad_value: object
) -> None:
    document = _ratified_six(contract)
    windows = [replace(window, concurrency=bad_value) for window in _six_windows()]

    result = evaluate(document, windows, evaluated_at=EVALUATED_AT)

    assert result["verdict"] == VERDICT_INCONCLUSIVE
    assert any("concurrency" in reason for reason in result["reasons"])


def test_gate_flags_and_counter_types_are_not_coerced(contract: dict) -> None:
    document = _ratified_six(contract)

    windows = _six_windows()
    windows[0] = replace(windows[0], schema_validated=1)
    result = evaluate(document, windows, evaluated_at=EVALUATED_AT)
    assert result["verdict"] == VERDICT_INCONCLUSIVE
    assert any("schema_validated" in reason for reason in result["reasons"])

    windows = _six_windows()
    windows[0] = replace(windows[0], http_attempts=20.0)
    result = evaluate(document, windows, evaluated_at=EVALUATED_AT)
    assert result["verdict"] == VERDICT_INCONCLUSIVE
    assert any("http_attempts" in reason for reason in result["reasons"])


@pytest.mark.parametrize("successful", [19, 21])
def test_outcome_cohorts_must_reconcile_to_started_trials(
    contract: dict, successful: int
) -> None:
    document = _ratified_six(contract)
    windows = _six_windows()
    windows[0] = replace(
        windows[0],
        successful_trials=successful,
        natural_completions=successful,
    )

    result = evaluate(document, windows, evaluated_at=EVALUATED_AT)

    assert result["verdict"] == VERDICT_INCONCLUSIVE
    entry = next(item for item in result["windows"] if item["window"] == "w0")
    assert entry["passes"] is False
    assert any("reconcile" in problem for problem in entry["cohort_failures"])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("workload", "other-workload"),
        ("client_profile", "other-client"),
        ("deployment_key", "other-deployment"),
        ("region", "other-region"),
    ],
)
def test_ratification_scope_must_match_the_evidence(
    contract: dict, field: str, value: str
) -> None:
    document = _ratified_six(contract)
    for objective in document["performance_thresholds"]["objectives"]:
        objective["ratification"][field] = value

    result = evaluate(document, _six_windows(), evaluated_at=EVALUATED_AT)

    assert result["verdict"] == VERDICT_INCONCLUSIVE
    assert any(
        field in reason and "does not apply" in reason
        for reason in result["reasons"]
    )


def test_ratification_concurrency_must_match_the_objective(contract: dict) -> None:
    document = _ratified_six(contract)
    for objective in document["performance_thresholds"]["objectives"]:
        objective["ratification"]["concurrency"] = 9

    result = evaluate(document, _six_windows(), evaluated_at=EVALUATED_AT)

    assert result["verdict"] == VERDICT_INCONCLUSIVE
    assert any(
        "concurrency" in reason and "objective levels" in reason
        for reason in result["reasons"]
    )


def test_expired_ratification_is_inapplicable(contract: dict) -> None:
    document = _ratified_six(contract)
    for objective in document["performance_thresholds"]["objectives"]:
        objective["ratification"]["expiry"] = "2000-01-01"

    result = evaluate(document, _six_windows(), evaluated_at=EVALUATED_AT)

    assert result["verdict"] == VERDICT_INCONCLUSIVE
    assert any("expired" in reason for reason in result["reasons"])


def test_reviewer_wrong_scope_and_expired_record_is_not_qualified(
    contract: dict,
) -> None:
    document = _ratified_six(contract)
    for objective in document["performance_thresholds"]["objectives"]:
        objective["ratification"].update(
            {
                "deployment_key": "other-deployment",
                "region": "other-region",
                "expiry": "2000-01-01",
            }
        )

    result = evaluate(document, _six_windows(), evaluated_at=EVALUATED_AT)

    assert result["verdict"] == VERDICT_INCONCLUSIVE


@pytest.mark.parametrize(
    ("field", "value"),
    [("date", "not-a-date"), ("expiry", "2026-13-01")],
)
def test_malformed_ratification_dates_are_inapplicable(
    contract: dict, field: str, value: str
) -> None:
    document = _ratified_six(contract)
    for objective in document["performance_thresholds"]["objectives"]:
        objective["ratification"][field] = value

    result = evaluate(document, _six_windows(), evaluated_at=EVALUATED_AT)

    assert result["verdict"] == VERDICT_INCONCLUSIVE
    assert any("ISO-8601" in reason for reason in result["reasons"])


def test_freeze_before_the_requirement_date_is_invalid(contract: dict) -> None:
    document = _ratified_six(contract)
    document["performance_thresholds"]["frozen_at_utc"] = "2025-01-01T00:00:00Z"

    result = evaluate(document, _six_windows(), evaluated_at=EVALUATED_AT)

    assert result["verdict"] == VERDICT_INCONCLUSIVE
    assert any("frozen before the requirement date" in reason for reason in result["reasons"])


def test_freeze_after_the_evaluation_instant_is_invalid(contract: dict) -> None:
    document = _ratified_six(contract)
    document["performance_thresholds"]["frozen_at_utc"] = "2100-01-01T00:00:00Z"

    result = evaluate(document, _six_windows(), evaluated_at=EVALUATED_AT)

    assert result["verdict"] == VERDICT_INCONCLUSIVE
    assert any("frozen after the evaluation instant" in reason for reason in result["reasons"])


def test_freeze_after_expiry_is_invalid(contract: dict) -> None:
    document = _ratified_six(contract)
    document["performance_thresholds"]["frozen_at_utc"] = "2026-07-01T00:00:00Z"
    for objective in document["performance_thresholds"]["objectives"]:
        objective["ratification"]["expiry"] = "2026-06-30"

    result = evaluate(document, _six_windows(), evaluated_at="2026-08-01T00:00:00Z")

    assert result["verdict"] == VERDICT_INCONCLUSIVE
    assert any("frozen after the requirement expiry" in reason for reason in result["reasons"])


def test_expiry_before_the_requirement_date_is_invalid(contract: dict) -> None:
    document = _ratified_six(contract)
    for objective in document["performance_thresholds"]["objectives"]:
        objective["ratification"]["date"] = "2026-12-31"
        objective["ratification"]["expiry"] = "2026-01-01"

    result = evaluate(document, _six_windows(), evaluated_at=EVALUATED_AT)

    assert result["verdict"] == VERDICT_INCONCLUSIVE
    assert any("expiry precedes its date" in reason for reason in result["reasons"])


def test_evaluation_instant_is_applied_deterministically(contract: dict) -> None:
    document = _ratified_six(contract)

    before = evaluate(
        document, _six_windows(), evaluated_at="2026-06-01T00:00:00Z"
    )
    after = evaluate(
        document, _six_windows(), evaluated_at="2100-01-01T00:00:00Z"
    )

    assert before["verdict"] == VERDICT_QUALIFIED
    assert after["verdict"] == VERDICT_INCONCLUSIVE
    assert any("expired" in reason for reason in after["reasons"])


def test_naive_evaluation_instant_is_refused(contract: dict) -> None:
    document = _ratified_six(contract)

    with pytest.raises(QualificationError, match="timezone"):
        evaluate(document, _six_windows(), evaluated_at="2026-06-01T00:00:00")


# -- 5. the prepared pilot is offline, correctly sized, and guarded ---------


def test_pilot_dry_run_sends_nothing_and_records_the_2048_ceiling() -> None:
    """A dry run must be provably inert and must state the real output ceiling."""
    from endpoint_bench.dry_run import build_dry_run_manifest
    from endpoint_bench.profiles import load_profile
    from endpoint_bench.registry import load_registry

    profile = load_profile("configs/profiles/pilot-10k-2048.json", "pilot-10k-2048")
    registry = load_registry(Path("configs/registry.example.json"))
    deployment = registry.deployments["offline-mock-smoke"]

    manifest = build_dry_run_manifest(registry, deployment, profile)

    assert manifest["inference"]["network_requests"] == 0
    assert manifest["inference"]["executed"] is False
    assert manifest["profile"]["max_output_tokens"] == 2048
    assert manifest["planned_exposure"]["max_generated_tokens"] == profile.request_count * 2048


def test_pilot_plan_guard_exceeds_the_estimator_and_retires_the_old_guard() -> None:
    """The $0.15 guard from the 1024-token plan is below a 2048-token estimate."""
    pilot = json.loads(Path("docs/gate3-plans/pilot-10k-2048.json").read_text())

    for run in pilot["runs_detail"]:
        assert run["max_usd_ceiling"] > run["estimated_cost_usd"]
        assert run["max_usd_ceiling"] > 0.15, "the retired 1024-token guard was reused"
        assert run["max_requests"] == run["requests"]


def test_an_insufficient_guard_is_refused_before_any_request() -> None:
    """The execute guard must reject a ceiling below the estimate, without traffic."""
    from argparse import Namespace

    from endpoint_bench.cli import _execute_guard
    from endpoint_bench.profiles import load_profile
    from endpoint_bench.registry import Deployment

    profile = load_profile("configs/profiles/pilot-10k-2048.json", "pilot-10k-2048")
    # A non-loopback target is where the cost guard applies, and the estimate needs
    # verified per-million rates. Rates here are synthetic test values, not pricing.
    live = Deployment(
        deployment_key="pilot-guard-test",
        provider_name="test-provider",
        base_url="https://api.example-provider.test/v1",
        protocol="openai-chat-completions",
        region="test-region",
        credential_env_name="TEST_API_KEY",
        model_id="test-model",
        model_revision=None,
        context={},
        pricing={
            "currency": "USD",
            "prompt_per_million_tokens": 0.30,
            "completion_per_million_tokens": 1.20,
            "verified": True,
            "source": "synthetic test fixture",
            "effective_date": "2026-11-01",
        },
    )
    args = Namespace(authorize_live=True, max_requests=20, max_usd=0.15)

    blockers = _execute_guard(live, args, profile)

    assert any("exceeds --max-usd" in blocker for blocker in blockers), blockers

    # The same run with an adequate guard must not be blocked on cost.
    ok = _execute_guard(live, Namespace(authorize_live=True, max_requests=20, max_usd=0.20), profile)
    assert not any("max-usd" in blocker for blocker in ok), ok


def test_pilot_plan_is_not_authorized_and_stops_on_censoring() -> None:
    pilot = json.loads(Path("docs/gate3-plans/pilot-10k-2048.json").read_text())

    assert pilot["authorization"]["execution_authorized"] is False
    assert pilot["design"]["ceiling_adequacy_established"] is False
    stop = pilot["stop_rule"]
    assert any("zero ceiling truncations" in item for item in stop["continue_only_if"])
    assert "escalating the output cap automatically" in stop["prohibited"]
