"""Evidence writing, restart journal and analysis denominators."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from endpoint_bench.session_engine import (
    OUTCOME_OUTPUT_TRUNCATED,
    OUTCOME_SERVER_ERROR,
    OUTCOME_SKIPPED,
    OUTCOME_SUCCESS,
    OUTCOME_UNSUPPORTED_TOOL,
    CallEvidence,
    CellEvidence,
    SessionEvidence,
)
from endpoint_bench.session_evidence import (
    JsonJournal,
    SessionEvidenceError,
    analyze_cells,
    load_cell,
    render_markdown,
    validate_manifest_document,
    write_cell_evidence,
)
from endpoint_bench.session_program import (
    build_manifest,
    build_plan,
    load_program,
)

PROGRAM = Path(__file__).resolve().parents[1] / "configs/session-programs/session-dynamics-v1.json"


def _call(
    session_id: str,
    turn: int,
    *,
    outcome: str = OUTCOME_SUCCESS,
    active_ms: float = 100.0,
    cache_read: int | None = None,
) -> CallEvidence:
    call = CallEvidence(
        operation_id="SD-test-C1",
        scenario="growing_transcript",
        phase="session_screening",
        window=None,
        concurrent_sessions=1,
        session_id=session_id,
        session_index=0,
        template="coding",
        group_id="g",
        prefix_group_id=None,
        turn_group_id="g",
        turn_index=turn,
        request_id=f"{session_id}-t{turn:02d}",
        seed=1,
        active_ms=active_ms,
        ttft_any_ms=10.0,
        ttft_visible_ms=12.0,
        http_status=200,
        finish_reason="stop",
        finish_class="natural",
        outcome=outcome,
        visible_content="answer",
        visible_chars=6,
        visible_missing=False,
        cache_read_tokens=cache_read,
    )
    call.visible_content_sha256 = __import__("hashlib").sha256(b"answer").hexdigest()
    if outcome == OUTCOME_OUTPUT_TRUNCATED:
        call.finish_reason = "length"
        call.finish_class = "ceiling"
        call.truncated = True
    if outcome == OUTCOME_SERVER_ERROR:
        call.http_status = 503
        call.finish_reason = None
        call.finish_class = "absent"
        call.visible_content = ""
        call.visible_chars = 0
        call.visible_missing = True
        call.visible_content_sha256 = None
    if outcome == OUTCOME_SKIPPED:
        call.http_status = None
        call.visible_content = ""
        call.visible_chars = 0
        call.visible_missing = True
        call.visible_content_sha256 = None
    return call


def _cell() -> CellEvidence:
    sessions: list[SessionEvidence] = []
    for index in range(3):
        session = SessionEvidence(
            session_id=f"SD-test-C1-s{index + 1:02d}",
            session_index=index,
            template="coding",
            group_id="g",
            seed=1,
            scenario="growing_transcript",
            operation_id="SD-test-C1",
            planned_calls=4,
            status="completed",
            elapsed_ms=500.0,
            active_ms=400.0,
            idle_ms=0.0,
        )
        session.calls = [
            _call(session.session_id, 1, cache_read=10 if index == 0 else None),
            _call(session.session_id, 2),
            _call(session.session_id, 3),
            _call(session.session_id, 4),
        ]
        session.attempted_calls = 4
        session.transcript = [
            {"turn": call.turn_index, "messages_sent": [], "assistant_reply": "answer"}
            for call in session.calls
        ]
        session.transcript_sha256 = "0" * 64
        session.transcript_messages = len(session.transcript)
        sessions.append(session)
    evidence = CellEvidence(
        operation_id="SD-test-C1",
        scenario="growing_transcript",
        phase="session_screening",
        window=None,
        concurrency=1,
        planned_sessions=3,
        planned_calls=12,
        attempted_calls=12,
        status="completed",
        achieved_max_inflight_calls=1,
        achieved_max_concurrent_sessions=1,
        sessions=sessions,
    )
    return evidence


def test_journal_reconciles_interrupted_and_never_repeats(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    journal = JsonJournal(path, program_id="p", plan_sha256="a" * 64)
    journal.begin_operation("OP-1")
    journal.attempt_started("OP-1-s01-t01", {"operation_id": "OP-1"})
    assert journal.lock_held is True
    journal.close()

    reloaded = JsonJournal(path, program_id="p", plan_sha256="a" * 64)

    assert reloaded.operation_status("OP-1") == "interrupted"
    assert reloaded.state["attempts"]["OP-1-s01-t01"]["status"] == "interrupted"
    with pytest.raises(SessionEvidenceError, match="already interrupted"):
        reloaded.begin_operation("OP-1")
    reloaded.close()


def test_journal_rejects_duplicate_request_and_plan_swap(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    journal = JsonJournal(path, program_id="p", plan_sha256="a" * 64)
    journal.begin_operation("OP-1")
    journal.attempt_started("OP-1-s01-t01", {"operation_id": "OP-1"})
    with pytest.raises(SessionEvidenceError, match="already has an attempt"):
        journal.attempt_started("OP-1-s01-t01", {"operation_id": "OP-1"})
    journal.close()
    with pytest.raises(SessionEvidenceError, match="refusing to mix evidence"):
        JsonJournal(path, program_id="p", plan_sha256="b" * 64)


def test_second_journal_cannot_open_while_lock_is_held(tmp_path: Path) -> None:
    """A second executor must not reconcile a live operation to interrupted."""
    path = tmp_path / "state.json"
    journal = JsonJournal(path, program_id="p", plan_sha256="a" * 64)
    try:
        with pytest.raises(SessionEvidenceError, match="exclusive program-state lock"):
            JsonJournal(path, program_id="p", plan_sha256="a" * 64)
    finally:
        journal.close()


def test_journal_tracks_only_unstarted_operations_for_resume(tmp_path: Path) -> None:
    journal = JsonJournal(tmp_path / "state.json", program_id="p", plan_sha256="a" * 64)
    journal.begin_operation("OP-1")
    journal.finish_operation("OP-1", cell_path="cells/OP-1", counters={}, status="completed", stop_reason="completed")
    journal.begin_operation("OP-2")

    assert journal.completed_operations() == {"OP-1"}
    assert journal.operation_status("OP-3") == "unstarted"
    assert journal.operation_status("OP-2") == "started"


def test_evidence_roundtrip_and_duplicate_request_rejection(tmp_path: Path) -> None:
    evidence = _cell()
    artifacts = write_cell_evidence(tmp_path, evidence)
    loaded = load_cell(artifacts.cell_path)
    assert loaded["operation_id"] == evidence.operation_id
    assert loaded["counters"]["attempted_calls"] == 12

    first_request = loaded["sessions"][0]["calls"][0]["request_id"]
    loaded["sessions"][0]["calls"][1]["request_id"] = first_request
    target = tmp_path / "tampered.json"
    target.write_text(json.dumps(loaded), encoding="utf-8")
    with pytest.raises(SessionEvidenceError, match="duplicate request identity"):
        load_cell(target)


def test_evidence_refuses_to_replace_a_completed_cell(tmp_path: Path) -> None:
    write_cell_evidence(tmp_path, _cell())
    with pytest.raises(SessionEvidenceError, match="already exists"):
        write_cell_evidence(tmp_path, _cell())


def test_analysis_denominators_and_null_telemetry(tmp_path: Path) -> None:
    evidence = _cell()
    # Replace the last session's calls with one truncation, one failure and two skips.
    evidence.sessions[2].calls = [
        _call(evidence.sessions[2].session_id, 1, outcome=OUTCOME_OUTPUT_TRUNCATED),
        _call(evidence.sessions[2].session_id, 2, outcome=OUTCOME_SERVER_ERROR),
        _call(evidence.sessions[2].session_id, 3, outcome=OUTCOME_SKIPPED),
        _call(evidence.sessions[2].session_id, 4, outcome=OUTCOME_SKIPPED),
    ]
    write_cell_evidence(tmp_path, evidence)

    report = analyze_cells(tmp_path, require_complete=False)
    summary = report["scenarios"]["growing_transcript"]["1"]
    denominators = summary["denominators"]

    assert denominators["records_total"] == 12
    assert denominators["successful_calls"] == 8
    assert denominators["truncated_calls"] == 1
    assert denominators["failed_calls"] == 1
    assert denominators["skipped_or_ineligible_calls"] == 2
    assert denominators["attempted_calls"] == 10
    assert denominators["partition_consistent"] is True
    assert summary["active_latency_censored_lower_bound_ms"]["n"] == 1
    assert summary["cache_telemetry"]["cache_read_sum"] == 10
    assert summary["cache_telemetry"]["calls_reporting_read"] == 1
    assert summary["cache_telemetry"]["cache_write_sum"] is None
    assert summary["cache_telemetry"]["timing_inferred_benefit"] is None
    assert report["claims"]["qualification"] is None
    assert report["claims"]["population_inference"] is False


def test_analysis_rejects_duplicate_operation_evidence(tmp_path: Path) -> None:
    artifacts = write_cell_evidence(tmp_path, _cell())
    duplicate = tmp_path / "cells" / "duplicate"
    duplicate.mkdir()
    shutil.copy(artifacts.cell_path, duplicate / "cell.json")

    with pytest.raises(SessionEvidenceError, match="duplicate cell evidence"):
        analyze_cells(tmp_path, require_complete=False)


def test_analysis_manifest_completeness_and_partial_mode(tmp_path: Path) -> None:
    write_cell_evidence(tmp_path, _cell())
    manifest = {"operations": [{"operation_id": "SD-test-C1"}, {"operation_id": "SD-other-C1"}]}

    report = analyze_cells(tmp_path, manifest=manifest, require_complete=False)
    assert report["missing_operations"] == ["SD-other-C1"]
    with pytest.raises(SessionEvidenceError, match="no cell evidence"):
        analyze_cells(tmp_path, manifest=manifest, require_complete=True)


def test_markdown_report_keeps_denominators_and_warning(tmp_path: Path) -> None:
    write_cell_evidence(tmp_path, _cell())
    report = analyze_cells(tmp_path, require_complete=False)
    markdown = render_markdown(report)

    assert "Descriptive finite-sample evidence" in markdown
    assert "No threshold is ratified" in markdown
    assert "skipped" in markdown
    assert "API-accepted context is not demonstrated effective context" in markdown


def test_manifest_document_validation_rejects_edits(tmp_path: Path) -> None:
    program = load_program(PROGRAM)
    plan = build_plan(program, output_dir=tmp_path, release_nonce="test")
    manifest = build_manifest(
        plan, manifest_path=tmp_path / "manifest.json", plan_path=tmp_path / "plan.json"
    )
    validate_manifest_document(manifest)
    manifest["operations"][0]["max_inference_attempts"] = 21
    with pytest.raises(SessionEvidenceError, match="stale|edited|attempt total"):
        validate_manifest_document(manifest)


def test_analysis_partitions_tool_outcomes_instead_of_dropping_them(tmp_path: Path) -> None:
    evidence = _cell()
    evidence.sessions[2].calls = [
        _call(
            evidence.sessions[2].session_id,
            1,
            outcome=OUTCOME_UNSUPPORTED_TOOL,
        ),
        _call(evidence.sessions[2].session_id, 2),
        _call(evidence.sessions[2].session_id, 3),
        _call(evidence.sessions[2].session_id, 4),
    ]
    write_cell_evidence(tmp_path, evidence)

    report = analyze_cells(tmp_path, require_complete=False)
    denominators = report["scenarios"]["growing_transcript"]["1"]["denominators"]

    assert denominators["failed_calls"] == 1
    assert denominators["unsupported_tool_calls"] == 1
    assert denominators["partition_consistent"] is True
