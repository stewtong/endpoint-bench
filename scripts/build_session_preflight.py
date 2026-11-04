"""Build the session-dynamics-v1 plan and 54-cell preflight manifest.

This is the target-bound preflight used for manager review. It reads a target
metadata JSON (no credential values), freezes the plan, and writes `plan.json`
and `manifest.json`. It sends no request and requires no credential environment
variable.

    python scripts/build_session_preflight.py \
      --target configs/session-programs/session-dynamics-v1.target.json \
      --out docs/session-dynamics-v1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from endpoint_bench.session_program import (
    build_manifest,
    build_plan,
    load_program,
    verify_prepared_manifest,
    write_json_atomic,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROGRAM = ROOT / "configs/session-programs/session-dynamics-v1.json"


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _target(path: Path) -> tuple[dict, dict]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise SystemExit("target metadata must be a JSON object")
    required = ("deployment_key", "model_id", "base_url", "protocol", "region", "credential_env_name")
    missing = [key for key in required if not document.get(key)]
    if missing:
        raise SystemExit(f"target metadata missing: {', '.join(missing)}")
    pricing = document.get("pricing") or {}
    prompt = pricing.get("prompt_per_million_tokens")
    completion = pricing.get("completion_per_million_tokens")
    if not isinstance(prompt, (int, float)) or not isinstance(completion, (int, float)):
        raise SystemExit("target pricing needs numeric prompt/completion rates")
    rates = {
        "prompt_per_million_tokens": float(prompt),
        "completion_per_million_tokens": float(completion),
        "safety_multiplier": 1.5,
        "source": str(pricing.get("source") or "target metadata"),
        "effective_date": pricing.get("effective_date"),
        "pricing_verified": pricing.get("verified") is True,
    }
    target = {
        "deployment_key": document["deployment_key"],
        "model_id": document["model_id"],
        "base_url": document["base_url"],
        "protocol": document["protocol"],
        "region": document["region"],
        "credential_env_name": document["credential_env_name"],
        "model_revision": document.get("model_revision"),
        "pricing_verified": document["pricing"]["verified"] is True,
        "bound": True,
        "binding_source": document.get("binding_source"),
        "target_metadata_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "target_metadata_path": str(path),
        "note": (
            "Target-bound preflight only. Live release remains manager-controlled; "
            "no credential value is stored in this manifest."
        ),
    }
    return target, rates


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--program", type=Path, default=DEFAULT_PROGRAM)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--release-nonce", default="d0004-r4-preflight")
    arguments = parser.parse_args(argv)

    program = load_program(arguments.program)
    target, rates = _target(arguments.target)
    out = arguments.out
    out.mkdir(parents=True, exist_ok=True)
    plan = build_plan(program, output_dir=out, release_nonce=arguments.release_nonce)
    manifest = build_manifest(
        plan,
        manifest_path=out / "manifest.json",
        plan_path=Path("plan.json"),
        target=target,
        operational_rates=rates,
    )
    verify_prepared_manifest(
        manifest, plan, require_unreleased=True, check_sources=True
    )
    write_json_atomic(out / "plan.json", plan)
    write_json_atomic(out / "manifest.json", manifest)

    # Read back the exact bytes that were written and label each digest by what
    # it covers: canonical content digests are not file SHA-256 values.
    plan_file = out / "plan.json"
    manifest_file = out / "manifest.json"
    readback_plan = json.loads(plan_file.read_text(encoding="utf-8"))
    readback_manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    if readback_plan != plan or readback_manifest != manifest:
        raise SystemExit("written preflight artifacts do not match the generated documents")
    print(
        json.dumps(
            {
                "program_id": manifest["program_id"],
                "target": manifest["target"]["deployment_key"],
                "cells": manifest["totals"]["cells"],
                "max_inference_attempts": manifest["totals"]["max_inference_attempts"],
                "operational_usd_envelope": manifest["totals"]["operational_usd_envelope"],
                "program_file_sha256": program.source_sha256,
                "plan_content_sha256": plan["plan_sha256"],
                "plan_file_sha256": _file_sha256(plan_file),
                "manifest_content_sha256": manifest["manifest_sha256"],
                "manifest_file_sha256": _file_sha256(manifest_file),
                "manifest_execution_identity_sha256": (
                    manifest["execution_identity_sha256"]
                ),
                "live_released_operation_ids": manifest["release"][
                    "live_released_operation_ids"
                ],
                "plan_path": str(plan_file),
                "manifest_path": str(manifest_file),
                "inference": {"executed": False, "network_requests": 0},
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
