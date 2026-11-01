from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .dry_run import build_dry_run_manifest
from .profiles import ProfileError, load_profile
from .registry import RegistryError, load_registry


def _json_print(value: object) -> None:
    json.dump(value, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="endpoint-bench",
        description=(
            "Validate provider configuration and create offline dry-run manifests. "
            "Live inference is not implemented."
        ),
    )
    parser.add_argument("--version", action="version", version="endpoint-bench 0.0.1")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("registry", help="registry operations")
    validate_subparsers = validate.add_subparsers(dest="registry_command", required=True)
    validate_registry = validate_subparsers.add_parser(
        "validate", help="validate a provider registry"
    )
    validate_registry.add_argument(
        "--config", type=Path, default=Path("configs/registry.example.json")
    )

    run = subparsers.add_parser("run", help="resolve a run and produce a dry-run manifest")
    run.add_argument("--config", type=Path, default=Path("configs/registry.example.json"))
    run.add_argument(
        "--profile-file", type=Path, default=Path("configs/profiles/smoke.json")
    )
    run.add_argument("--target", required=True)
    run.add_argument("--profile", help="expected profile name")
    modes = run.add_mutually_exclusive_group(required=True)
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--execute", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "registry":
            registry = load_registry(arguments.config)
            _json_print(
                {
                    "status": "valid",
                    "schema_version": registry.schema_version,
                    "source_path": str(registry.source_path),
                    "source_sha256": registry.source_sha256,
                    "deployment_count": len(registry.deployments),
                }
            )
            return 0

        if arguments.execute:
            parser.error("live execution is not implemented; use --dry-run")

        registry = load_registry(arguments.config)
        if arguments.target not in registry.deployments:
            available = ", ".join(sorted(registry.deployments)) or "none"
            raise RegistryError(
                f"unknown target `{arguments.target}`; available targets: {available}"
            )
        profile = load_profile(arguments.profile_file, arguments.profile)
        deployment = registry.deployments[arguments.target]
        manifest = build_dry_run_manifest(registry, deployment, profile)
        _json_print(manifest)
        return 0
    except (RegistryError, ProfileError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
