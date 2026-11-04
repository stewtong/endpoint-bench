"""The session CLI: zero-call preflight, loopback execution, resume and guards."""

from __future__ import annotations

import json
from pathlib import Path

from endpoint_bench.cli import main
from endpoint_bench.mock_provider import MockBehavior, MockProvider
from endpoint_bench.session_program import (
    build_manifest,
    build_plan,
    canonical_json,
    load_program,
    manifest_execution_identity_sha256,
    sha256_text,
)

PROGRAM = Path(__file__).resolve().parents[1] / "configs/session-programs/session-dynamics-v1.json"


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


def _dry_run(tmp_path: Path, base_url: str) -> Path:
    registry = _registry(tmp_path / "registry.json", base_url)
    run_dir = tmp_path / "run"
    code = main(
        [
            "session",
            "dry-run",
            "--program",
            str(PROGRAM),
            "--config",
            str(registry),
            "--target",
            "mock-target",
            "--out",
            str(run_dir),
            "--max-usd",
            "31",
        ]
    )
    assert code == 0
    return run_dir


def test_generate_is_zero_inference(tmp_path: Path) -> None:
    out = tmp_path / "plan"
    code = main(
        ["session", "generate", "--program", str(PROGRAM), "--out", str(out)]
    )

    assert code == 0
    assert (out / "plan.json").is_file()
    assert (out / "manifest.json").is_file()
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["totals"]["max_inference_attempts"] == 1038
    assert manifest["release"]["live_released_operation_ids"] == []


def test_dry_run_makes_zero_requests_and_rejects_a_low_guard(tmp_path: Path) -> None:
    provider = MockProvider(behavior=MockBehavior.STREAM_SUCCESS)
    url = provider.start()
    try:
        registry = _registry(tmp_path / "registry.json", url)
        run_dir = tmp_path / "run"
        assert (
            main(
                [
                    "session",
                    "dry-run",
                    "--program",
                    str(PROGRAM),
                    "--config",
                    str(registry),
                    "--target",
                    "mock-target",
                    "--out",
                    str(run_dir),
                    "--max-usd",
                    "1",
                ]
            )
            == 2
        )
        assert provider.inference_requests == []
        assert not (run_dir / "manifest.json").exists()
    finally:
        provider.stop()


def test_execute_loopback_and_never_repeat_a_completed_cell(tmp_path: Path) -> None:
    provider = MockProvider(behavior=MockBehavior.STREAM_SUCCESS)
    url = provider.start()
    try:
        run_dir = _dry_run(tmp_path, url)
        assert provider.inference_requests == []

        code = main(
            [
                "session",
                "execute",
                "--manifest",
                str(run_dir / "manifest.json"),
                "--operation",
                "SD-independent-C1",
                "--execute",
                "--max-requests",
                "20",
                "--max-usd",
                "2",
                "--authorize-live",
            ]
        )
        assert code == 0
        assert len(provider.inference_requests) == 20
        state = json.loads((run_dir / "program-state.json").read_text(encoding="utf-8"))
        assert state["operations"]["SD-independent-C1"]["status"] == "completed"
        assert (run_dir / "cells" / "SD-independent-C1" / "cell.json").is_file()

        before = len(provider.inference_requests)
        code = main(
            [
                "session",
                "execute",
                "--manifest",
                str(run_dir / "manifest.json"),
                "--operation",
                "SD-independent-C1",
                "--execute",
                "--max-requests",
                "20",
                "--max-usd",
                "2",
                "--authorize-live",
            ]
        )
        assert code == 0
        assert len(provider.inference_requests) == before
    finally:
        provider.stop()


def test_execute_rejects_a_live_unreleased_operation(tmp_path: Path) -> None:
    program = load_program(PROGRAM)
    plan = build_plan(program, output_dir=tmp_path, release_nonce="test")
    manifest = build_manifest(
        plan,
        manifest_path=tmp_path / "manifest.json",
        plan_path=tmp_path / "plan.json",
        target={
            "deployment_key": "live",
            "model_id": "model",
            "base_url": "https://example.invalid/v1",
            "protocol": "openai-chat-completions",
            "region": "r",
            "credential_env_name": "LIVE_KEY",
            "bound": True,
        },
    )
    (tmp_path / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    code = main(
        [
            "session",
            "execute",
            "--manifest",
            str(tmp_path / "manifest.json"),
            "--operation",
            "SD-independent-C1",
            "--execute",
            "--max-requests",
            "20",
            "--max-usd",
            "2",
            "--authorize-live",
        ]
    )
    assert code == 2
    assert not (tmp_path / "program-state.json").exists()


def test_execute_requires_the_exact_cell_request_budget(tmp_path: Path) -> None:
    provider = MockProvider(behavior=MockBehavior.STREAM_SUCCESS)
    url = provider.start()
    try:
        run_dir = _dry_run(tmp_path, url)
        code = main(
            [
                "session",
                "execute",
                "--manifest",
                str(run_dir / "manifest.json"),
                "--operation",
                "SD-independent-C1",
                "--execute",
                "--max-requests",
                "1",
                "--authorize-live",
            ]
        )
        assert code == 2
        assert provider.inference_requests == []
    finally:
        provider.stop()


def test_execute_refuses_an_interrupted_partial_cell(tmp_path: Path) -> None:
    provider = MockProvider(behavior=MockBehavior.STREAM_SUCCESS)
    url = provider.start()
    try:
        run_dir = _dry_run(tmp_path, url)
        state = {
            "schema_version": "session-state/1.0.0",
            "program_id": "session-dynamics-v1",
            "plan_sha256": json.loads(
                (run_dir / "manifest.json").read_text(encoding="utf-8")
            )["plan_sha256"],
            "operations": {
                "SD-independent-C1": {
                    "status": "started",
                    "started_at_utc": "2026-11-02T00:00:00Z",
                }
            },
            "attempts": {},
        }
        (run_dir / "program-state.json").write_text(json.dumps(state), encoding="utf-8")
        code = main(
            [
                "session",
                "execute",
                "--manifest",
                str(run_dir / "manifest.json"),
                "--operation",
                "SD-independent-C1",
                "--execute",
                "--max-requests",
                "20",
                "--authorize-live",
            ]
        )
        assert code == 2
        assert provider.inference_requests == []
        reloaded = json.loads((run_dir / "program-state.json").read_text(encoding="utf-8"))
        assert reloaded["operations"]["SD-independent-C1"]["status"] == "interrupted"
    finally:
        provider.stop()


def test_analyze_partial_evidence(tmp_path: Path) -> None:
    provider = MockProvider(behavior=MockBehavior.STREAM_SUCCESS)
    url = provider.start()
    try:
        run_dir = _dry_run(tmp_path, url)
        assert (
            main(
                [
                    "session",
                    "execute",
                    "--manifest",
                    str(run_dir / "manifest.json"),
                    "--operation",
                    "SD-independent-C1",
                    "--execute",
                    "--max-requests",
                    "20",
                    "--authorize-live",
                ]
            )
            == 0
        )
        assert (
            main(
                [
                    "session",
                    "analyze",
                    "--run-dir",
                    str(run_dir),
                    "--manifest",
                    str(run_dir / "manifest.json"),
                    "--allow-partial",
                ]
            )
            == 0
        )
        report = json.loads((run_dir / "analysis.json").read_text(encoding="utf-8"))
        assert report["cell_count"] == 1
        assert report["missing_operations"]
        assert (run_dir / "analysis.md").is_file()
    finally:
        provider.stop()


def _set_anchor(run_dir: Path, anchor: str) -> None:
    path = run_dir / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["release"]["release_anchor_utc"] = anchor
    manifest["manifest_sha256"] = sha256_text(
        canonical_json(
            {key: value for key, value in manifest.items() if key != "manifest_sha256"}
        )
    )
    path.write_text(json.dumps(manifest), encoding="utf-8")


def test_missed_temporal_window_is_refused_not_backfilled(tmp_path: Path) -> None:
    provider = MockProvider(behavior=MockBehavior.STREAM_SUCCESS)
    url = provider.start()
    try:
        run_dir = _dry_run(tmp_path, url)
        _set_anchor(run_dir, "2020-01-01T00:00:00Z")
        code = main(
            [
                "session",
                "execute",
                "--manifest",
                str(run_dir / "manifest.json"),
                "--operation",
                "TR-W1-growing_transcript-C1",
                "--execute",
                "--max-requests",
                "20",
                "--authorize-live",
            ]
        )
        assert code == 2
        assert provider.inference_requests == []
    finally:
        provider.stop()


def test_not_yet_open_window_is_refused(tmp_path: Path) -> None:
    provider = MockProvider(behavior=MockBehavior.STREAM_SUCCESS)
    url = provider.start()
    try:
        run_dir = _dry_run(tmp_path, url)
        _set_anchor(run_dir, "2099-01-01T00:00:00Z")
        code = main(
            [
                "session",
                "execute",
                "--manifest",
                str(run_dir / "manifest.json"),
                "--operation",
                "TR-W2-growing_transcript-C1",
                "--execute",
                "--max-requests",
                "20",
                "--authorize-live",
            ]
        )
        assert code == 2
        assert provider.inference_requests == []
    finally:
        provider.stop()


def test_late_screening_is_refused(tmp_path: Path) -> None:
    provider = MockProvider(behavior=MockBehavior.STREAM_SUCCESS)
    url = provider.start()
    try:
        run_dir = _dry_run(tmp_path, url)
        _set_anchor(run_dir, "2020-01-01T00:00:00Z")
        code = main(
            [
                "session",
                "execute",
                "--manifest",
                str(run_dir / "manifest.json"),
                "--operation",
                "SD-independent-C1",
                "--execute",
                "--max-requests",
                "20",
                "--authorize-live",
            ]
        )
        assert code == 2
        assert provider.inference_requests == []
    finally:
        provider.stop()


def test_program_elapsed_cap_is_refused(tmp_path: Path) -> None:
    provider = MockProvider(behavior=MockBehavior.STREAM_SUCCESS)
    url = provider.start()
    try:
        run_dir = _dry_run(tmp_path, url)
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        state = {
            "schema_version": "session-state/1.0.0",
            "program_id": "session-dynamics-v1",
            "plan_sha256": manifest["plan_sha256"],
            "created_at_utc": "2020-01-01T00:00:00Z",
            "updated_at_utc": "2020-01-01T00:00:00Z",
            "operations": {},
            "attempts": {},
        }
        (run_dir / "program-state.json").write_text(json.dumps(state), encoding="utf-8")
        code = main(
            [
                "session",
                "execute",
                "--manifest",
                str(run_dir / "manifest.json"),
                "--operation",
                "SD-independent-C1",
                "--execute",
                "--max-requests",
                "20",
                "--authorize-live",
            ]
        )
        assert code == 2
        assert provider.inference_requests == []
    finally:
        provider.stop()


def test_program_active_cap_is_refused(tmp_path: Path) -> None:
    provider = MockProvider(behavior=MockBehavior.STREAM_SUCCESS)
    url = provider.start()
    try:
        run_dir = _dry_run(tmp_path, url)
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        state = {
            "schema_version": "session-state/1.0.0",
            "program_id": "session-dynamics-v1",
            "plan_sha256": manifest["plan_sha256"],
            "created_at_utc": manifest["generated_at_utc"],
            "updated_at_utc": manifest["generated_at_utc"],
            "operations": {
                "prior": {"status": "completed", "duration_ms": 36.0 * 3600.0 * 1000.0}
            },
            "attempts": {},
        }
        (run_dir / "program-state.json").write_text(json.dumps(state), encoding="utf-8")
        code = main(
            [
                "session",
                "execute",
                "--manifest",
                str(run_dir / "manifest.json"),
                "--operation",
                "SD-independent-C1",
                "--execute",
                "--max-requests",
                "20",
                "--authorize-live",
            ]
        )
        assert code == 2
        assert provider.inference_requests == []
    finally:
        provider.stop()


# -- F4/additional: runtime identity, state accounting and locking ----------


def _reseal(manifest: dict) -> None:
    manifest["execution_identity_sha256"] = manifest_execution_identity_sha256(
        manifest
    )
    manifest["manifest_sha256"] = sha256_text(
        canonical_json(
            {key: value for key, value in manifest.items() if key != "manifest_sha256"}
        )
    )


def test_state_override_cannot_bypass_no_replay(tmp_path: Path) -> None:
    provider = MockProvider(behavior=MockBehavior.STREAM_SUCCESS)
    url = provider.start()
    try:
        run_dir = _dry_run(tmp_path, url)
        alternate = tmp_path / "other-state.json"
        code = main(
            [
                "session",
                "execute",
                "--manifest",
                str(run_dir / "manifest.json"),
                "--operation",
                "SD-independent-C1",
                "--state",
                str(alternate),
                "--execute",
                "--max-requests",
                "20",
                "--authorize-live",
            ]
        )
        assert code == 2
        assert provider.inference_requests == []
        assert not alternate.exists()
    finally:
        provider.stop()


def test_execute_rejects_a_tampered_plan_before_journal(tmp_path: Path) -> None:
    provider = MockProvider(behavior=MockBehavior.STREAM_SUCCESS)
    url = provider.start()
    try:
        run_dir = _dry_run(tmp_path, url)
        plan_path = run_dir / "plan.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan["operations"][0]["order_seed"] = 999
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        code = main(
            [
                "session",
                "execute",
                "--manifest",
                str(run_dir / "manifest.json"),
                "--operation",
                "SD-independent-C1",
                "--execute",
                "--max-requests",
                "20",
                "--authorize-live",
            ]
        )
        assert code == 2
        assert provider.inference_requests == []
        assert not (run_dir / "program-state.json").exists()
    finally:
        provider.stop()


def test_execute_rejects_manifest_timeout_drift_before_journal(tmp_path: Path) -> None:
    provider = MockProvider(behavior=MockBehavior.STREAM_SUCCESS)
    url = provider.start()
    try:
        run_dir = _dry_run(tmp_path, url)
        manifest_path = run_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["operations"][0]["timeouts"]["request_timeout_s"] = 1
        _reseal(manifest)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        code = main(
            [
                "session",
                "execute",
                "--manifest",
                str(manifest_path),
                "--operation",
                "SD-independent-C1",
                "--execute",
                "--max-requests",
                "20",
                "--authorize-live",
            ]
        )
        assert code == 2
        assert provider.inference_requests == []
        assert not (run_dir / "program-state.json").exists()
    finally:
        provider.stop()


def test_execute_rejects_runtime_source_drift_before_journal(
    tmp_path: Path, monkeypatch
) -> None:
    provider = MockProvider(behavior=MockBehavior.STREAM_SUCCESS)
    url = provider.start()
    try:
        run_dir = _dry_run(tmp_path, url)

        def refuse(*args, **kwargs):
            raise __import__(
                "endpoint_bench.session_program", fromlist=["SessionProgramError"]
            ).SessionProgramError("frozen source drift: simulated")

        monkeypatch.setattr(
            "endpoint_bench.session_program.verify_runtime_sources", refuse
        )
        code = main(
            [
                "session",
                "execute",
                "--manifest",
                str(run_dir / "manifest.json"),
                "--operation",
                "SD-independent-C1",
                "--execute",
                "--max-requests",
                "20",
                "--authorize-live",
            ]
        )
        assert code == 2
        assert provider.inference_requests == []
        assert not (run_dir / "program-state.json").exists()
    finally:
        provider.stop()
