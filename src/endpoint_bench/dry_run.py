from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from . import RESULT_SCHEMA_VERSION, __version__
from .profiles import Profile
from .registry import Deployment, Registry


def build_dry_run_manifest(registry: Registry, deployment: Deployment, profile: Profile) -> dict:
    if deployment.deployment_key not in registry.deployments:
        raise KeyError(deployment.deployment_key)

    config_material = "\n".join(
        [registry.source_sha256, profile.source_sha256, deployment.deployment_key]
    ).encode("utf-8")
    run_id = "dryrun-" + hashlib.sha256(config_material).hexdigest()[:12]
    max_requests = profile.request_count
    max_tokens = max_requests * (profile.input_tokens + profile.max_output_tokens)
    generated_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    pricing = deployment.pricing
    pricing_verified = pricing.get("verified") is True

    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "manifest_type": "dry_run",
        "run_id": run_id,
        "generated_at": generated_at,
        "software": {"name": "endpoint-bench", "version": __version__},
        "inference": {
            "executed": False,
            "network_requests": 0,
            "credentials_sent": False,
            "reason": "dry_run_only",
        },
        "deployment": {
            "deployment_key": deployment.deployment_key,
            "provider_name": deployment.provider_name,
            "base_url": deployment.base_url,
            "protocol": deployment.protocol,
            "region": deployment.region,
            "credential_env_name": deployment.credential_env_name,
            "model_id": deployment.model_id,
            "model_revision": deployment.model_revision,
            "context": deployment.context,
            "pricing_verified": pricing_verified,
        },
        "profile": {
            "schema_version": profile.schema_version,
            "name": profile.name,
            "description": profile.description,
            "request_count": profile.request_count,
            "input_tokens": profile.input_tokens,
            "max_output_tokens": profile.max_output_tokens,
            "concurrency": profile.concurrency,
            "streaming": profile.streaming,
            "source_sha256": profile.source_sha256,
        },
        "planned_exposure": {
            "max_requests": max_requests,
            "max_input_tokens": max_requests * profile.input_tokens,
            "max_generated_tokens": max_requests * profile.max_output_tokens,
            "max_total_tokens": max_tokens,
            "max_concurrency": profile.concurrency,
            "estimated_cost": None,
            "estimated_cost_reason": (
                "not_estimated"
                if not pricing_verified
                else "cost_estimation_not_implemented"
            ),
        },
        "source_hashes": {
            "registry_sha256": registry.source_sha256,
            "profile_sha256": profile.source_sha256,
        },
        "limitations": [
            "This manifest is a dry-run and is not benchmark evidence.",
            "No endpoint request was sent and no token was generated.",
        ],
    }
