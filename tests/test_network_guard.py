from __future__ import annotations

import json
from pathlib import Path

import pytest

from endpoint_bench.cli import main
from endpoint_bench.mock_provider import MockBehavior, MockProvider
from endpoint_bench.network_guard import (
    NetworkGuardError,
    assert_loopback,
    is_loopback,
    local_environment,
    parse_destination,
)
from endpoint_bench.probes import ProbeConfig, probe
from endpoint_bench.trials import OutcomeState, Trial

# -- destination parsing -----------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:9/v1",
        "http://127.0.0.1:18080",
        "https://127.0.0.1:443/v1",
        "http://[::1]:8080/v1",
    ],
)
def test_numeric_loopback_is_accepted(url: str) -> None:
    destination = assert_loopback(url)

    assert destination.is_loopback is True


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://localhost:8000/v1", "not a numeric address"),
        ("http://LOCALHOST:8000/v1", "not a numeric address"),
        ("http://127.0.0.1.evil.example:8000/v1", "not a numeric address"),
        ("http://127.0.0.1:8000@evil.example/v1", "embeds credentials"),
        ("http://user:pass@127.0.0.1:8000/v1", "embeds credentials"),
        ("http://127.0.0.1/v1", "explicit port"),
        ("http://127.0.0.1:99999/v1", "invalid port"),
        ("ftp://127.0.0.1:9/v1", "unsupported scheme"),
        ("127.0.0.1:9/v1", "unsupported scheme"),
        ("http://2130706433:9/v1", "not a numeric address"),
        ("http://0x7f000001:9/v1", "not a numeric address"),
        ("http://[::ffff:127.0.0.1]:9/v1", "IPv4-mapped"),
        ("", "empty"),
    ],
)
def test_ambiguous_or_external_destinations_are_rejected(url: str, reason: str) -> None:
    with pytest.raises(NetworkGuardError, match=reason):
        parse_destination(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.2:9/v1",  # loopback range, but not an allowed address
        "http://10.0.0.1:9/v1",
        "http://198.51.100.7:9/v1",
        "http://[2001:db8::1]:9/v1",
    ],
)
def test_non_allowlisted_addresses_fail_the_loopback_assertion(url: str) -> None:
    with pytest.raises(NetworkGuardError, match="not an allowed loopback address"):
        assert_loopback(url)
    assert is_loopback(url) is False


def test_destination_records_effective_identity() -> None:
    destination = parse_destination("http://127.0.0.1:9/v1")

    assert destination.to_dict() == {
        "scheme": "http",
        "host": "127.0.0.1",
        "port": 9,
        "path": "/v1",
        "is_loopback": True,
    }


# -- environment hygiene -----------------------------------------------------


def test_local_environment_strips_proxy_variables() -> None:
    base = {
        "PATH": "/usr/bin",
        "HTTP_PROXY": "http://proxy.example:3128",
        "HTTPS_PROXY": "http://proxy.example:3128",
        "ALL_PROXY": "socks5://proxy.example:1080",
        "http_proxy": "http://proxy.example:3128",
        "SECRET_KEY": "value",
    }

    environment = local_environment(base)

    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy"):
        assert name not in environment
    assert environment["NO_PROXY"] == "127.0.0.1,::1"
    assert environment["no_proxy"] == "127.0.0.1,::1"
    # Unrelated variables survive untouched.
    assert environment["PATH"] == "/usr/bin"
    assert environment["SECRET_KEY"] == "value"


def test_local_environment_can_seed_a_discovery_variable() -> None:
    environment = local_environment({}, discovery_prefixes=("EXAMPLE_NO_PROXY",))

    assert environment["EXAMPLE_NO_PROXY"] == "127.0.0.1,::1"


# -- redirects ---------------------------------------------------------------


def test_redirect_is_refused_rather_than_followed() -> None:
    with MockProvider(behavior=MockBehavior.REDIRECT) as mock:
        config = ProbeConfig(base_url=mock.base_url, model_id="mock-model", timeout_s=2.0)
        trial = Trial(trial_id="t", deployment_key="mock", profile_name="smoke")
        probe(config, api_key=None, prompt="hi", trial=trial)

        # Exactly one request: the redirect was not followed.
        assert len(mock.inference_requests) == 1

    assert trial.outcome is OutcomeState.PROTOCOL_FAILURE
    assert trial.outcome_detail == "redirect_not_permitted"
    assert trial.http_attempts == 1


# -- CLI authorization -------------------------------------------------------


def _registry_with(tmp_path: Path, base_url: str) -> Path:
    document = json.loads(Path("configs/registry.example.json").read_text())
    document["providers"][0]["base_url"] = base_url
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(document))
    return path


def _prepare_workspace(tmp_path: Path) -> None:
    profiles = tmp_path / "configs" / "profiles"
    profiles.mkdir(parents=True)
    (profiles / "smoke.json").write_text(Path("configs/profiles/smoke.json").read_text())


@pytest.mark.parametrize(
    "base_url",
    [
        "http://localhost:8000/v1",
        "http://127.0.0.2:8000/v1",
        "http://10.1.2.3:8000/v1",
        "http://127.0.0.1.evil.example:8000/v1",
    ],
)
def test_execute_refuses_non_numeric_or_external_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    base_url: str,
) -> None:
    _prepare_workspace(tmp_path)
    registry_path = _registry_with(tmp_path, base_url)
    monkeypatch.chdir(tmp_path)

    exit_code = main(
        [
            "run",
            "--config",
            str(registry_path),
            "--target",
            "offline-mock-smoke",
            "--execute",
            "--max-requests",
            "1",
        ]
    )

    assert exit_code == 2
    stderr = capsys.readouterr().err
    assert "local target rejected" in stderr or "not loopback" in stderr


def test_probe_refuses_non_loopback_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _prepare_workspace(tmp_path)
    registry_path = _registry_with(tmp_path, "http://198.51.100.7:8000/v1")
    monkeypatch.chdir(tmp_path)

    exit_code = main(
        [
            "probe",
            "--config",
            str(registry_path),
            "--target",
            "offline-mock-smoke",
            "--profile",
            "smoke",
        ]
    )

    assert exit_code == 2
    assert "numeric loopback" in capsys.readouterr().err


def test_probe_succeeds_against_numeric_loopback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _prepare_workspace(tmp_path)
    with MockProvider() as mock:
        registry_path = _registry_with(tmp_path, mock.base_url)
        monkeypatch.chdir(tmp_path)

        exit_code = main(
            [
                "probe",
                "--config",
                str(registry_path),
                "--target",
                "offline-mock-smoke",
                "--profile",
                "smoke",
                "--requests",
                "2",
            ]
        )

    assert exit_code == 0
