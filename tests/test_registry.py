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
