"""Reconstruct a deduplicated historical run inventory from original artifacts.

The repository contains run directories plus documentation copies of selected
runs. Counting directories would double-count the same inference attempts, so
this script groups by the SHA-256 of `trials.jsonl` (and records report/config
hashes too), picks one canonical source, and marks every entry retrospective.

It performs no inference and reads no credentials. Authority is not inferred
from the existence of an artifact: every historical entry is labelled
`unresolved_retrospective` unless a manager-owned authorization record names it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


class InventoryError(ValueError):
    pass


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _config_concurrency(path: Path) -> int | None:
    if not path.is_file():
        return None
    lines = path.read_text(encoding="utf-8").splitlines()
    in_phases = False
    for line in lines:
        if not in_phases:
            if line.strip() == "phases:":
                in_phases = True
            continue
        if line and not line.startswith(" "):
            break
        match = re.fullmatch(r"\s+concurrency:\s*(\d+)\s*", line)
        if match:
            return int(match.group(1))
    return None


def _time_bounds(trials: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    starts: list[str] = []
    ends: list[str] = []
    for trial in trials:
        for attempt in trial.get("attempts") or []:
            if isinstance(attempt, dict):
                if isinstance(attempt.get("started_at"), str):
                    starts.append(attempt["started_at"])
                if isinstance(attempt.get("ended_at"), str):
                    ends.append(attempt["ended_at"])
    def parse(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (
        min(starts, key=parse) if starts else None,
        max(ends, key=parse) if ends else None,
    )


def _finish_counts(trials: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for trial in trials:
        completion = trial.get("completion") or {}
        key = str(completion.get("finish_reason") or "<absent>")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


@dataclass
class RunArtifact:
    path: Path
    kind: str
    trials_path: Path | None = None
    report_path: Path | None = None
    manifest_path: Path | None = None
    config_path: Path | None = None
    trials_sha256: str | None = None
    report_sha256: str | None = None
    config_sha256: str | None = None
    records: int = 0
    report: dict[str, Any] = field(default_factory=dict)
    manifest: dict[str, Any] = field(default_factory=dict)

    def identity(self) -> dict[str, Any]:
        report = self.report
        provenance = report.get("provenance") or {}
        deployment = provenance.get("deployment") or {}
        profile = provenance.get("profile") or {}
        counters = report.get("counters") or self.manifest.get("counters") or {}
        trials = _load_trials(self.trials_path) if self.trials_path else []
        start, end = _time_bounds(trials)
        return {
            "run_id": report.get("run_id") or self.path.name,
            "kind": self.kind,
            "canonical_path": str(self.path),
            "trials_sha256": self.trials_sha256,
            "report_sha256": self.report_sha256,
            "config_sha256": self.config_sha256,
            "records": self.records,
            "deployment_key": report.get("deployment_key") or deployment.get("deployment_key"),
            "model_id": deployment.get("model_id"),
            "protocol": deployment.get("protocol"),
            "region": deployment.get("region"),
            "profile_name": report.get("profile_name") or profile.get("name"),
            "profile_sha256": profile.get("sha256"),
            "workload_type": provenance.get("workload_type"),
            "concurrency": _config_concurrency(self.config_path) if self.config_path else None,
            "request_timeout_s": (provenance.get("timeouts") or {}).get("request_timeout_s"),
            "process_deadline_s": (provenance.get("timeouts") or {}).get("process_deadline_s"),
            "first_attempt_metadata_utc": start,
            "last_attempt_metadata_utc": end,
            "reported_duration_ms": (report.get("run") or {}).get("duration_ms"),
            "timestamp_note": (
                "Attempt timestamps are the harness's per-attempt metadata interval, "
                "not a run wall-clock anchor. The corrected chronology for the 12 "
                "bounded cells is documented in reviews/0006-bounded-review.md."
            ),
            "counters": counters,
            "http_attempts": counters.get("http_attempts"),
            "finish_reason_counts": _finish_counts(trials),
            "retrospective": True,
            "authority": "unresolved_retrospective",
            "authority_note": (
                "Reconstructed from repository artifacts. No manager-owned ledger entry "
                "or current-session authorization record names this run; existence of "
                "evidence is not authority."
            ),
            "new_inference_in_d0003": False,
        }


def _load_trials(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.is_file():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                records.append(value)
    return records


def discover(root: Path) -> list[RunArtifact]:
    candidates: list[Path] = []
    runs = root / "runs"
    if runs.is_dir():
        candidates.extend(path for path in runs.iterdir() if path.is_dir())
    docs = root / "docs"
    if docs.is_dir():
        for path in docs.rglob("*"):
            if path.is_dir() and (
                (path / "trials.jsonl").is_file() or (path / "report.json").is_file()
            ):
                candidates.append(path)
    artifacts: list[RunArtifact] = []
    for path in sorted(set(candidates)):
        trials_path = path / "trials.jsonl"
        report_path = path / "report.json"
        manifest_path = path / "manifest.json"
        config_path = path / "aiperf-config.yaml"
        if not (trials_path.is_file() or report_path.is_file() or manifest_path.is_file()):
            continue
        trials = _load_trials(trials_path) if trials_path.is_file() else []
        report = _load_json(report_path) or {}
        manifest = _load_json(manifest_path) or {}
        artifact = RunArtifact(
            path=path,
            kind=(
                "benchmark"
                if trials_path.is_file() and report
                else "probe"
                if manifest.get("manifest_type") == "probe"
                else "derived_correction"
                if report
                else "unknown"
            ),
            trials_path=trials_path if trials_path.is_file() else None,
            report_path=report_path if report_path.is_file() else None,
            manifest_path=manifest_path if manifest_path.is_file() else None,
            config_path=config_path if config_path.is_file() else None,
            trials_sha256=_sha256(trials_path) if trials_path.is_file() else None,
            report_sha256=_sha256(report_path) if report_path.is_file() else None,
            config_sha256=_sha256(config_path) if config_path.is_file() else None,
            records=len(trials),
            report=report,
            manifest=manifest,
        )
        artifacts.append(artifact)
    return artifacts


def build(root: Path) -> dict[str, Any]:
    artifacts = discover(root)
    derived = [
        {
            "run_id": artifact.report.get("run_id") or artifact.path.name,
            "path": str(artifact.path),
            "report_sha256": artifact.report_sha256,
            "retrospective": True,
            "authority": "unresolved_retrospective",
            "note": (
                "Derived correction/analysis report without original trial records in "
                "this directory. It is not a new run and contributes no attempts."
            ),
        }
        for artifact in artifacts
        if artifact.kind == "derived_correction"
    ]
    groups: dict[tuple[str, str], list[RunArtifact]] = {}
    for artifact in artifacts:
        if artifact.kind == "derived_correction":
            continue
        # A benchmark is identified by its normalized trial bytes; a probe by
        # its manifest bytes. Missing hashes never collapse distinct
        # directories into one identity.
        identity_hash = artifact.trials_sha256 or artifact.report_sha256
        if identity_hash is None and artifact.manifest_path is not None:
            identity_hash = _sha256(artifact.manifest_path)
        key = (artifact.kind, identity_hash or str(artifact.path))
        groups.setdefault(key, []).append(artifact)
    unique: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    for key, members in sorted(groups.items(), key=lambda item: item[0]):
        canonical = sorted(
            members,
            key=lambda artifact: (
                0 if str(artifact.path).startswith(str(root / "runs")) else 1,
                str(artifact.path),
            ),
        )[0]
        identity = canonical.identity()
        identity["duplicate_copies"] = [
            {
                "path": str(member.path),
                "trials_sha256": member.trials_sha256,
                "report_sha256": member.report_sha256,
            }
            for member in members
            if member is not canonical
        ]
        unique.append(identity)
        if len(members) > 1:
            duplicates.append(
                {
                    "identity": key,
                    "canonical": str(canonical.path),
                    "copies": [str(member.path) for member in members if member is not canonical],
                }
            )
    total_attempts = sum(int(item.get("http_attempts") or 0) for item in unique)
    benchmark = [item for item in unique if item["kind"] == "benchmark"]
    probes = [item for item in unique if item["kind"] == "probe"]
    return {
        "schema_version": "historical-run-inventory/1.0.0",
        "generated_at_utc": datetime.now().astimezone().isoformat(),
        "source_root": str(root),
        "method": (
            "Directories are grouped by the SHA-256 of trials.jsonl (benchmark) or "
            "manifest identity (probe). One canonical source is chosen from runs/ when "
            "available; documentation copies are retained as duplicates. All entries "
            "are retrospective and authority-unresolved."
        ),
        "counts": {
            "directories_scanned": len(artifacts),
            "unique_run_identities": len(unique),
            "benchmark_identities": len(benchmark),
            "probe_identities": len(probes),
            "duplicate_groups": len(duplicates),
            "duplicate_copies": sum(len(group["copies"]) for group in duplicates),
            "derived_correction_reports": len(derived),
            "unique_http_attempts": total_attempts,
        },
        "runs": unique,
        "derived_corrections": derived,
        "duplicate_groups": duplicates,
        "authority": {
            "state": "unresolved_retrospective",
            "note": (
                "Review 0006 found the 12 bounded 8192 cells do not match the "
                "coordinating chat's 2048/$0.20 bounded approval. Existing artifacts "
                "are preserved and counted, but no current authorization is inferred "
                "from them. D0003 releases no live operation."
            ),
        },
        "exclusions": [
            "No run is relabelled as a D0003 window.",
            "Duplicate documentation copies are not additional trials.",
            "Historical pilot cells are not reused in session-dynamics-v1.",
        ],
    }


def render_markdown(document: dict[str, Any]) -> str:
    lines = [
        "# Historical run inventory",
        "",
        "**Version:** 1.0  ",
        "**Datestamp:** 2026-11-02  ",
        "**Source:** `runs/`, `docs/gate3-*`, `docs/gate3-corrections*` artifacts; "
        "rebuilt by `scripts/build_historical_inventory.py`.  ",
        "**Update rule:** Rebuild from original artifacts; never edit a recorded counter "
        "or promote a retrospective entry to current authority.",
        "",
        f"Generated: {document['generated_at_utc']}",
        "",
        "Retrospective reconstruction from repository artifacts. **No entry below is a "
        "new D0003 operation, and no entry carries current live authorization.**",
        "",
        "## Dedupe summary",
        "",
        f"- Directories scanned: {document['counts']['directories_scanned']}",
        f"- Unique run identities: {document['counts']['unique_run_identities']} "
        f"({document['counts']['benchmark_identities']} benchmark, "
        f"{document['counts']['probe_identities']} probe)",
        f"- Duplicate copies: {document['counts']['duplicate_copies']}",
        f"- Unique HTTP attempts: {document['counts']['unique_http_attempts']}",
        f"- Derived correction reports without original trials: "
        f"{document['counts']['derived_correction_reports']}",
        "",
        "## Runs",
        "",
        "| Run | Kind | Profile | C | Attempts | Natural | Truncated | Timeout (s) | First attempt metadata (UTC) | Duration (s) | Authority |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | ---: | --- |",
    ]
    for run in sorted(
        document["runs"], key=lambda item: str(item["first_attempt_metadata_utc"] or "")
    ):
        counters = run.get("counters") or {}
        duration = run.get("reported_duration_ms")
        lines.append(
            "| {run_id} | {kind} | {profile} | {c} | {attempts} | {natural} | {truncated} | "
            "{timeout} | {start} | {duration} | {authority} |".format(
                run_id=run["run_id"],
                kind=run["kind"],
                profile=run.get("profile_name") or "-",
                c=run.get("concurrency") if run.get("concurrency") is not None else "-",
                attempts=run.get("http_attempts") if run.get("http_attempts") is not None else "-",
                natural=counters.get("successful_trials", "-"),
                truncated=counters.get("truncated_trials", "-"),
                timeout=run.get("request_timeout_s") if run.get("request_timeout_s") is not None else "-",
                start=run.get("first_attempt_metadata_utc") or "-",
                duration=(
                    f"{float(duration) / 1000.0:.1f}" if duration is not None else "-"
                ),
                authority=run["authority"],
            )
        )
    lines.extend(
        [
            "",
            "## Duplicate groups",
            "",
            "Documentation copies are the same inference attempts, not new evidence.",
            "",
        ]
    )
    for group in document["duplicate_groups"]:
        lines.append(
            f"- canonical `{group['canonical']}`; copies: "
            + ", ".join(f"`{copy}`" for copy in group["copies"])
        )
    lines.extend(
        [
            "",
            "## Interpretation corrections",
            "",
            "- The bounded plan declares a 240 s request timeout, but every historical "
            "report and generated AIPerf configuration observed here records 60 s. The "
            "plan value is a declared intent; the runtime value is the evidence.",
            "- Window 1 C1 (07:41–07:42 UTC) is the reused first-window chronology; the "
            "C2 pair began 07:57 UTC. Later windows use ten-minute offsets from that "
            "C2 start. This is exploratory reused evidence, not six uniformly paired "
            "fresh windows under a prospectively frozen plan.",
            "- The 8192 fixed cap did not eliminate censoring. It is not evidence that "
            "no higher cap could ever do so; no universal ceiling-tail claim is supported.",
            "",
            document["authority"]["note"],
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--out", type=Path, default=None)
    arguments = parser.parse_args(argv)
    document = build(arguments.root)
    out = arguments.out or (arguments.root / "docs" / "HISTORICAL_RUN_INVENTORY.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown = arguments.root / "docs" / "HISTORICAL_RUN_INVENTORY.md"
    markdown.write_text(render_markdown(document), encoding="utf-8")
    print(
        json.dumps(
            {
                "out": str(out),
                "markdown": str(markdown),
                "unique_runs": document["counts"]["unique_run_identities"],
                "unique_http_attempts": document["counts"]["unique_http_attempts"],
                "duplicate_groups": document["counts"]["duplicate_groups"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
