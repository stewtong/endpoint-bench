from __future__ import annotations

import json

import pytest

from endpoint_bench.cli import main


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


def test_execute_fails_closed(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["run", "--target", "offline-mock-smoke", "--execute"])

    assert exc_info.value.code == 2
    assert "live execution is not implemented" in capsys.readouterr().err


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
            "--dry-run",
        ]
    )

    assert exit_code == 2
    assert "does not match requested profile" in capsys.readouterr().err


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
