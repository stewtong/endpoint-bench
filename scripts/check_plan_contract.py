"""Guard against drift between prepared plans, the contract, and the code.

The multi-window plan and the SLO contract are documents that make numeric claims
about the harness: how many trials a plan contains, how much it should cost, and
which schema versions are required as evidence. Those claims silently rot when the
schema or profiles change. This script fails the build instead.

Run directly or from CI:

    uv run python scripts/check_plan_contract.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from endpoint_bench import RESULT_SCHEMA_VERSION
from endpoint_bench.cost import estimate_cost_usd
from endpoint_bench.profiles import load_profile
from endpoint_bench.registry import load_registry
from endpoint_bench.report import REPORT_SCHEMA_VERSION
from endpoint_bench.trials import TRIAL_SCHEMA_VERSION

PLAN_PATH = Path("docs/gate3-plans/multiwindow-10k.json")
CONTRACT_PATH = Path("docs/qualification/10k-slo-contract.json")
PROFILE_PATH = Path("configs/profiles/qualification-10k.json")
PILOT_PLAN_PATH = Path("docs/gate3-plans/pilot-10k-2048.json")
PILOT_PROFILE_NAME = "pilot-10k-2048"
PILOT_PROFILE_PATH = Path("configs/profiles/pilot-10k-2048.json")
#: Expected pilot shape from decision 0003. Changing it is a plan revision, not a tweak.
PILOT_EXPECTED = {
    "requests": 40,
    "per_run": 20,
    "concurrency_levels": [1, 2],
    "input_tokens": 10240,
    "output_ceiling": 2048,
}


class CheckError(AssertionError):
    """A document no longer matches the code it describes."""


def _load(path: Path) -> dict:
    if not path.is_file():
        raise CheckError(f"missing document: {path}")
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise CheckError(f"{path} is not a JSON object")
    return document


def check_plan(plan: dict, profile: dict) -> list[str]:
    findings: list[str] = []
    runs = plan["runs"]
    design = plan["design"]

    if plan["status"] != "prepared_not_executed":
        raise CheckError(
            f"plan status is `{plan['status']}`; a plan must not claim execution it "
            "has not performed"
        )
    offsets = design["window_start_offsets_minutes"]
    if len(offsets) != design["windows"] or offsets[0] != 0 or any(
        b - a < 10 for a, b in zip(offsets, offsets[1:], strict=False)
    ):
        raise CheckError("window start offsets must provide at least ten-minute spacing")
    if plan["purpose"] != "exploratory_repeat_measurement_not_qualification":
        raise CheckError("this pilot plan must not claim qualification")
    if plan["pilot_gate"]["max_trials"] != 2 * design["requests_per_cell"]:
        raise CheckError("pilot budget must cover exactly the first window")
    if design["requests_per_cell"] != profile["request_count"]:
        raise CheckError(
            f"plan requests_per_cell {design['requests_per_cell']} does not match "
            f"profile request_count {profile['request_count']}"
        )
    if design["output_ceiling_rationale"] == "":
        raise CheckError("plan has no rationale for its output ceiling")

    planned = sum(run["planned_requests"] for run in runs)
    if planned != design["total_trials"]:
        raise CheckError(
            f"plan declares {design['total_trials']} trials but its runs total {planned}"
        )
    if design["total_http_attempts"] != design["total_trials"]:
        raise CheckError(
            "plan expects more HTTP attempts than trials; the harness issues no retries, "
            "so a mismatch means unaccounted traffic is planned"
        )

    cost_total = sum(run["estimated_cost_usd"] for run in runs)
    declared = plan["cost"]["total_estimate_usd"]
    if abs(cost_total - declared) > 1e-6:
        raise CheckError(
            f"plan cost total {declared} does not equal the sum of its cells {cost_total:.6f}"
        )

    levels = [run["concurrency"] for run in runs]
    if levels.count(1) != levels.count(2):
        raise CheckError(f"plan is not balanced across concurrency levels: {levels}")

    per_cell = {(r["window"], r["concurrency"]) for r in runs}
    expected = {(w, c) for w in range(1, design["windows"] + 1) for c in (1, 2)}
    if per_cell != expected:
        raise CheckError("plan does not contain exactly one cell per window per concurrency level")

    for run in runs:
        if run["max_usd_ceiling"] < run["estimated_cost_usd"]:
            raise CheckError(
                f"run {run['run_index']} ceiling {run['max_usd_ceiling']} is below its "
                f"estimate {run['estimated_cost_usd']}; the guard would refuse it"
            )

    findings.append(
        f"plan: {len(runs)} cells, {planned} trials, ${declared:.6f} estimated"
    )
    return findings


def check_contract(contract: dict) -> list[str]:
    findings: list[str] = []
    required = contract["evidence_requirements"]

    if required["report_schema_version"] != REPORT_SCHEMA_VERSION:
        raise CheckError(
            f"contract requires report schema {required['report_schema_version']} but the "
            f"code writes {REPORT_SCHEMA_VERSION}"
        )
    if required["trial_schema_version"] != TRIAL_SCHEMA_VERSION:
        raise CheckError(
            f"contract requires trial schema {required['trial_schema_version']} but the "
            f"code writes {TRIAL_SCHEMA_VERSION}"
        )
    if RESULT_SCHEMA_VERSION in required["rejected_schemas"]:
        raise CheckError(
            f"contract rejects schema {RESULT_SCHEMA_VERSION}, which the code considers current"
        )

    objectives = contract["performance_thresholds"]["objectives"]
    # Any unratified objective blocks a verdict, not just all of them: an objective
    # with no threshold cannot be evaluated, so it cannot be counted as met.
    partial = [objective["id"] for objective in objectives if objective["threshold"] is None]
    reachable = contract["verdict_logic"]["current_reachable_state"]
    if partial and reachable != "inconclusive":
        raise CheckError(
            f"contract leaves {len(partial)} performance objective(s) unratified "
            f"({', '.join(partial[:3])}{'...' if len(partial) > 3 else ''}) but claims a "
            f"reachable verdict of `{reachable}`"
        )

    gates = contract["data_quality_gates"]["gates"]
    by_id = {gate["id"]: gate for gate in gates}
    if "dq.error_rate" in by_id:
        raise CheckError("endpoint error rate belongs in performance objectives")
    if "perf.endpoint_error_rate" not in {obj["id"] for obj in objectives}:
        raise CheckError("endpoint error-rate objective is missing")
    if by_id.get("dq.truncation_rate", {}).get("threshold") != 0:
        raise CheckError("unadjusted completion latency requires uncensored evidence")
    if "dq.accounting_provenance" not in by_id:
        raise CheckError("accounting provenance gate is missing")
    gate_ids = [gate["id"] for gate in gates]
    if len(set(gate_ids)) != len(gate_ids):
        raise CheckError("contract has duplicate data-quality gate ids")
    allowed = {"inconclusive", "mean_only"}
    bad = [
        gate["id"]
        for gate in contract["data_quality_gates"]["gates"]
        if gate["on_failure"] not in allowed
    ]
    if bad:
        raise CheckError(f"gates have unsupported on_failure actions: {bad}")

    findings.append(
        f"contract: {len(gate_ids)} data-quality gates, "
        f"{len(objectives)} performance objectives, verdict `{reachable}`"
    )
    return findings


def check_pilot(pilot: dict, profile: dict) -> list[str]:
    """Validate the exploratory pilot plan against its profile and decision 0003.

    The 1024-token plan and the 2048-token pilot are separate identities. A check
    that only looked at one of them would let either drift silently.
    """
    findings: list[str] = []

    if pilot["status"] != "prepared_not_executed":
        raise CheckError(f"pilot status is `{pilot['status']}`; preparation is not execution")
    if pilot["authorization"]["execution_authorized"] is not False:
        raise CheckError("pilot must not claim execution authorization")

    if profile["name"] != PILOT_PROFILE_NAME:
        raise CheckError(f"pilot profile is `{profile['name']}`, expected `{PILOT_PROFILE_NAME}`")

    for field_name, expected in (
        ("request_count", PILOT_EXPECTED["per_run"]),
        ("input_tokens", PILOT_EXPECTED["input_tokens"]),
        ("max_output_tokens", PILOT_EXPECTED["output_ceiling"]),
    ):
        if profile[field_name] != expected:
            raise CheckError(
                f"pilot profile `{field_name}` is {profile[field_name]}, expected {expected}"
            )

    design = pilot["design"]
    if design["output_ceiling_per_request"] != profile["max_output_tokens"]:
        raise CheckError("pilot design ceiling disagrees with the profile")
    if design["ceiling_adequacy_established"] is not False:
        raise CheckError(
            "pilot must not claim that ceiling adequacy is established; three natural "
            "completions cannot identify the length tail"
        )

    runs = pilot["runs_detail"]
    if [run["concurrency"] for run in runs] != PILOT_EXPECTED["concurrency_levels"]:
        raise CheckError(f"pilot concurrency levels are {[r['concurrency'] for r in runs]}")
    for run in runs:
        if run["requests"] != PILOT_EXPECTED["per_run"]:
            raise CheckError(f"run {run['run_index']} requests is {run['requests']}")
        if run["profile"] != PILOT_PROFILE_NAME:
            raise CheckError(f"run {run['run_index']} names profile `{run['profile']}`")
        if run["requested_output_token_ceiling_per_request"] != PILOT_EXPECTED["output_ceiling"]:
            raise CheckError(f"run {run['run_index']} ceiling disagrees with the profile")
        if run["max_requests"] != run["requests"]:
            raise CheckError(f"run {run['run_index']} max_requests exceeds its request count")
        if run["max_usd_ceiling"] <= run["estimated_cost_usd"]:
            raise CheckError(
                f"run {run['run_index']} guard {run['max_usd_ceiling']} is not above its "
                f"estimate {run['estimated_cost_usd']}; the execute guard would refuse it"
            )

    total = sum(run["requests"] for run in runs)
    if total != PILOT_EXPECTED["requests"]:
        raise CheckError(f"pilot totals {total} requests, expected {PILOT_EXPECTED['requests']}")
    if design["total_requests"] != total:
        raise CheckError(
            f"pilot declares {design['total_requests']} requests but its runs total {total}"
        )

    declared = pilot["cost"]["total_estimate_usd"]
    summed = round(sum(run["estimated_cost_usd"] for run in runs), 6)
    if abs(summed - declared) > 1e-6:
        raise CheckError(f"pilot cost {declared} does not equal its run sum {summed}")

    nominal_out = sum(run["nominal_output_tokens"] for run in runs)
    expected_out = total * PILOT_EXPECTED["output_ceiling"]
    if nominal_out != expected_out:
        raise CheckError(
            f"pilot nominal output tokens {nominal_out} does not equal "
            f"{total} requests x {PILOT_EXPECTED['output_ceiling']}"
        )

    findings.append(
        f"pilot: {total} requests, {PILOT_EXPECTED['output_ceiling']}-token ceiling, "
        f"${declared:.6f} estimated, guard ${pilot['cost']['max_usd_ceiling_per_run']:.2f}/run"
    )
    return findings


def check_pilot_exposure_against_estimator() -> list[str]:
    """Recompute the pilot's exposure with the owning estimator.

    A hand-maintained dollar figure rots the moment pricing or the profile changes.
    This regenerates it and compares.
    """
    registry_path = Path.home() / ".endpoint-bench" / "registry.live.json"
    if not registry_path.is_file():
        return ["pilot exposure: skipped, no live registry on this machine"]
    try:
        registry = load_registry(registry_path)
        deployment = registry.deployments["tf-deepseek-v4.1-flash-1017"]
    except Exception as error:  # noqa: BLE001 - a missing deployment is a skip, not a failure
        return [f"pilot exposure: skipped ({error})"]

    profile = load_profile(PILOT_PROFILE_PATH, PILOT_PROFILE_NAME)
    estimate = estimate_cost_usd(deployment, profile)
    pilot = _load(PILOT_PLAN_PATH)
    declared = pilot["cost"]["per_run_estimate_usd"]
    if estimate is not None and abs(estimate - declared) > 1e-6:
        raise CheckError(
            f"pilot per-run exposure {declared} disagrees with the estimator's {estimate:.6f}"
        )
    # The retired 1024-token guard must not be reused for a 2048-token workload.
    retired_guard = 0.15
    if pilot["cost"]["max_usd_ceiling_per_run"] <= retired_guard:
        raise CheckError(
            f"pilot reuses the retired ${retired_guard} guard, which is below the 2048 estimate"
        )
    return [f"pilot exposure: estimator agrees at ${declared:.6f}/run, guard exceeds it"]


def check_contract_scope_consistency(
    contract: dict, pilot: dict, plan: dict, windows_profile: dict
) -> list[str]:
    """The two prepared experiments must stay distinct and correctly labelled."""
    findings: list[str] = []
    if contract["direction"]["current"] != "characterization":
        raise CheckError(
            f"contract direction is `{contract['direction']['current']}`; the reviewed "
            "direction is characterization until an operator ratifies thresholds"
        )
    if contract["performance_thresholds"]["status"] != "unratified":
        raise CheckError("contract performance thresholds must stay unratified")
    if not all(
        objective["threshold"] is None
        for objective in contract["performance_thresholds"]["objectives"]
    ):
        raise CheckError("a performance threshold is populated; that is a ratification event")

    model = contract["evaluation_model"]
    if "pooled" not in model["percentile_min_samples_applies_to"]:
        raise CheckError("the percentile floor must apply to the pooled sample, not a window")
    if "averaging per-window percentiles" not in " ".join(model["prohibited"]):
        raise CheckError("the contract must forbid averaging per-window percentiles")

    # An evaluator claim must match the tree. The only permitted scope is synthetic
    # evidence: a verdict from run reports would be a live qualification pipeline.
    verdict_logic = contract["verdict_logic"]
    evaluator_module = Path("src/endpoint_bench/qualification.py")
    if verdict_logic["evaluator_implemented"] != evaluator_module.is_file():
        raise CheckError(
            "verdict_logic.evaluator_implemented does not match the presence of "
            "src/endpoint_bench/qualification.py"
        )
    if (
        verdict_logic["evaluator_implemented"]
        and verdict_logic["evaluator_scope"] != "synthetic_evidence_only"
    ):
        raise CheckError(
            "a qualification evaluator may only be claimed as synthetic-evidence-only; "
            f"scope is `{verdict_logic['evaluator_scope']}`"
        )

    # A window smaller than the floor is exactly the case that makes pooling mandatory.
    floor = next(
        gate["threshold"]
        for gate in contract["data_quality_gates"]["gates"]
        if gate["id"] == "dq.percentile_samples"
    )
    if contract["scope"]["requests_per_window"] < floor:
        if "pooled eligible per-trial observations" not in model["performance_evaluation_sample"]:
            raise CheckError(
                f"windows hold {contract['scope']['requests_per_window']} requests, below the "
                f"{floor}-sample floor, so evaluation must pool per-trial observations"
            )
    else:
        raise CheckError(
            "windows now meet the percentile floor on their own; the pooling rationale and "
            "this check need revisiting rather than silent drift"
        )

    # Separate workload identities: a different ceiling must not be described as the same run.
    if pilot["identity"]["distinct_from"] != plan["experiment"]:
        raise CheckError("pilot does not name the plan it is distinct from")
    if pilot["design"]["output_ceiling_per_request"] == windows_profile["max_output_tokens"]:
        raise CheckError(
            "pilot ceiling equals the multi-window profile's ceiling, so the two plans would "
            "describe one workload under two identities"
        )

    findings.append(
        "contract: characterization direction, pooled evaluation, no thresholds set; "
        "pilot and multi-window plans remain distinct workloads"
    )
    return findings


def main() -> int:
    try:
        plan = _load(PLAN_PATH)
        contract = _load(CONTRACT_PATH)
        profile = _load(PROFILE_PATH)
        pilot = _load(PILOT_PLAN_PATH)
        pilot_profile = _load(PILOT_PROFILE_PATH)
        findings = (
            check_plan(plan, profile)
            + check_contract(contract)
            + check_pilot(pilot, pilot_profile)
            + check_pilot_exposure_against_estimator()
            + check_contract_scope_consistency(contract, pilot, plan, profile)
        )
    except (CheckError, KeyError, TypeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    for line in findings:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
