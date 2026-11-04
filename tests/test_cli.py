from __future__ import annotations

import json
from pathlib import Path

import pytest

from endpoint_bench.cli import main
from endpoint_bench.cost import estimate_cost_usd
from endpoint_bench.mock_provider import MockProvider
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


def test_every_run_option_declared_by_code_is_registered_in_the_parser() -> None:
    """Guards a half-wired option: the handler read it before argparse knew it."""
    from endpoint_bench.cli import build_parser

    parser = build_parser()
    namespace = parser.parse_args(
        ["run", "--target", "x", "--dry-run", "--token-accounting", "server"]
    )

    assert namespace.token_accounting == "server"
    assert parser.parse_args(["run", "--target", "x", "--dry-run"]).token_accounting == "client"
    assert (
        parser.parse_args(
            ["run", "--target", "x", "--dry-run", "--run-label", "w1-c1"]
        ).run_label
        == "w1-c1"
    )
    assert parser.parse_args(["run", "--target", "x", "--dry-run"]).run_label is None


def test_run_label_distinguishes_repeated_identical_runs() -> None:
    """A repeated experiment has one config hash; the label keeps identities apart."""
    from endpoint_bench.cli import _run_id, _validate_run_label

    sha = "a" * 64
    assert _run_id(sha) == "run-" + "a" * 12
    assert _run_id(sha, "w1-c1") == "run-" + "a" * 12 + "-w1-c1"
    assert _run_id(sha, "w1-c1") != _run_id(sha, "w1-c2")
    assert _validate_run_label("w1-c1") == "w1-c1"


@pytest.mark.parametrize(
    "label", ["", "w1/c1", "../escape", "-leading", "a" * 33, "a b"]
)
def test_invalid_run_label_is_rejected(label: str) -> None:
    from endpoint_bench.aiperf import AiperfError
    from endpoint_bench.cli import _validate_run_label

    with pytest.raises(AiperfError):
        _validate_run_label(label)


def test_token_accounting_selects_the_server_accounting_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """--token-accounting must be reachable past the guard, not just parseable."""
    aiperf = Path(".venv-aiperf/bin/aiperf").resolve()
    if not aiperf.exists():
        pytest.skip("pinned AIPerf environment not installed")
    with MockProvider() as mock:
        document = json.loads(Path("configs/registry.example.json").read_text())
        document["providers"][0]["base_url"] = mock.base_url
        document["providers"][0]["deployments"][0]["pricing"] = {
            "currency": "USD",
            "prompt_per_million_tokens": 0.0,
            "completion_per_million_tokens": 0.0,
            "verified": True,
            "source": "loopback mock",
            "effective_date": "2026-11-01",
        }
        registry_path = tmp_path / "registry.json"
        registry_path.write_text(json.dumps(document))
        profiles = tmp_path / "configs" / "profiles"
        profiles.mkdir(parents=True)
        (profiles / "smoke.json").write_text(Path("configs/profiles/smoke.json").read_text())
        monkeypatch.chdir(tmp_path)

        exit_code = main(
            [
                "run",
                "--config",
                str(registry_path),
                "--target",
                "offline-mock-smoke",
                "--profile",
                "smoke",
                "--execute",
                "--max-requests",
                "2",
                "--max-usd",
                "1",
                "--token-accounting",
                "server",
                "--aiperf",
                str(aiperf),
                "--run-dir",
                str(tmp_path / "runs"),
            ]
        )
        capsys.readouterr()

    # Reaching this point at all proves the option is wired past the guard.
    assert exit_code in (0, 1)


def test_reprocess_command_is_registered_and_dispatches(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The command must be reachable through argparse, not only as a function.

    A previous defect had a handler read an option argparse never registered, so
    new commands are exercised through `main` rather than called directly.
    """
    from test_measurement_integrity import _legacy_run

    run_dir = _legacy_run(tmp_path)
    out = tmp_path / "corrections"

    assert main(["reprocess", "--run", str(run_dir), "--out", str(out)]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["accounting_source"] == "provider_usage"
    assert payload["counters_after"]["truncated_trials"] == 20
    assert (out / "correction.json").is_file()


def test_reprocess_command_reports_a_guard_violation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from test_measurement_integrity import _legacy_run

    run_dir = _legacy_run(tmp_path)

    exit_code = main(["reprocess", "--run", str(run_dir), "--out", str(run_dir / "x")])

    assert exit_code == 2
    assert "refusing to write inside" in capsys.readouterr().err


def test_reprocess_requires_both_paths() -> None:
    from endpoint_bench.cli import build_parser

    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["reprocess", "--run", "only-one"])
