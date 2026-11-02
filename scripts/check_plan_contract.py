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
from endpoint_bench.report import REPORT_SCHEMA_VERSION
from endpoint_bench.trials import TRIAL_SCHEMA_VERSION

PLAN_PATH = Path("docs/gate3-plans/multiwindow-10k.json")
CONTRACT_PATH = Path("docs/qualification/10k-slo-contract.json")
PROFILE_PATH = Path("configs/profiles/qualification-10k.json")


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


def main() -> int:
    try:
        plan = _load(PLAN_PATH)
        contract = _load(CONTRACT_PATH)
        profile = _load(PROFILE_PATH)
        findings = check_plan(plan, profile) + check_contract(contract)
    except (CheckError, KeyError, TypeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    for line in findings:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
