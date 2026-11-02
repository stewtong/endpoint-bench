from __future__ import annotations

from pathlib import Path

import pytest

from endpoint_bench.dry_run import build_dry_run_manifest
from endpoint_bench.profiles import load_profile
from endpoint_bench.registry import load_registry


def test_dry_run_manifest_is_network_free() -> None:
    registry = load_registry(Path("configs/registry.example.json"))
    profile = load_profile(Path("configs/profiles/smoke.json"), "smoke")
    deployment = registry.deployments["offline-mock-smoke"]

    manifest = build_dry_run_manifest(registry, deployment, profile)

    assert manifest["manifest_type"] == "dry_run"
    assert manifest["inference"] == {
        "executed": False,
        "network_requests": 0,
        "credentials_sent": False,
        "reason": "dry_run_only",
    }
    assert manifest["planned_exposure"]["max_requests"] == 2
    assert manifest["planned_exposure"]["max_total_tokens"] == 320
    assert manifest["planned_exposure"]["estimated_cost"] is None
    assert manifest["source_hashes"] == {
        "registry_sha256": registry.source_sha256,
        "profile_sha256": profile.source_sha256,
    }
    assert "not benchmark evidence" in " ".join(manifest["limitations"])


def test_unknown_pricing_remains_explicitly_unknown() -> None:
    registry = load_registry(Path("configs/registry.example.json"))
    profile = load_profile(Path("configs/profiles/smoke.json"))
    deployment = registry.deployments["offline-mock-smoke"]

    manifest = build_dry_run_manifest(registry, deployment, profile)

    assert deployment.pricing["verified"] is False
    assert manifest["deployment"]["pricing_verified"] is False
    assert manifest["planned_exposure"]["estimated_cost"] is None
    assert manifest["planned_exposure"]["estimated_cost_reason"] == (
        "not_estimated_pricing_unverified"
    )


def test_verified_pricing_produces_a_conservative_estimate(tmp_path: Path) -> None:
    import json

    document = json.loads(Path("configs/registry.example.json").read_text())
    document["providers"][0]["deployments"][0]["pricing"] = {
        "currency": "USD",
        "prompt_per_million_tokens": 1.0,
        "completion_per_million_tokens": 2.0,
        "verified": True,
        "source": "test fixture",
        "effective_date": "2026-11-01",
    }
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(document))

    registry = load_registry(path)
    profile = load_profile(Path("configs/profiles/smoke.json"), "smoke")
    deployment = registry.deployments["offline-mock-smoke"]

    manifest = build_dry_run_manifest(registry, deployment, profile)
    exposure = manifest["planned_exposure"]

    # 2 x 128 input at $1/M + 2 x 32 output at $2/M, times 1.5 safety.
    expected = (256 / 1_000_000 * 1.0 + 64 / 1_000_000 * 2.0) * 1.5
    assert exposure["estimated_cost"] == pytest.approx(expected)
    assert exposure["estimated_cost_reason"] == "conservative_upper_bound"
