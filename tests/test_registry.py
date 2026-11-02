from __future__ import annotations

import json
from pathlib import Path

import pytest

from endpoint_bench.registry import RegistryError, load_registry


def test_loads_example_registry() -> None:
    registry = load_registry(Path("configs/registry.example.json"))

    assert registry.schema_version == "1.0"
    assert list(registry.deployments) == ["offline-mock-smoke"]
    deployment = registry.deployments["offline-mock-smoke"]
    assert deployment.protocol == "openai-chat-completions"
    assert deployment.context["context_state"] == "unverified"
    assert deployment.pricing["verified"] is False


@pytest.mark.parametrize(
    "field", ["name", "base_url", "protocol", "region", "credential_env_name"]
)
def test_missing_provider_fields_fail(tmp_path: Path, field: str) -> None:
    document = json.loads(Path("configs/registry.example.json").read_text())
    del document["providers"][0][field]
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(document))

    with pytest.raises(RegistryError, match=field):
        load_registry(path)


def test_duplicate_deployments_fail(tmp_path: Path) -> None:
    document = json.loads(Path("configs/registry.example.json").read_text())
    document["providers"][0]["deployments"].append(
        document["providers"][0]["deployments"][0]
    )
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(document))

    with pytest.raises(RegistryError, match="duplicate deployment_key"):
        load_registry(path)


def test_capability_states_are_validated(tmp_path: Path) -> None:
    document = json.loads(Path("configs/registry.example.json").read_text())
    document["providers"][0]["deployments"][0]["capabilities"]["tools"] = "probably_yes"
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(document))

    with pytest.raises(RegistryError, match="expected one of"):
        load_registry(path)


def test_invalid_output_limit_is_rejected(tmp_path: Path) -> None:
    document = json.loads(Path("configs/registry.example.json").read_text())
    document["providers"][0]["deployments"][0]["output_limit_tokens"] = 0
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(document))

    with pytest.raises(RegistryError, match="output_limit_tokens"):
        load_registry(path)


def test_extended_fields_are_carried_through() -> None:
    registry = load_registry(Path("configs/registry.example.json"))
    deployment = registry.deployments["offline-mock-smoke"]

    assert deployment.capabilities["tools"] == "unverified"
    assert deployment.output_limit_tokens is None
    assert deployment.tokenizer is None
    assert deployment.cache["reports_cached_tokens"] is None
    assert deployment.last_verified_at is None
