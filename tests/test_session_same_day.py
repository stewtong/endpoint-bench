"""The D0010 same-day default: finite, calendar-free, resumable and isolated.

These tests pin the repository contract, not a live endpoint result:

- the default matrix is 30 cells / 558 maximum calls with no temporal cells;
- the plan carries no anchor, not-before window, scheduled wait or monitor;
- the foreground invocation has a finite deadline and records skipped cells;
- a loopback invocation runs, analyzes and writes the report end to end;
- cancellation preserves partial evidence and resume never replays a cell;
- the legacy D0009 program, plan, journal and cells stay byte-identical.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

import endpoint_bench.cli as cli_module
from endpoint_bench.cli import main
from endpoint_bench.mock_provider import MockBehavior, MockProvider
from endpoint_bench.session_engine import SessionCellRunner
from endpoint_bench.session_program import (
    manifest_content_sha256,
    manifest_execution_identity_sha256,
)

ROOT = Path(__file__).resolve().parents[1]
SAME_DAY_PROGRAM = ROOT / "configs/session-programs/session-dynamics-sameday-v1.json"
V1_PROGRAM = ROOT / "configs/session-programs/session-dynamics-v1.json"
LEGACY_RUN = ROOT / "docs/session-dynamics-v1"
LEGACY_PROGRAM_SHA256 = (
    "3964d3d360bf5b325c540d1bedc4404a493d5cd15d846835174218167c10050e"
)


def _registry(path: Path, base_url: str) -> Path:
    document = {
        "schema_version": "1.0",
        "providers": [
            {
                "name": "mock",
                "base_url": base_url,
                "protocol": "openai-chat-completions",
                "region": "local",
                "credential_env_name": "MOCK_KEY",
                "deployments": [
                    {
                        "deployment_key": "mock-target",
                        "model_id": "mock-model",
                        "context": {
                            "advertised_input_tokens": 131072,
                            "effective_input_tokens": None,
                            "context_state": "unverified",
                        },
                        "pricing": {
                            "verified": True,
                            "prompt_per_million_tokens": 0.3,
                            "completion_per_million_tokens": 1.2,
                            "source": "test",
                            "effective_date": "2026-01-01",
                        },
                    }
                ],
            }
        ],
    }
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _same_day(
    out: Path, *extra: str
) -> int:
    return main(["session", "same-day", "--out", str(out), *extra])


def _loopback(out: Path, registry: Path, *extra: str) -> int:
    return main(
        [
            "session",
            "same-day",
            "--config",
            str(registry),
            "--target",
            "mock-target",
            "--out",
            str(out),
            *extra,
        ]
    )


def _hash_tree(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }


def test_same_day_preflight_is_zero_inference_and_calendar_free(
    tmp_path: Path,
) -> None:
    out = tmp_path / "run"
    assert _same_day(out) == 0

    plan = json.loads((out / "plan.json").read_text(encoding="utf-8"))
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    preflight = (out / "PREFLIGHT.md").read_text(encoding="utf-8")

    assert plan["schema_version"] == "session-plan/2.0.0"
    assert plan["program_id"] == "session-dynamics-sameday-v1"
    assert plan["directive_id"] == "D0010"
    assert plan["workload_type"] == "live_turn_session_dynamics_same_day"
    assert plan["totals"]["cells"] == 30
    assert plan["totals"]["inference_attempts"] == 558
    assert plan["totals"]["phases"] == {
        "session_screening": 27,
        "context_sentinels": 3,
    }
    assert plan["limits"]["cell_process_deadline_s"] == 600
    assert plan["limits"]["max_foreground_invocation_seconds"] == 21600
    assert plan["run_mode"] == {
        "kind": "foreground_same_day",
        "description": "one foreground invocation; no calendar dependency",
        "calendar_anchors": False,
        "not_before_windows": False,
        "scheduled_waits": False,
        "background_monitors": False,
        "automatic_repeat_blocks": False,
        "invocation_deadline_s": 21600,
        "cell_deadline_s": 600,
        "analysis": "written by the same invocation after execution stops",
    }
    cells = [item for item in plan["schedule"] if item["operation_id"]]
    assert len(cells) == 30
    for item in cells:
        assert item["window"] is None
        assert item["anchor"] == "program_start"
        assert item["not_before_offset_s"] == 0.0
        assert item["cell_process_deadline_s"] == 600.0
    guard = plan["schedule"][-1]
    assert guard["max_invocation_elapsed_s"] == 21600
    assert guard["cell_deadline_total_s"] <= guard["max_invocation_elapsed_s"]

    assert manifest["schema_version"] == "session-manifest/2.0.0"
    assert manifest["manifest_type"] == "session_dynamics_same_day_preflight"
    assert manifest["workload_type"] == "live_turn_session_dynamics_same_day"
    assert manifest["totals"]["cells"] == 30
    assert manifest["totals"]["max_inference_attempts"] == 558
    assert manifest["release"]["release_anchor_utc"] is None
    assert manifest["release"]["live_released_operation_ids"] == []
    assert manifest["timeouts"]["invocation_deadline_s"] == 21600
    assert manifest["run_mode"]["scheduled_waits"] is False

    assert not (out / "program-state.json").exists()
    assert "No calendar anchor" in preflight
    assert "30 (27 screening + 3 context)" in preflight


def test_same_day_rejects_the_temporal_program_and_wait_options(
    tmp_path: Path,
) -> None:
    out = tmp_path / "run"
    assert (
        main(
            [
                "session",
                "same-day",
                "--program",
                str(V1_PROGRAM),
                "--out",
                str(out),
            ]
        )
        == 2
    )
    assert not (out / "plan.json").exists()

    for forbidden in (
        ["--wait-until", "2026-11-04T11:00:00Z"],
        ["--anchor", "2026-11-04T05:00:00Z"],
        ["--repeat", "2"],
    ):
        with pytest.raises(SystemExit) as raised:
            _same_day(out, *forbidden)
        assert raised.value.code == 2


def test_same_day_deadline_overrides_are_lower_bounds_only(
    tmp_path: Path,
) -> None:
    out = tmp_path / "run"
    for extra in (
        ["--cell-deadline", "601"],
        ["--cell-deadline", "0"],
        ["--deadline-hours", "7"],
        ["--deadline-hours", "0"],
    ):
        assert _same_day(out, *extra) == 2
    assert not (out / "plan.json").exists()

    assert _same_day(out, "--cell-deadline", "599", "--deadline-hours", "5.5") == 0
    plan = json.loads((out / "plan.json").read_text(encoding="utf-8"))
    assert plan["limits"]["cell_process_deadline_s"] == 600


def test_same_day_live_execution_requires_explicit_authorization(
    tmp_path: Path,
) -> None:
    out = tmp_path / "run"
    # The default target is non-loopback. --execute alone must fail closed before
    # any released manifest or journal exists.
    assert _same_day(out, "--execute") == 2
    assert (out / "manifest.json").is_file()
    assert not (out / "manifest.released.json").exists()
    assert not (out / "program-state.json").exists()
    assert not (out / "cells").exists()


def test_same_day_full_loopback_run_writes_analysis_and_report(
    tmp_path: Path,
) -> None:
    provider = MockProvider(behavior=MockBehavior.STREAM_SUCCESS)
    url = provider.start()
    try:
        registry = _registry(tmp_path / "registry.json", url)
        out = tmp_path / "run"
        assert (
            _loopback(
                out,
                registry,
                "--execute",
                "--idle-seconds",
                "0",
            )
            == 0
        )
        summary = json.loads((out / "run-summary.json").read_text(encoding="utf-8"))
        analysis = json.loads((out / "analysis.json").read_text(encoding="utf-8"))
        report = (out / "REPORT.md").read_text(encoding="utf-8")

        assert summary["status"] == "COMPLETED"
        assert summary["selection"] == "all"
        assert summary["totals"]["selected"] == 30
        assert summary["totals"]["executed_this_invocation"] == 30
        assert summary["totals"]["skipped_deadline"] == 0
        # The three tool cells naturally stop after 2 of 4 calls per session.
        assert summary["totals"]["attempted_calls"] == 528
        assert len(provider.inference_requests) == 528
        assert analysis["cell_count"] == 30
        assert analysis["missing_operations"] == []
        assert analysis["program_id"] == "session-dynamics-sameday-v1"
        assert analysis["workload_type"] == "live_turn_session_dynamics_same_day"
        assert sorted(analysis["context_sentinels"]["by_requested_input_tokens"]) == [
            "131072",
            "32768",
            "65536",
        ]
        assert "| SDS-" in report
        assert "Status: COMPLETED" in report
        assert (out / "analysis.md").is_file()

        released = json.loads(
            (out / "manifest.released.json").read_text(encoding="utf-8")
        )
        assert released["release"]["release_anchor_utc"] is None
        assert len(released["release"]["live_released_operation_ids"]) == 30
        assert released["execution_identity_sha256"] == summary[
            "execution_identity_sha256"
        ]

        # A second invocation is a pure resume: every completed cell is preserved.
        before = len(provider.inference_requests)
        assert (
            _loopback(
                out,
                registry,
                "--execute",
                "--idle-seconds",
                "0",
            )
            == 0
        )
        resumed = json.loads((out / "run-summary.json").read_text(encoding="utf-8"))
        assert resumed["totals"]["already_completed"] == 30
        assert resumed["totals"]["attempted_calls"] == 0
        assert len(provider.inference_requests) == before
    finally:
        provider.stop()


def test_same_day_context_only_execution_needs_no_calendar_anchor(
    tmp_path: Path,
) -> None:
    provider = MockProvider(behavior=MockBehavior.STREAM_SUCCESS)
    url = provider.start()
    try:
        registry = _registry(tmp_path / "registry.json", url)
        out = tmp_path / "run"
        assert (
            _loopback(
                out,
                registry,
                "--select",
                "context",
                "--execute",
                "--idle-seconds",
                "0",
            )
            == 0
        )
        summary = json.loads((out / "run-summary.json").read_text(encoding="utf-8"))
        assert summary["status"] == "COMPLETED"
        assert summary["selected_operation_ids"] == [
            "SDS-CTX-32768-C1",
            "SDS-CTX-65536-C1",
            "SDS-CTX-131072-C1",
        ]
        assert summary["totals"]["attempted_calls"] == 18
        assert len(provider.inference_requests) == 18
        released = json.loads(
            (out / "manifest.released.json").read_text(encoding="utf-8")
        )
        assert released["release"]["release_anchor_utc"] is None
        assert released["release"]["live_released_operation_ids"] == [
            "SDS-CTX-32768-C1",
            "SDS-CTX-65536-C1",
            "SDS-CTX-131072-C1",
        ]
        # The released digest is deterministic from the frozen preflight and the
        # selection, which is what lets a review packet name the exact bytes.
        preflight = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        predicted = deepcopy(preflight)
        predicted["release"]["live_released_operation_ids"] = [
            "SDS-CTX-32768-C1",
            "SDS-CTX-65536-C1",
            "SDS-CTX-131072-C1",
        ]
        predicted["execution_identity_sha256"] = (
            manifest_execution_identity_sha256(predicted)
        )
        predicted["manifest_sha256"] = manifest_content_sha256(predicted)
        assert released["execution_identity_sha256"] == predicted[
            "execution_identity_sha256"
        ]
        assert released["manifest_sha256"] == predicted["manifest_sha256"]
        assert summary["stop"] is None
    finally:
        provider.stop()


def test_same_day_invocation_deadline_skips_unstarted_cells(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = MockProvider(behavior=MockBehavior.STREAM_SUCCESS)
    url = provider.start()
    try:
        registry = _registry(tmp_path / "registry.json", url)
        out = tmp_path / "run"
        calls = {"count": 0}

        def fake_clock() -> float:
            calls["count"] += 1
            # Start and the first cell's check are in budget; after that the
            # invocation deadline has passed.
            return 0.0 if calls["count"] <= 2 else 7 * 3600.0

        monkeypatch.setattr(cli_module, "_same_day_clock", fake_clock)
        assert (
            _loopback(
                out,
                registry,
                "--select",
                "screening",
                "--execute",
                "--idle-seconds",
                "0",
            )
            == 1
        )
        summary = json.loads((out / "run-summary.json").read_text(encoding="utf-8"))
        assert summary["status"] == "PARTIAL"
        assert summary["stop"]["reason"] == "invocation_deadline"
        assert summary["totals"]["executed_this_invocation"] == 1
        assert summary["totals"]["skipped_deadline"] == 26
        assert summary["totals"]["attempted_calls"] == 20
        assert len(provider.inference_requests) == 20
        assert (out / "REPORT.md").is_file()
        assert (out / "analysis.json").is_file()
        # Unstarted cells stay resumable, not marked as failures.
        journal = json.loads((out / "program-state.json").read_text(encoding="utf-8"))
        assert len(journal["operations"]) == 1
        assert journal["operations"][
            summary["cells"][0]["operation_id"]
        ]["status"] == "completed"
    finally:
        provider.stop()


def test_same_day_cancellation_and_resume_never_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = MockProvider(behavior=MockBehavior.STREAM_SUCCESS)
    url = provider.start()
    try:
        registry = _registry(tmp_path / "registry.json", url)
        out = tmp_path / "run"
        real_runner = SessionCellRunner

        class CancelOnSecondCell:
            instances = 0

            def __init__(self, **kwargs: object) -> None:
                self.inner = real_runner(**kwargs)  # type: ignore[arg-type]
                CancelOnSecondCell.instances += 1

            def run(self):  # type: ignore[no-untyped-def]
                if CancelOnSecondCell.instances == 2:
                    raise KeyboardInterrupt
                return self.inner.run()

            def snapshot(self):  # type: ignore[no-untyped-def]
                return self.inner.snapshot()

        monkeypatch.setattr(cli_module, "SessionCellRunner", CancelOnSecondCell)
        assert (
            _loopback(
                out,
                registry,
                "--select",
                "screening",
                "--execute",
                "--idle-seconds",
                "0",
            )
            == 130
        )
        cancelled = json.loads(
            (out / "run-summary.json").read_text(encoding="utf-8")
        )
        assert cancelled["status"] == "CANCELLED"
        assert cancelled["stop"]["reason"] == "cancelled"
        assert cancelled["totals"]["executed_this_invocation"] == 1
        assert cancelled["totals"]["cancelled"] == 1
        first_attempts = cancelled["totals"]["attempted_calls"]
        assert len(provider.inference_requests) == first_attempts
        interrupted_id = cancelled["stop"]["operation_id"]
        assert (out / "REPORT.md").is_file()

        monkeypatch.setattr(cli_module, "SessionCellRunner", real_runner)
        assert (
            _loopback(
                out,
                registry,
                "--select",
                "screening",
                "--execute",
                "--idle-seconds",
                "0",
            )
            == 1
        )
        resumed = json.loads((out / "run-summary.json").read_text(encoding="utf-8"))
        assert resumed["totals"]["already_completed"] == 1
        assert resumed["totals"]["preserved_partial"] == 1
        assert resumed["totals"]["executed_this_invocation"] == 25
        assert resumed["totals"]["attempted_calls"] >= 25 * 10
        # The first cell and the interrupted cell are never replayed.
        assert len(provider.inference_requests) == (
            first_attempts + resumed["totals"]["attempted_calls"]
        )
        by_id = {cell["operation_id"]: cell for cell in resumed["cells"]}
        assert by_id[interrupted_id]["outcome"] == "preserved_interrupted"
        assert by_id[interrupted_id]["attempts"] == 0
        journal = json.loads((out / "program-state.json").read_text(encoding="utf-8"))
        statuses = {
            operation["status"] for operation in journal["operations"].values()
        }
        assert "interrupted" in statuses
        assert statuses <= {"completed", "interrupted"}
    finally:
        provider.stop()


def test_same_day_manifest_reuse_is_byte_exact(
    tmp_path: Path,
) -> None:
    provider = MockProvider(behavior=MockBehavior.STREAM_SUCCESS)
    url = provider.start()
    try:
        registry = _registry(tmp_path / "registry.json", url)
        out = tmp_path / "run"
        assert _loopback(out, registry) == 0
        before = {
            name: hashlib.sha256((out / name).read_bytes()).hexdigest()
            for name in ("plan.json", "manifest.json")
        }
        assert (
            _loopback(
                out,
                registry,
                "--manifest",
                str(out / "manifest.json"),
                "--select",
                "context",
            )
            == 0
        )
        after = {
            name: hashlib.sha256((out / name).read_bytes()).hexdigest()
            for name in ("plan.json", "manifest.json")
        }
        assert after == before
        # A reused manifest cannot escape its frozen run directory.
        assert (
            _loopback(
                tmp_path / "elsewhere",
                registry,
                "--manifest",
                str(out / "manifest.json"),
            )
            == 2
        )
    finally:
        provider.stop()


def test_same_day_per_cell_argv_runs_without_an_anchor(
    tmp_path: Path,
) -> None:
    provider = MockProvider(behavior=MockBehavior.STREAM_SUCCESS)
    url = provider.start()
    try:
        registry = _registry(tmp_path / "registry.json", url)
        out = tmp_path / "run"
        assert _loopback(out, registry) == 0
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        operation = next(
            item
            for item in manifest["operations"]
            if item["operation_id"] == "SDS-CTX-32768-C1"
        )
        argv = list(operation["argv"])
        # The manifest's exact argv is executable for a loopback target without
        # any release anchor or temporal window.
        assert argv[0] == "endpoint-bench"
        argv[argv.index("--manifest") + 1] = str(out / "manifest.json")
        assert main(argv[1:]) == 0
        assert len(provider.inference_requests) == 6
        cell = json.loads(
            (out / "cells/SDS-CTX-32768-C1/cell.json").read_text(encoding="utf-8")
        )
        assert cell["status"] == "completed"
        assert cell["workload_type"] == "live_turn_session_dynamics_same_day"
    finally:
        provider.stop()


def test_same_day_leaves_legacy_evidence_untouched(
    tmp_path: Path,
) -> None:
    assert (
        hashlib.sha256(V1_PROGRAM.read_bytes()).hexdigest()
        == LEGACY_PROGRAM_SHA256
    )
    # A fresh clone has no private live run directory. Pin preservation with
    # synthetic legacy evidence in a sibling directory instead.
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "program-state.json").write_text('{"status":"completed","attempts":2}\n')
    (legacy / "calls.jsonl").write_text('{"request_id":"synthetic-legacy","outcome":"success"}\n')
    before = _hash_tree(legacy)
    provider = MockProvider(behavior=MockBehavior.STREAM_SUCCESS)
    url = provider.start()
    try:
        registry = _registry(tmp_path / "registry.json", url)
        assert (
            _loopback(
                tmp_path / "run",
                registry,
                "--select",
                "context",
                "--execute",
                "--idle-seconds",
                "0",
            )
            == 0
        )
    finally:
        provider.stop()
    after = _hash_tree(legacy)
    assert after == before
    assert len(before) == 2
