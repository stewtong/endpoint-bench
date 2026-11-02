from __future__ import annotations

import json
from pathlib import Path

import pytest

from endpoint_bench.cli import main
from endpoint_bench.cost import estimate_cost_usd
from endpoint_bench.profiles import load_profile
from endpoint_bench.registry import load_registry


def test_registry_validate(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["registry", "validate"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "valid"
    assert payload["deployment_count"] == 1


def test_dry_run(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(
        [
            "run",
            "--target",
            "offline-mock-smoke",
            "--profile",
            "smoke",
            "--dry-run",
        ]
    )

    assert exit_code == 0
    manifest = json.loads(capsys.readouterr().out)
    assert manifest["manifest_type"] == "dry_run"
    assert manifest["inference"]["network_requests"] == 0


def test_run_requires_one_mode(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["run", "--target", "offline-mock-smoke"])

    assert exc_info.value.code == 2
    assert "one of the arguments" in capsys.readouterr().err


def test_execute_fails_closed_without_ceilings(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["run", "--target", "offline-mock-smoke", "--execute"])

    assert exit_code == 2
    assert "requires --max-requests" in capsys.readouterr().err


def test_execute_rejects_request_ceiling_above_profile(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(
        ["run", "--target", "offline-mock-smoke", "--execute", "--max-requests", "999"]
    )

    assert exit_code == 2
    assert "exceeds the profile" in capsys.readouterr().err


def test_unknown_target_fails(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["run", "--target", "does-not-exist", "--dry-run"]) == 2
    assert "unknown target" in capsys.readouterr().err


def test_profile_name_mismatch_fails(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(
        [
            "run",
            "--target",
            "offline-mock-smoke",
            "--profile",
            "standard",
            "--profile-file",
            "configs/profiles/smoke.json",
            "--dry-run",
        ]
    )

    assert exit_code == 2
    assert "does not match requested profile" in capsys.readouterr().err


def test_profile_families_resolve_by_name(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(
        ["run", "--target", "offline-mock-smoke", "--profile", "quick", "--dry-run"]
    )

    assert exit_code == 0
    manifest = json.loads(capsys.readouterr().out)
    assert manifest["profile"]["name"] == "quick"
    assert manifest["profile"]["request_count"] == 20


def test_profile_file_override(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(
        [
            "run",
            "--target",
            "offline-mock-smoke",
            "--profile",
            "smoke",
            "--profile-file",
            "configs/profiles/smoke.json",
            "--dry-run",
        ]
    )

    assert exit_code == 0
    manifest = json.loads(capsys.readouterr().out)
    assert manifest["profile"]["name"] == "smoke"


# Cost preflight: an estimate must exist and must fit under the ceiling.


def _priced_deployment(tmp_path: Path, **pricing):
    document = json.loads(Path("configs/registry.example.json").read_text())
    document["providers"][0]["base_url"] = "https://api.example.invalid/v1"
    deployment = document["providers"][0]["deployments"][0]
    deployment["pricing"] = {
        "currency": "USD",
        "prompt_per_million_tokens": 0.30,
        "completion_per_million_tokens": 1.20,
        "verified": True,
        "source": "test fixture",
        "effective_date": "2026-11-01",
        **pricing,
    }
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(document))
    return path


def test_estimate_is_none_without_verified_rates() -> None:
    registry = load_registry(Path("configs/registry.example.json"))
    deployment = registry.deployments["offline-mock-smoke"]
    profile = load_profile(Path("configs/profiles/smoke.json"), "smoke")

    assert estimate_cost_usd(deployment, profile) is None


def test_estimate_scales_with_request_count(tmp_path: Path) -> None:
    registry = load_registry(_priced_deployment(tmp_path))
    deployment = registry.deployments["offline-mock-smoke"]
    smoke = load_profile(Path("configs/profiles/smoke.json"), "smoke")
    quick = load_profile(Path("configs/profiles/quick.json"), "quick")

    smoke_estimate = estimate_cost_usd(deployment, smoke)
    quick_estimate = estimate_cost_usd(deployment, quick)

    assert smoke_estimate is not None and quick_estimate is not None
    assert quick_estimate > smoke_estimate


def test_execute_refuses_when_estimate_exceeds_ceiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "configs" / "profiles").mkdir(parents=True)
    (tmp_path / "configs" / "profiles" / "smoke.json").write_text(
        Path("configs/profiles/smoke.json").read_text()
    )
    registry_path = _priced_deployment(tmp_path)
    monkeypatch.chdir(tmp_path)

    exit_code = main(
        [
            "run",
            "--config",
            str(registry_path),
            "--target",
            "offline-mock-smoke",
            "--execute",
            "--authorize-live",
            "--max-requests",
            "2",
            "--max-usd",
            "0.000001",
        ]
    )

    assert exit_code == 2
    assert "exceeds --max-usd" in capsys.readouterr().err


def test_execute_refuses_verified_pricing_without_rates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "configs" / "profiles").mkdir(parents=True)
    (tmp_path / "configs" / "profiles" / "smoke.json").write_text(
        Path("configs/profiles/smoke.json").read_text()
    )
    registry_path = _priced_deployment(
        tmp_path, prompt_per_million_tokens=None, completion_per_million_tokens=None
    )
    monkeypatch.chdir(tmp_path)

    exit_code = main(
        [
            "run",
            "--config",
            str(registry_path),
            "--target",
            "offline-mock-smoke",
            "--execute",
            "--authorize-live",
            "--max-requests",
            "2",
            "--max-usd",
            "5",
        ]
    )

    assert exit_code == 2
    assert "cost estimate unavailable" in capsys.readouterr().err


def test_generated_config_requests_raw_evidence(tmp_path: Path) -> None:
    """Web-search behaviour depends on it, and the live run needs HTTP status."""
    from endpoint_bench.aiperf import BASELINE_CLIENT, build_benchmark_config

    config = build_benchmark_config(
        model_id="m",
        base_url="http://127.0.0.1:9",
        client=BASELINE_CLIENT,
        requests=1,
        concurrency=1,
        input_tokens=8,
        output_tokens=4,
        artifact_dir=tmp_path,
        credential_env_name=None,
        capture_raw=True,
    )

    assert config["benchmark"]["artifacts"]["raw"] is True


def test_concurrency_override_is_applied_and_echoed(tmp_path: Path) -> None:
    (tmp_path / "configs" / "profiles").mkdir(parents=True)
    for name in ("screening-1k",):
        (tmp_path / "configs" / "profiles" / f"{name}.json").write_text(
            Path(f"configs/profiles/{name}.json").read_text()
        )
    registry_path = Path("configs/registry.example.json").resolve()
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.chdir(tmp_path)
    try:
        import contextlib
        import io

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            exit_code = main(
                [
                    "run",
                    "--config",
                    str(registry_path),
                    "--target",
                    "offline-mock-smoke",
                    "--profile",
                    "screening-1k",
                    "--concurrency",
                    "2",
                    "--dry-run",
                ]
            )
        payload = json.loads(buffer.getvalue())
    finally:
        monkeypatch.undo()

    assert exit_code == 0
    assert payload["profile"]["concurrency"] == 2
    assert payload["planned_exposure"]["max_concurrency"] == 2


def test_concurrency_override_above_request_count_is_rejected(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(
        [
            "run",
            "--target",
            "offline-mock-smoke",
            "--profile",
            "screening-1k",
            "--concurrency",
            "999",
            "--dry-run",
        ]
    )

    assert exit_code == 2
    assert "exceeds the profile" in capsys.readouterr().err


def test_probe_dry_run_plans_without_sending(tmp_path: Path, capsys) -> None:
    profiles = tmp_path / "configs" / "profiles"
    profiles.mkdir(parents=True)
    (profiles / "smoke.json").write_text(Path("configs/profiles/smoke.json").read_text())
    document = json.loads(Path("configs/registry.example.json").read_text())
    document["providers"][0]["base_url"] = "http://198.51.100.7:8000/v1"
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps(document))
    monkey = pytest.MonkeyPatch()
    monkey.chdir(tmp_path)
    try:
        exit_code = main(
            [
                "probe",
                "--config",
                str(registry_path),
                "--target",
                "offline-mock-smoke",
                "--profile",
                "smoke",
                "--scenario",
                "tool-round-trip",
                "--requests",
                "1",
                "--dry-run",
            ]
        )
    finally:
        monkey.undo()

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["manifest_type"] == "probe_dry_run"
    assert payload["planned_trials"] == 1
    assert payload["planned_http_attempts"] == 2
    assert payload["inference"]["network_requests"] == 0
    assert payload["cost_preflight"]["estimate_usd"] is None  # example pricing unverified
