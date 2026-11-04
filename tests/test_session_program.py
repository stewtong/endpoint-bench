"""Session-dynamics-v1 program, plan and manifest contract tests."""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest

from endpoint_bench.session_program import (
    CONTEXT_SIZES,
    PROGRAM_ID,
    SESSION_MANIFEST_SCHEMA,
    SessionProgramError,
    build_manifest,
    build_plan,
    build_templates,
    context_fixture,
    load_program,
    manifest_content_sha256,
    manifest_execution_identity_sha256,
    plan_content_sha256,
    validate_manifest,
    validate_manifest_document,
    validate_plan_document,
    verify_context_fixtures,
    verify_manifest_against_plan,
    verify_program_identity,
    verify_runtime_sources,
)

PROGRAM = Path(__file__).resolve().parents[1] / "configs/session-programs/session-dynamics-v1.json"


@pytest.fixture(scope="module")
def program():
    return load_program(PROGRAM)


@pytest.fixture(scope="module")
def plan(program, tmp_path_factory):
    return build_plan(
        program,
        output_dir=tmp_path_factory.mktemp("plan"),
        release_nonce="test-nonce-0001",
    )


def _card(cell: dict) -> dict:
    return dict(next(c for c in cell["operations"] if c["operation_id"] == "SD-stable_prefix-C1"))


def test_program_is_the_exact_finite_design(program) -> None:
    assert program.program_id == PROGRAM_ID
    assert program.directive_id == "D0003"
    assert len(program.operations) == 54
    assert program.total_max_attempts == 1038
    assert program.live_released_operation_ids == ()
    assert program.limits["max_concurrent_sessions"] == 4
    assert program.limits["request_timeout_s"] == 240
    assert program.limits["cell_process_deadline_s"] == 2400
    phases = {}
    for operation in program.operations:
        phases[operation.phase] = phases.get(operation.phase, 0) + 1
    assert phases == {
        "session_screening": 27,
        "temporal_repeats": 24,
        "context_sentinels": 3,
    }


def test_program_rejects_attempt_drift(program, tmp_path: Path) -> None:
    document = json.loads(PROGRAM.read_text(encoding="utf-8"))
    document["operations"][0]["max_inference_attempts"] += 1
    path = tmp_path / "drifted.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(SessionProgramError, match="attempts|max_inference"):
        load_program(path)


def test_program_rejects_live_release_in_preflight(program, tmp_path: Path) -> None:
    document = json.loads(PROGRAM.read_text(encoding="utf-8"))
    document["live_released_operation_ids"] = ["SD-independent-C1"]
    path = tmp_path / "released.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(SessionProgramError, match="implementation/preflight"):
        load_program(path)


def test_templates_are_deterministic_and_matched() -> None:
    first = build_templates()
    second = build_templates()

    assert first.keys() == {"coding", "document", "transcript"}
    lengths = set()
    for name in first:
        assert first[name].to_descriptor() == second[name].to_descriptor()
        lengths.add(len(first[name].prefix))
        assert {len(task) for task in first[name].tasks} == {8192}
    assert lengths == {32768}
    for template in first.values():
        assert len(template.sentinel_facts) == 4
        for fact in template.sentinel_facts:
            assert fact in template.prefix


def test_plan_freezes_54_cells_and_1038_attempts(plan) -> None:
    assert plan["totals"]["cells"] == 54
    assert plan["totals"]["inference_attempts"] == 1038
    assert plan["totals"]["sessions"] == 255
    assert len(plan["operations"]) == 54
    namespaces = {cell["namespace"] for cell in plan["operations"]}
    assert len(namespaces) == 54
    for cell in plan["operations"]:
        assert cell["max_inference_attempts"] in (6, 20)
        assert cell["session_order"] or cell["scenario"] == "context_sentinels"
        assert cell["turn_order"] or cell["scenario"] == "context_sentinels"


def test_plan_schedule_uses_anchors_and_keeps_windows_disjoint(plan) -> None:
    schedule = plan["schedule"]
    windows = {
        item["window"]: item for item in schedule if item.get("window") is not None
    }
    assert set(windows) == {1, 2, 3, 4}
    assert windows[1]["not_before_offset_s"] == 0
    assert windows[2]["not_before_offset_s"] == 6 * 3600
    assert windows[3]["not_before_offset_s"] == 24 * 3600
    assert windows[4]["not_before_offset_s"] == 30 * 3600
    assert windows[1]["anchor"] == "temporal_anchor"
    context = [item for item in schedule if item["phase"] == "context_sentinels"]
    assert context and context[0]["not_before_offset_s"] == 30 * 3600 + 6 * 2400


def test_stable_prefix_shares_one_group_per_session(plan) -> None:
    cell = _card({"operations": plan["operations"]})
    for session in cell["sessions"]:
        assert len(set(session["turn_group_ids"])) == 1
        assert session["prefix_group_id"] is not None


def test_prefix_invalidation_changes_group_at_turn_three(plan) -> None:
    cell = next(
        c
        for c in plan["operations"]
        if c["operation_id"] == "SD-prefix_invalidation-C1"
    )
    for session in cell["sessions"]:
        groups = session["turn_group_ids"]
        assert groups[0] == groups[1]
        assert groups[2] != groups[0]
        assert groups[3] != groups[2]


def test_independent_uses_a_unique_group_per_call(plan) -> None:
    cell = next(
        c for c in plan["operations"] if c["operation_id"] == "SD-independent-C1"
    )
    groups = [group for session in cell["sessions"] for group in session["turn_group_ids"]]
    assert len(set(groups)) == 20


def test_context_fixtures_have_exact_size_and_one_sentinel() -> None:
    for size in CONTEXT_SIZES:
        for position in ("early", "middle", "late"):
            fixture = context_fixture(
                requested_input_tokens=size, position=position, replicate=1, seed=7
            )
            assert fixture["client_estimated_input_tokens"] == size
            assert fixture["prompt"].count(fixture["sentinel_code"]) == 1
            index = fixture["prompt"].index(fixture["sentinel_code"])
            third = len(fixture["prompt"]) / 3
            if position == "early":
                assert index < third
            elif position == "middle":
                assert third <= index < 2 * third
            else:
                assert index >= 2 * third


def test_manifest_contains_exact_argv_hashes_and_timeouts(plan, tmp_path: Path) -> None:
    manifest = build_manifest(
        plan,
        manifest_path=tmp_path / "manifest.json",
        plan_path=tmp_path / "plan.json",
    )
    validate_manifest(manifest)
    assert manifest["schema_version"] == SESSION_MANIFEST_SCHEMA
    assert manifest["totals"]["max_inference_attempts"] == 1038
    assert manifest["timeouts"]["request_timeout_s"] == 240
    assert manifest["timeouts"]["cell_process_deadline_s"] == 2400
    assert len(manifest["operations"]) == 54
    for operation in manifest["operations"]:
        assert operation["argv"][:5] == [
            "endpoint-bench",
            "session",
            "execute",
            "--manifest",
            str(tmp_path / "manifest.json"),
        ]
        assert operation["timeouts"]["request_timeout_s"] == 240
        assert operation["timeouts"]["cell_process_deadline_s"] == 2400
        assert operation["client"]["session_affinity"] is False
        assert operation["cache_treatment"]["first_turn_measured"] is True
        assert operation["cache_treatment"]["hidden_warmup"] is False
    assert manifest["totals"]["operational_usd_envelope"] > 0


def test_manifest_hash_rejects_an_edit(plan, tmp_path: Path) -> None:
    manifest = build_manifest(
        plan, manifest_path=tmp_path / "manifest.json", plan_path=tmp_path / "plan.json"
    )
    manifest["operations"][0]["max_inference_attempts"] = 21
    manifest["limits"]["max_total_new_inference_attempts"] = 1039

    with pytest.raises(SessionProgramError, match="stale|edited|attempts"):
        validate_manifest(manifest)


def test_release_has_no_live_operations(plan, tmp_path: Path) -> None:
    manifest = build_manifest(
        plan, manifest_path=tmp_path / "manifest.json", plan_path=tmp_path / "plan.json"
    )
    assert manifest["release"]["live_released_operation_ids"] == []
    manifest["release"]["live_released_operation_ids"] = ["SD-independent-C1"]
    with pytest.raises(SessionProgramError, match="stale|must not release"):
        validate_manifest(manifest)


# -- F1/F4: frozen identity, validation chain and release digest scheme -----


def _reseal_plan(plan: dict) -> None:
    plan["plan_sha256"] = plan_content_sha256(plan)


def _reseal_manifest(manifest: dict) -> None:
    manifest["execution_identity_sha256"] = manifest_execution_identity_sha256(
        manifest
    )
    manifest["manifest_sha256"] = manifest_content_sha256(manifest)


def test_plan_freezes_namespace_labels_for_every_turn(plan) -> None:
    for cell in plan["operations"]:
        if cell["scenario"] == "context_sentinels":
            assert cell["context_fixtures"]
            continue
        for session in cell["sessions"]:
            assert len(session["turn_namespace_labels"]) == 4
            assert all(session["turn_namespace_labels"])
    stable = next(
        cell for cell in plan["operations"] if cell["operation_id"] == "SD-stable_prefix-C1"
    )
    for session in stable["sessions"]:
        assert len(set(session["turn_namespace_labels"])) == 1
    independent = next(
        cell for cell in plan["operations"] if cell["operation_id"] == "SD-independent-C1"
    )
    labels = [
        label
        for session in independent["sessions"]
        for label in session["turn_namespace_labels"]
    ]
    assert len(set(labels)) == 20
    invalidated = next(
        cell
        for cell in plan["operations"]
        if cell["operation_id"] == "SD-prefix_invalidation-C1"
    )
    for session in invalidated["sessions"]:
        labels = session["turn_namespace_labels"]
        assert labels[0] == labels[1]
        assert labels[2] == labels[3]
        assert labels[0] != labels[2]
    assert plan["workloads"]["cache_isolation"]["headers_are_not_proof"] is True


def test_plan_validation_rejects_content_without_a_matching_digest(plan) -> None:
    tampered = copy.deepcopy(plan)
    tampered["operations"][0]["order_seed"] = 999

    with pytest.raises(SessionProgramError, match="plan content"):
        validate_plan_document(tampered)


def test_plan_validation_rejects_duplicate_operation_ids(plan) -> None:
    tampered = copy.deepcopy(plan)
    tampered["operations"][1] = copy.deepcopy(tampered["operations"][0])
    _reseal_plan(tampered)

    with pytest.raises(SessionProgramError, match="duplicate operation_id"):
        validate_plan_document(tampered)


def test_manifest_validation_rejects_duplicate_ids_after_reseal(plan, tmp_path: Path) -> None:
    manifest = build_manifest(
        plan, manifest_path=tmp_path / "manifest.json", plan_path=tmp_path / "plan.json"
    )
    manifest["operations"][1]["operation_id"] = manifest["operations"][0]["operation_id"]
    _reseal_manifest(manifest)

    with pytest.raises(SessionProgramError, match="duplicate operation_id"):
        validate_manifest_document(manifest)


def test_manifest_validation_rejects_timeout_drift_after_reseal(plan, tmp_path: Path) -> None:
    manifest = build_manifest(
        plan, manifest_path=tmp_path / "manifest.json", plan_path=tmp_path / "plan.json"
    )
    manifest["operations"][0]["timeouts"]["request_timeout_s"] = 1
    _reseal_manifest(manifest)

    with pytest.raises(SessionProgramError, match="request timeout"):
        validate_manifest_document(manifest)


def test_manifest_validation_rejects_concurrency_drift_after_reseal(
    plan, tmp_path: Path
) -> None:
    manifest = build_manifest(
        plan, manifest_path=tmp_path / "manifest.json", plan_path=tmp_path / "plan.json"
    )
    manifest["operations"][0]["concurrent_sessions"] = 8
    _reseal_manifest(manifest)

    with pytest.raises(SessionProgramError, match="concurrency"):
        validate_manifest_document(manifest)


def test_manifest_validation_rejects_envelope_drift_after_reseal(
    plan, tmp_path: Path
) -> None:
    manifest = build_manifest(
        plan, manifest_path=tmp_path / "manifest.json", plan_path=tmp_path / "plan.json"
    )
    manifest["totals"]["operational_usd_envelope"] = 0.01
    _reseal_manifest(manifest)

    with pytest.raises(SessionProgramError, match="operational_usd_envelope"):
        verify_manifest_against_plan(manifest, plan)


def test_release_fields_change_only_the_manifest_seal(plan, tmp_path: Path) -> None:
    manifest = build_manifest(
        plan, manifest_path=tmp_path / "manifest.json", plan_path=tmp_path / "plan.json"
    )
    identity = manifest["execution_identity_sha256"]
    manifest["release"]["release_anchor_utc"] = "2026-11-03T00:00:00Z"
    manifest["release"]["live_released_operation_ids"] = [
        manifest["operations"][0]["operation_id"]
    ]
    manifest["manifest_sha256"] = manifest_content_sha256(manifest)

    validate_manifest_document(manifest)
    assert manifest["execution_identity_sha256"] == identity
    with pytest.raises(SessionProgramError, match="must not release"):
        validate_manifest_document(manifest, require_unreleased=True)


def test_manifest_must_match_the_plan_content(plan, program, tmp_path: Path) -> None:
    manifest = build_manifest(
        plan, manifest_path=tmp_path / "manifest.json", plan_path=tmp_path / "plan.json"
    )
    other = build_plan(program, output_dir=tmp_path, release_nonce="different-nonce")

    with pytest.raises(SessionProgramError, match="plan hash|does not match"):
        verify_manifest_against_plan(manifest, other)


def test_context_fixture_drift_is_refused(plan) -> None:
    tampered = copy.deepcopy(plan)
    cell = next(
        cell for cell in tampered["operations"] if cell["scenario"] == "context_sentinels"
    )
    cell["context_fixtures"][0]["prompt_sha256"] = "0" * 64
    _reseal_plan(tampered)

    with pytest.raises(SessionProgramError, match="does not match"):
        verify_context_fixtures(tampered)


def test_runtime_source_drift_is_refused(plan, tmp_path: Path) -> None:
    manifest = build_manifest(
        plan, manifest_path=tmp_path / "manifest.json", plan_path=tmp_path / "plan.json"
    )
    root = tmp_path / "repo"
    shutil.copytree(Path("src"), root / "src")
    shutil.copytree(Path("scripts"), root / "scripts")
    (root / "src/endpoint_bench/session_engine.py").write_text(
        (root / "src/endpoint_bench/session_engine.py").read_text() + "\n# drift\n",
        encoding="utf-8",
    )

    with pytest.raises(SessionProgramError, match="frozen source drift"):
        verify_runtime_sources(manifest, root)


def test_program_config_drift_is_refused(plan, tmp_path: Path) -> None:
    manifest = build_manifest(
        plan, manifest_path=tmp_path / "manifest.json", plan_path=tmp_path / "plan.json"
    )
    root = tmp_path / "repo"
    program_path = Path(str(plan["program_path"]))
    destination = root / program_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    document = json.loads(PROGRAM.read_text(encoding="utf-8"))
    document["state"] = "tampered_state"
    destination.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(SessionProgramError, match="program file hash"):
        verify_program_identity(manifest, plan, root)
