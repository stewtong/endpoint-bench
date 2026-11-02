from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class RegistryError(ValueError):
    """Raised when a provider registry or deployment configuration is invalid."""


#: Allowed confidence labels for declared capabilities and limits.
CAPABILITY_STATES = ("unknown", "unverified", "confirmed", "unsupported", "not_applicable")


@dataclass(frozen=True)
class Deployment:
    deployment_key: str
    provider_name: str
    base_url: str
    protocol: str
    region: str
    credential_env_name: str
    model_id: str
    model_revision: str | None
    context: dict[str, Any]
    pricing: dict[str, Any]
    capabilities: dict[str, str] = field(default_factory=dict)
    output_limit_tokens: int | None = None
    tokenizer: str | None = None
    cache: dict[str, Any] = field(default_factory=dict)
    last_verified_at: str | None = None


@dataclass(frozen=True)
class Registry:
    schema_version: str
    source_path: Path
    source_sha256: str
    deployments: dict[str, Deployment]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require(mapping: dict[str, Any], key: str, context: str) -> Any:
    value = mapping.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise RegistryError(f"{context}: missing or empty `{key}`")
    return value


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RegistryError(f"registry file does not exist: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RegistryError(f"could not read registry `{path}`: {error}") from error
    if not isinstance(value, dict):
        raise RegistryError("registry root must be a JSON object")
    return value


def load_registry(path: Path | str) -> Registry:
    source_path = Path(path)
    document = _load_json(source_path)
    schema_version = _require(document, "schema_version", "registry")
    providers = document.get("providers")
    if not isinstance(providers, list) or not providers:
        raise RegistryError("registry: `providers` must be a non-empty array")

    deployments: dict[str, Deployment] = {}
    for provider_index, provider in enumerate(providers):
        provider_context = f"providers[{provider_index}]"
        if not isinstance(provider, dict):
            raise RegistryError(f"{provider_context}: must be an object")
        provider_name = _require(provider, "name", provider_context)
        base_url = _require(provider, "base_url", provider_context)
        protocol = _require(provider, "protocol", provider_context)
        region = _require(provider, "region", provider_context)
        credential_env_name = _require(
            provider, "credential_env_name", provider_context
        )
        credential_env_name = str(credential_env_name)
        if not credential_env_name.replace("_", "").isalnum():
            raise RegistryError(
                f"{provider_context}: `credential_env_name` must be a valid environment name"
            )

        provider_deployments = provider.get("deployments")
        if not isinstance(provider_deployments, list) or not provider_deployments:
            raise RegistryError(f"{provider_context}: `deployments` must be a non-empty array")
        for deployment_index, deployment in enumerate(provider_deployments):
            context = f"{provider_context}.deployments[{deployment_index}]"
            if not isinstance(deployment, dict):
                raise RegistryError(f"{context}: must be an object")
            deployment_key = str(_require(deployment, "deployment_key", context))
            if deployment_key in deployments:
                raise RegistryError(f"{context}: duplicate deployment_key `{deployment_key}`")
            context_map = deployment.get("context")
            pricing = deployment.get("pricing")
            if not isinstance(context_map, dict):
                raise RegistryError(f"{context}: `context` must be an object")
            if not isinstance(pricing, dict):
                raise RegistryError(f"{context}: `pricing` must be an object")
            model_revision = deployment.get("model_revision")
            if model_revision is not None and not isinstance(model_revision, str):
                raise RegistryError(f"{context}: `model_revision` must be a string or null")

            capabilities = deployment.get("capabilities", {})
            if not isinstance(capabilities, dict):
                raise RegistryError(f"{context}: `capabilities` must be an object")
            for capability, state in capabilities.items():
                if state not in CAPABILITY_STATES:
                    raise RegistryError(
                        f"{context}: capability `{capability}` has state `{state}`; "
                        f"expected one of {', '.join(CAPABILITY_STATES)}"
                    )

            output_limit = deployment.get("output_limit_tokens")
            if output_limit is not None and (
                isinstance(output_limit, bool) or not isinstance(output_limit, int)
                or output_limit <= 0
            ):
                raise RegistryError(
                    f"{context}: `output_limit_tokens` must be a positive integer or null"
                )

            tokenizer = deployment.get("tokenizer")
            if tokenizer is not None and not isinstance(tokenizer, str):
                raise RegistryError(f"{context}: `tokenizer` must be a string or null")

            cache = deployment.get("cache", {})
            if not isinstance(cache, dict):
                raise RegistryError(f"{context}: `cache` must be an object")

            last_verified_at = deployment.get("last_verified_at")
            if last_verified_at is not None and not isinstance(last_verified_at, str):
                raise RegistryError(f"{context}: `last_verified_at` must be a string or null")

            deployments[deployment_key] = Deployment(
                deployment_key=deployment_key,
                provider_name=str(provider_name),
                base_url=str(base_url),
                protocol=str(protocol),
                region=str(region),
                credential_env_name=credential_env_name,
                model_id=str(_require(deployment, "model_id", context)),
                model_revision=model_revision,
                context=context_map,
                pricing=pricing,
                capabilities=dict(capabilities),
                output_limit_tokens=output_limit,
                tokenizer=tokenizer,
                cache=dict(cache),
                last_verified_at=last_verified_at,
            )

    return Registry(
        schema_version=str(schema_version),
        source_path=source_path,
        source_sha256=sha256_file(source_path),
        deployments=deployments,
    )
