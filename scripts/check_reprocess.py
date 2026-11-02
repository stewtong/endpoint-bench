"""End-to-end check of the reprocess path without any network access.

Builds a completed run in the shape version 0.1.0 wrote, reprocesses it, and
asserts three things:

1. the command succeeds and writes a correction;
2. the legacy success becomes a ceiling truncation once `finish_reason` is read;
3. the source run directory is byte-identical afterwards.

The third assertion is the point. Reprocessing exists so published evidence can be
corrected without being overwritten, so a test that only checked the output would
miss the property that matters.

    uv run python scripts/check_reprocess.py
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

SCRATCH = Path("/tmp/verify-reprocess")
MAX_COMPLETION_TOKENS = 256
PROMPT_TOKENS = 1086


class CheckError(AssertionError):
    """The reprocess path did not behave as specified."""


def _build_legacy_run(run: Path) -> None:
    artifact = run / "aiperf"
    artifact.mkdir(parents=True, exist_ok=True)

    final = {
        "choices": [{"index": 0, "delta": {}, "finish_reason": "length"}],
        "usage": {
            "prompt_tokens": PROMPT_TOKENS,
            "completion_tokens": MAX_COMPLETION_TOKENS,
            "prompt_tokens_details": {"cached_tokens": 0, "created_cache_tokens": 1024},
            "completion_tokens_details": {"reasoning_tokens": MAX_COMPLETION_TOKENS},
        },
    }
    raw = {
        "metadata": {"x_request_id": "req-0"},
        "payload": {
            "messages": [{"role": "user", "content": "x"}],
            "max_completion_tokens": MAX_COMPLETION_TOKENS,
        },
        "status": 200,
        "responses": [
            {"perf_ns": 1, "packets": [{"name": "data", "value": json.dumps(final)}]}
        ],
    }
    (artifact / "profile_export_raw.jsonl").write_text(json.dumps(raw) + "\n")
    (artifact / "profile_export.jsonl").write_text(
        json.dumps(
            {
                "metadata": {
                    "x_request_id": "req-0",
                    "was_cancelled": False,
                    "context_overflow_skip": False,
                },
                "metrics": {
                    "input_sequence_length": {"value": PROMPT_TOKENS},
                    "usage_prompt_tokens": {"value": PROMPT_TOKENS},
                    "output_sequence_length": {"value": MAX_COMPLETION_TOKENS},
                    "usage_completion_tokens": {"value": MAX_COMPLETION_TOKENS},
                    "time_to_first_token": {"value": 500.0, "unit": "ms"},
                    "request_latency": {"value": 1100.0, "unit": "ms"},
                },
            }
        )
        + "\n"
    )
    (artifact / "profile_export_aiperf.json").write_text(
        json.dumps({"schema_version": "1.4", "aiperf_version": "0.13.0"})
    )
    # Legacy normalized record: a completed stream scored as a plain success, with
    # the circular sizing verdict that version 0.1.0 produced.
    (run / "trials.jsonl").write_text(
        json.dumps(
            {
                "schema_version": "0.1.0",
                "trial_id": "aiperf-0000",
                "outcome": "success",
                "outcome_detail": "completed",
                "attribution": "unknown",
                "finish_reason": None,
                "attempts": [{"attempt_index": 1, "http_status": 200}],
                "token_accounting": {
                    "target_input_tokens": 1024,
                    "client_estimated_input_tokens": PROMPT_TOKENS,
                    "provider_input_tokens": PROMPT_TOKENS,
                    "discrepancy_pct": 0.0,
                    "tolerance_pct": 10.0,
                    "sizing_verified": True,
                },
            }
        )
        + "\n"
    )
    (run / "report.json").write_text(
        json.dumps(
            {
                "schema_version": "0.1.0",
                "run_id": "verify-reprocess",
                "deployment_key": "local-mock",
                "profile_name": "verify",
                "client_profile": {
                    "name": "baseline-normal-client-server-accounting",
                    "session_affinity": False,
                    "use_server_token_count": True,
                    "token_accounting_source": "provider_usage",
                },
                "counters": {"successful_trials": 1, "failed_trials": 0},
            }
        )
    )


def main() -> int:
    shutil.rmtree(SCRATCH, ignore_errors=True)
    run = SCRATCH / "source"
    out = SCRATCH / "corrected"
    try:
        _build_legacy_run(run)
        before = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (run / "report.json", run / "trials.jsonl")
        }

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "endpoint_bench.cli",
                "reprocess",
                "--run",
                str(run),
                "--out",
                str(out),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise CheckError(
                f"reprocess exited {result.returncode}\n{result.stdout}\n{result.stderr}"
            )

        after = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (run / "report.json", run / "trials.jsonl")
        }
        if before != after:
            raise CheckError("reprocess modified the source run directory")

        correction = json.loads((out / "correction.json").read_text())
        counters = correction["counters_after"]
        if counters["successful_trials"] != 0:
            raise CheckError(
                f"a ceiling-truncated trial was still counted successful: {counters}"
            )
        if counters["truncated_trials"] != 1:
            raise CheckError(f"truncation was not recorded: {counters}")
        if correction["completion"]["finish_reason_counts"].get("length") != 1:
            raise CheckError(
                f"finish_reason was not recovered: {correction['completion']}"
            )
        if correction["provider_telemetry"]["metrics"]["cache_write_tokens"]["sum"] != 1024:
            raise CheckError(
                "cache-write was not recovered from the raw usage: "
                f"{correction['provider_telemetry']['metrics']['cache_write_tokens']}"
            )
        if not any(
            "client_estimated_input_tokens" in gap["field"]
            for gap in correction["unrecoverable"]
        ):
            raise CheckError("the absent client estimate was not listed as unrecoverable")

        report = json.loads((out / "report.json").read_text())
        if report["schema_version"] != "0.2.0":
            raise CheckError(f"corrected report has schema {report['schema_version']}")

    except (CheckError, KeyError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print("reprocess: 1 legacy success corrected to 1 truncation, source unchanged")
    print("reprocess: cache-write recovered from raw usage, absent client estimate listed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
