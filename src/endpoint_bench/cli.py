from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import RESULT_SCHEMA_VERSION, __version__
from .aiperf import (
    BASELINE_CLIENT,
    BASELINE_SERVER_ACCOUNTING,
    AiperfError,
    build_benchmark_config,
    dump_yaml,
    parse_artifacts,
    run_aiperf,
    write_config,
)
from .cost import cost_preflight, estimate_cost_usd
from .dry_run import build_dry_run_manifest
from .fixtures import generate_fixture
from .network_guard import NetworkGuardError, assert_loopback, is_loopback
from .probes import DEFAULT_TOOL_PROMPT, ProbeConfig, probe, probe_tool_round_trip
from .profiles import Profile, ProfileError, load_profile
from .registry import Deployment, RegistryError, load_registry
from .report import (
    ReportError,
    build_provenance,
    build_report,
    compare_reports,
    load_report,
    write_report,
)
from .reprocess import ReprocessError, reprocess_run
from .session_engine import SessionCellRunner, SessionEngineError, SessionTarget
from .session_evidence import (
    JsonJournal,
    SessionEvidenceError,
    analyze_cells,
    render_markdown,
    write_analysis,
    write_cell_evidence,
)
from .session_program import (
    SAME_DAY_PROGRAM_ID,
    SessionProgramError,
    build_manifest,
    build_plan,
    load_program,
    load_target_metadata,
    manifest_content_sha256,
    manifest_execution_identity_sha256,
    plan_content_sha256,
    utc_now,
    validate_manifest_document,
    validate_plan_document,
    verify_manifest_against_plan,
    verify_prepared_manifest,
    write_json_atomic,
)
from .trials import (
    ACCOUNTING_CLIENT_TOKENIZER,
    ACCOUNTING_PROVIDER_USAGE,
    WORKLOAD_LIVE_TURN_CONFORMANCE,
    WORKLOAD_RECORDED_MULTI_TURN_REPLAY,
    WORKLOAD_SYNTHETIC_SINGLE_TURN,
    Trial,
    count_trials,
    token_sizing_summary,
    write_trials_jsonl,
)

DEFAULT_AIPERF = Path(".venv-aiperf/bin/aiperf")


def _json_print(value: object) -> None:
    json.dump(value, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="endpoint-bench",
        description=(
            "Validate provider configuration, run offline conformance probes, and "
            "orchestrate pinned AIPerf runs. Live paid inference is fail-closed."
        ),
    )
    parser.add_argument("--version", action="version", version=f"endpoint-bench {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    registry = subparsers.add_parser("registry", help="registry operations")
    registry_subparsers = registry.add_subparsers(dest="registry_command", required=True)
    validate_registry = registry_subparsers.add_parser("validate", help="validate a provider registry")
    validate_registry.add_argument("--config", type=Path, default=Path("configs/registry.example.json"))

    mock_parser = subparsers.add_parser(
        "mock", help="run the local OpenAI-compatible mock provider"
    )
    mock_parser.add_argument("--port", type=int, default=18080)
    mock_parser.add_argument(
        "--behavior",
        default="stream_success",
        help="deterministic response shape, e.g. stream_success or tool_call_stream",
    )
    mock_parser.add_argument("--model-id", default="mock-model")

    probe_parser = subparsers.add_parser("probe", help="run deterministic conformance probes")
    _add_target_options(probe_parser)
    probe_parser.add_argument("--requests", type=int, default=1)
    probe_parser.add_argument(
        "--scenario",
        choices=("streaming", "tool-round-trip"),
        default="streaming",
        help="conformance scenario to exercise",
    )
    probe_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="emit the plan and cost estimate without sending any request",
    )
    probe_parser.add_argument(
        "--authorize-live",
        action="store_true",
        help="permit a non-loopback target and credential transmission",
    )
    probe_parser.add_argument(
        "--run-dir", type=Path, default=Path("runs"), help="parent directory for run artifacts"
    )

    run_parser = subparsers.add_parser("run", help="resolve a run and produce evidence")
    _add_target_options(run_parser)
    run_parser.add_argument(
        "--aiperf",
        type=Path,
        default=Path(os.environ.get("ENDPOINT_BENCH_AIPERF", DEFAULT_AIPERF)),
        help="pinned AIPerf executable",
    )
    run_parser.add_argument("--max-requests", type=int, default=None)
    run_parser.add_argument("--max-usd", type=float, default=None)
    run_parser.add_argument("--process-timeout", type=float, default=900.0)
    run_parser.add_argument(
        "--authorize-live",
        action="store_true",
        help="explicit authorization for non-loopback inference traffic",
    )
    run_parser.add_argument("--run-dir", type=Path, default=Path("runs"))
    run_parser.add_argument(
        "--run-label",
        default=None,
        help=(
            "optional suffix that distinguishes repeated runs of the same "
            "configuration; the AIPerf config hash alone is identical for every "
            "window of a repeated experiment"
        ),
    )
    run_parser.add_argument(
        "--trace-file",
        type=Path,
        default=None,
        help="recorded trace for replay instead of synthetic prompts",
    )
    run_parser.add_argument("--trace-type", default="mooncake-trace")
    run_parser.add_argument(
        "--token-accounting",
        choices=("client", "server"),
        default="client",
        help=(
            "count tokens with the built-in tokenizer (client) or from provider "
            "usage (server); server makes the throughput denominator "
            "authoritative but stops verifying delivered target size"
        ),
    )
    run_parser.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help="override the profile concurrency for this run",
    )
    _add_mode_group(run_parser)

    report_parser = subparsers.add_parser("report", help="build a report from a completed run")
    report_parser.add_argument("--run", type=Path, required=True, help="run directory")

    compare_parser = subparsers.add_parser("compare", help="compare two completed runs")
    compare_parser.add_argument("--baseline", type=Path, required=True)
    compare_parser.add_argument("--candidate", type=Path, required=True)

    reprocess_parser = subparsers.add_parser(
        "reprocess",
        help=(
            "re-derive a completed run's evidence from its original artifacts without "
            "re-executing anything; writes to a new directory"
        ),
    )
    reprocess_parser.add_argument(
        "--run", type=Path, required=True, help="completed run directory (read-only)"
    )
    reprocess_parser.add_argument(
        "--out", type=Path, required=True, help="new directory for the corrected evidence"
    )

    session_parser = subparsers.add_parser(
        "session",
        help=(
            "session-dynamics: same-day default (generate/preflight/execute/analyze "
            "in one invocation) plus legacy temporal planning commands"
        ),
    )
    session_subparsers = session_parser.add_subparsers(
        dest="session_command", required=True
    )

    session_generate = session_subparsers.add_parser(
        "generate",
        help="legacy: freeze the 54-cell temporal plan and an unbound manifest offline",
    )
    _add_session_program_options(session_generate)
    session_generate.add_argument("--out", type=Path, required=True)
    session_generate.add_argument(
        "--release-nonce",
        default=None,
        help="optional fixed nonce for a reproducible cache namespace",
    )

    session_dry_run = session_subparsers.add_parser(
        "dry-run",
        help="bind a registry target and emit the preflight manifest; sends nothing",
    )
    _add_session_program_options(session_dry_run)
    session_dry_run.add_argument("--config", type=Path, default=Path("configs/registry.example.json"))
    session_dry_run.add_argument("--target", required=True)
    session_dry_run.add_argument("--out", type=Path, required=True)
    session_dry_run.add_argument("--max-usd", type=float, default=None)
    session_dry_run.add_argument("--release-nonce", default=None)

    session_execute = session_subparsers.add_parser(
        "execute", help="run exactly one released cell from a preflight manifest"
    )
    session_execute.add_argument("--manifest", type=Path, required=True)
    session_execute.add_argument("--operation", required=True)
    session_execute.add_argument("--out", type=Path, default=None)
    session_execute.add_argument("--state", type=Path, default=None)
    session_execute.add_argument("--max-requests", type=int, required=True)
    session_execute.add_argument("--max-usd", type=float, default=None)
    session_execute.add_argument("--authorize-live", action="store_true")
    session_execute.add_argument("--execute", action="store_true", required=True)
    session_execute.add_argument(
        "--idle-seconds",
        type=float,
        default=60.0,
        help="idle pause for the idle_resume scenario (60s in the released design)",
    )

    session_analyze = session_subparsers.add_parser(
        "analyze", help="validate and aggregate recorded session cells offline"
    )
    session_analyze.add_argument("--run-dir", type=Path, required=True)
    session_analyze.add_argument("--manifest", type=Path, default=None)
    session_analyze.add_argument("--out", type=Path, default=None)
    session_analyze.add_argument(
        "--allow-partial",
        action="store_true",
        help="aggregate available cells even when the manifest declares more",
    )

    session_same_day = session_subparsers.add_parser(
        "same-day",
        help=(
            "one foreground same-day invocation: generate/preflight, execute the "
            "30-cell calendar-free matrix in order, analyze and write the report"
        ),
    )
    session_same_day.add_argument(
        "--program",
        type=Path,
        default=Path("configs/session-programs/session-dynamics-sameday-v1.json"),
    )
    session_same_day.add_argument(
        "--out",
        type=Path,
        default=Path("docs/session-dynamics-sameday-v1"),
        help="run directory for plan, manifest, journal, cells, analysis and report",
    )
    session_same_day.add_argument(
        "--target-metadata",
        type=Path,
        default=Path("configs/session-programs/session-dynamics-v1.target.json"),
        help="committed, credential-free target binding used when --config is absent",
    )
    session_same_day.add_argument(
        "--config",
        type=Path,
        default=None,
        help="optional registry file; requires --target and overrides target metadata",
    )
    session_same_day.add_argument(
        "--target",
        default=None,
        help="registry deployment key (only with --config)",
    )
    session_same_day.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help=(
            "reuse a frozen same-day preflight manifest instead of rebuilding it; "
            "the manifest must live in --out"
        ),
    )
    session_same_day.add_argument(
        "--release-nonce",
        default="d0010-sameday-default",
        help="frozen plan nonce for a fresh build; keep it stable to resume",
    )
    session_same_day.add_argument(
        "--select",
        choices=("all", "screening", "context"),
        default="all",
        help="execution-scoped cell selection; the frozen plan stays the 30-cell matrix",
    )
    session_same_day.add_argument(
        "--execute",
        action="store_true",
        help="send inference; without this flag the command is an offline preflight",
    )
    session_same_day.add_argument(
        "--authorize-live",
        action="store_true",
        help="required for a non-loopback target; loopback tests need no paid release",
    )
    session_same_day.add_argument("--max-usd", type=float, default=None)
    session_same_day.add_argument(
        "--cell-deadline",
        type=float,
        default=None,
        help="lower the per-cell deadline (never above the frozen 600s default)",
    )
    session_same_day.add_argument(
        "--deadline-hours",
        type=float,
        default=None,
        help="lower the foreground invocation deadline (never above the frozen 6h)",
    )
    session_same_day.add_argument(
        "--idle-seconds",
        type=float,
        default=60.0,
        help="idle pause for the idle_resume scenario (60s in the released design)",
    )
    session_same_day.add_argument(
        "--state",
        type=Path,
        default=None,
        help="must be the canonical <out>/program-state.json when supplied",
    )

    return parser


def _add_session_program_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--program",
        type=Path,
        default=Path("configs/session-programs/session-dynamics-v1.json"),
    )



def _add_target_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path, default=Path("configs/registry.example.json"))
    parser.add_argument("--profile-file", type=Path, default=None)
    parser.add_argument("--target", required=True)
    parser.add_argument("--profile", default="smoke")


def _add_mode_group(parser: argparse.ArgumentParser) -> None:
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--execute", action="store_true")


def _resolve_profile_path(profile_file: Path | None, profile_name: str) -> Path:
    if profile_file is not None:
        return profile_file
    return Path("configs") / "profiles" / f"{profile_name}.json"


def _resolve_target(registry, target: str) -> Deployment:
    if target not in registry.deployments:
        available = ", ".join(sorted(registry.deployments)) or "none"
        raise RegistryError(f"unknown target `{target}`; available targets: {available}")
    return registry.deployments[target]


def _credential_env(deployment: Deployment) -> tuple[dict[str, str], list[str]]:
    """Read a credential from the environment only. Never from files or argv."""
    value = os.environ.get(deployment.credential_env_name)
    if not value:
        return {}, [f"credential environment variable {deployment.credential_env_name} not set"]
    return {deployment.credential_env_name: value}, []


def _execute_guard(
    deployment: Deployment,
    args: argparse.Namespace,
    profile: Profile,
    planned: int | None = None,
) -> list[str]:
    """Return blocking reasons for live execution. Empty means allowed.

    A local target must be a numeric loopback address on an explicit port.
    Hostname resolution is not trusted, so `localhost` and anything that merely
    resolves to loopback is rejected for unattended execution.
    """
    blockers: list[str] = []
    if planned is None:
        planned = profile.request_count
    loopback = is_loopback(deployment.base_url)

    if loopback:
        try:
            assert_loopback(deployment.base_url)
        except NetworkGuardError as error:
            blockers.append(f"local target rejected: {error}")
    elif not args.authorize_live:
        blockers.append(
            f"target `{deployment.deployment_key}` is not loopback; live inference requires "
            "--authorize-live"
        )
    if not loopback:
        if deployment.pricing.get("verified") is not True:
            blockers.append(
                "pricing is unverified; unattended live execution is blocked because cost "
                "cannot be conservatively bounded"
            )
        if args.max_usd is None:
            blockers.append("live execution requires --max-usd")
        elif args.max_usd <= 0:
            blockers.append("--max-usd must be positive")
        else:
            estimate = estimate_cost_usd(deployment, profile)
            if estimate is None:
                blockers.append(
                    "cost estimate unavailable: verified per-million rates are missing"
                )
            elif estimate > args.max_usd:
                blockers.append(
                    f"estimated cost ${estimate:.6f} exceeds --max-usd ${args.max_usd:.6f}"
                )
    if args.max_requests is None:
        blockers.append("execution requires --max-requests")
    elif args.max_requests <= 0:
        blockers.append("--max-requests must be positive")
    elif args.max_requests > planned:
        blockers.append(
            f"--max-requests {args.max_requests} exceeds the profile's planned {planned} requests"
        )
    return blockers


def _apply_concurrency(profile: Profile, concurrency: int | None) -> Profile:
    """Apply a validated concurrency override without mutating the profile file."""
    if concurrency is None:
        return profile
    if concurrency <= 0:
        raise AiperfError("--concurrency must be positive")
    if concurrency > profile.request_count:
        raise AiperfError(
            f"--concurrency {concurrency} exceeds the profile's {profile.request_count} requests"
        )
    return replace(profile, concurrency=concurrency)


def _planned_requests(trace_file: Path | None, profile: Profile) -> int:
    """Planned request count: trace entries when replaying, else the profile."""
    if trace_file is None:
        return profile.request_count
    if not trace_file.is_file():
        raise AiperfError(f"trace file does not exist: {trace_file}")
    lines = [
        line
        for line in trace_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not lines:
        raise AiperfError(f"trace file is empty: {trace_file}")
    return len(lines)


def _run_directory(parent: Path, run_id: str) -> Path:
    path = parent / run_id
    if path.exists():
        raise AiperfError(f"run directory already exists: {path}; runs are immutable")
    path.mkdir(parents=True)
    return path


_RUN_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")


def _validate_run_label(label: str | None) -> str | None:
    """Reject a label that could escape the run directory or break provenance.

    A repeated experiment runs the same configuration several times. The config
    hash is identical each time, so the label is the only thing that keeps the
    run identities distinct while the artifacts stay separate directories.
    """
    if label is None:
        return None
    if not _RUN_LABEL_PATTERN.fullmatch(label):
        raise AiperfError(
            "--run-label must be 1-32 characters of letters, digits, dots, dashes, "
            "or underscores, and must start with a letter or digit"
        )
    return label


def _run_id(config_sha256: str, label: str | None = None) -> str:
    suffix = f"-{label}" if label else ""
    return f"run-{config_sha256[:12]}{suffix}"


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

        if arguments.command == "mock":
            return _command_mock(arguments)

        if arguments.command == "probe":
            return _command_probe(arguments)

        if arguments.command == "run":
            return _command_run(arguments, parser)

        if arguments.command == "report":
            return _command_report(arguments)

        if arguments.command == "compare":
            return _command_compare(arguments)

        if arguments.command == "reprocess":
            return _command_reprocess(arguments)

        if arguments.command == "session":
            return _command_session(arguments, parser)

        parser.error(f"unhandled command `{arguments.command}`")
    except (
        RegistryError,
        ProfileError,
        AiperfError,
        ReportError,
        ReprocessError,
        SessionProgramError,
        SessionEngineError,
        SessionEvidenceError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


def _command_mock(arguments: argparse.Namespace) -> int:
    """Serve the mock provider until interrupted.

    Binding an explicit loopback port is what lets a contributor point the
    benchmark at a reachable local target and get a passing run.
    """
    from .mock_provider import MockBehavior, MockProvider

    try:
        behavior = MockBehavior(arguments.behavior)
    except ValueError as error:
        available = ", ".join(item.value for item in MockBehavior)
        raise AiperfError(f"unknown behavior `{arguments.behavior}`; available: {available}") from error

    provider = MockProvider(behavior=behavior, model_id=arguments.model_id)
    try:
        base_url = provider.start(port=arguments.port)
    except OSError as error:
        raise AiperfError(
            f"could not bind 127.0.0.1:{arguments.port} ({error}); choose another --port"
        ) from error
    print(f"mock provider listening at {base_url}", file=sys.stderr)
    print(f"behavior: {behavior.value}", file=sys.stderr)
    print("point a registry base_url at this address; press Ctrl-C to stop", file=sys.stderr)
    provider.serve_forever()
    return 0


def _command_probe(arguments: argparse.Namespace) -> int:
    registry = load_registry(arguments.config)
    deployment = _resolve_target(registry, arguments.target)
    profile = load_profile(_resolve_profile_path(arguments.profile_file, arguments.profile), arguments.profile)
    secrets, warnings = _credential_env(deployment)

    fixture = generate_fixture(count=arguments.requests, target_tokens=profile.input_tokens)
    config = ProbeConfig(
        base_url=deployment.base_url,
        model_id=deployment.model_id,
        timeout_s=profile.timeout_s,
        max_output_tokens=profile.max_output_tokens,
        streaming=profile.streaming,
    )
    api_key = secrets.get(deployment.credential_env_name)

    attempts_per_trial = 2 if arguments.scenario == "tool-round-trip" else 1
    preflight = cost_preflight(deployment, profile)

    if arguments.dry_run:
        _json_print(
            {
                "schema_version": RESULT_SCHEMA_VERSION,
                "manifest_type": "probe_dry_run",
                "scenario": arguments.scenario,
                "deployment_key": deployment.deployment_key,
                "profile": profile.name,
                "planned_trials": arguments.requests,
                "planned_http_attempts": arguments.requests * attempts_per_trial,
                "inference": {"executed": False, "network_requests": 0},
                "cost_preflight": preflight,
                "limitations": [
                    "This is a plan, not evidence. No request was sent.",
                ],
            }
        )
        return 0

    live = not is_loopback(deployment.base_url)
    if live and not arguments.authorize_live:
        raise AiperfError(
            "probe only runs against explicit numeric loopback targets without "
            f"--authorize-live; `{deployment.base_url}` is not one"
        )
    if live and not api_key:
        raise AiperfError(
            f"--authorize-live requires {deployment.credential_env_name} to be set"
        )
    if not live and api_key:
        raise AiperfError(
            "probe will not transmit a credential to a loopback target; "
            "the local mock is unauthenticated"
        )

    trials: list[Trial] = []
    for index in range(arguments.requests):
        trial = Trial(
            trial_id=f"probe-{index:04d}",
            deployment_key=deployment.deployment_key,
            profile_name=profile.name,
        )
        if arguments.scenario == "tool-round-trip":
            probe_tool_round_trip(
                config, api_key=api_key, prompt=DEFAULT_TOOL_PROMPT, trial=trial
            )
        else:
            probe(config, api_key=api_key, prompt=fixture.prompts[index].text, trial=trial)
        trials.append(trial)

    run_dir = _run_directory(arguments.run_dir, f"probe-{fixture.sha256[:12]}")
    trials_path = run_dir / "trials.jsonl"
    trials_sha = write_trials_jsonl(trials_path, trials)
    manifest = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "manifest_type": "probe",
        "software": {"name": "endpoint-bench", "version": __version__},
        "deployment_key": deployment.deployment_key,
        "profile": profile.name,
        "fixture": {"name": fixture.name, "version": fixture.version, "sha256": fixture.sha256},
        "workload_type": WORKLOAD_LIVE_TURN_CONFORMANCE,
        "scenario": arguments.scenario,
        "counters": count_trials(trials).to_dict(),
        "token_accounting": token_sizing_summary(trials),
        "trials_path": str(trials_path),
        "trials_sha256": trials_sha,
        "authorization": {
            "live_authorized": live,
            "credential_env_name": deployment.credential_env_name if live else None,
            "cost_preflight": preflight,
        },
        "warnings": warnings,
    }
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    _json_print(manifest)
    return 0


def _command_run(arguments: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    registry = load_registry(arguments.config)
    deployment = _resolve_target(registry, arguments.target)
    profile = load_profile(
        _resolve_profile_path(arguments.profile_file, arguments.profile), arguments.profile
    )

    profile = _apply_concurrency(profile, arguments.concurrency)
    run_label = _validate_run_label(arguments.run_label)
    planned = _planned_requests(arguments.trace_file, profile)

    if arguments.dry_run:
        _json_print(build_dry_run_manifest(registry, deployment, profile))
        return 0

    blockers = _execute_guard(deployment, arguments, profile, planned)
    if not is_loopback(deployment.base_url) and not os.environ.get(
        deployment.credential_env_name or ""
    ):
        blockers.append(
            f"credential environment variable {deployment.credential_env_name} is not set"
        )
    if blockers:
        for reason in blockers:
            print(f"error: {reason}", file=sys.stderr)
        return 2

    client = (
        BASELINE_SERVER_ACCOUNTING
        if arguments.token_accounting == "server"
        else BASELINE_CLIENT
    )
    secrets, warnings = _credential_env(deployment)
    # A loopback mock has no account, so requiring a credential would make the
    # contributor demo unrunnable. Non-loopback targets still must present one.
    credential_env_name = deployment.credential_env_name or None
    if not secrets and is_loopback(deployment.base_url):
        credential_env_name = None
        warnings = [warning for warning in warnings if "not set" not in warning]

    config = build_benchmark_config(
        model_id=deployment.model_id,
        base_url=deployment.base_url,
        client=client,
        requests=planned,
        concurrency=profile.concurrency,
        input_tokens=profile.input_tokens,
        output_tokens=profile.max_output_tokens,
        artifact_dir=Path("pending"),
        credential_env_name=credential_env_name,
        streaming=profile.streaming,
        # Raw request/response evidence is the backbone of every run: it is
        # the only place the HTTP status and the exact outbound headers survive.
        capture_raw=True,
        trace_file=arguments.trace_file,
        trace_type=arguments.trace_type,
    )
    preview_sha = hashlib.sha256(dump_yaml(config).encode("utf-8")).hexdigest()
    run_id = _run_id(preview_sha, run_label)
    run_dir = _run_directory(arguments.run_dir, run_id)

    config["benchmark"]["artifacts"]["dir"] = str(run_dir / "aiperf")
    config_path = run_dir / "aiperf-config.yaml"
    config_sha = write_config(config_path, config)

    run = run_aiperf(
        executable=str(arguments.aiperf),
        config_path=config_path,
        artifact_dir=run_dir / "aiperf",
        client=client,
        secrets=secrets,
        process_timeout_s=arguments.process_timeout,
        config_sha256=config_sha,
        target_url=deployment.base_url,
    )

    if not run.completed and not (run_dir / "aiperf").exists():
        raise AiperfError(
            f"AIPerf failed before producing artifacts (exit {run.exit_code}); "
            f"see {run.stderr_path}"
        )

    results = parse_artifacts(
        run_dir / "aiperf",
        deployment_key=deployment.deployment_key,
        profile_name=profile.name,
        target_input_tokens=profile.input_tokens,
        token_tolerance_pct=profile.token_tolerance_pct,
        accounting_source=(
            ACCOUNTING_PROVIDER_USAGE
            if client.use_server_token_count
            else ACCOUNTING_CLIENT_TOKENIZER
        ),
    )
    trials_sha = write_trials_jsonl(run_dir / "trials.jsonl", results.trials)
    provenance = build_provenance(
        deployment=deployment,
        profile=profile,
        client=client,
        results=results,
        workload_type=(
            WORKLOAD_RECORDED_MULTI_TURN_REPLAY
            if arguments.trace_file
            else WORKLOAD_SYNTHETIC_SINGLE_TURN
        ),
        run_dir=run_dir,
        run=run,
        planned_requests=planned,
        config_path=config_path,
        config_sha256=config_sha,
        trials_path=run_dir / "trials.jsonl",
        artifact_dir=run_dir / "aiperf",
        process_deadline_s=arguments.process_timeout,
        max_usd_ceiling=arguments.max_usd,
    )
    report = build_report(
        run_id=run_id,
        deployment_key=deployment.deployment_key,
        profile_name=profile.name,
        client_profile=client,
        results=results,
        trials=results.trials,
        provenance=provenance,
    )
    report["run"] = run.to_dict()
    report["trials_sha256"] = trials_sha
    report["warnings"] = warnings
    write_report(run_dir / "report.json", report)

    _json_print(
        {
            "run_id": run_id,
            "run_dir": str(run_dir),
            "exit_code": run.exit_code,
            "timed_out": run.timed_out,
            "counters": results.counters.to_dict(),
            "benchmark_failed": results.benchmark_failed,
            "report_path": str(run_dir / "report.json"),
        }
    )
    return 0 if not results.benchmark_failed else 1


def _command_report(arguments: argparse.Namespace) -> int:
    run_dir = arguments.run
    if not run_dir.is_dir():
        raise ReportError(f"run directory does not exist: {run_dir}")
    report_path = run_dir / "report.json"
    if report_path.is_file():
        _json_print(load_report(report_path))
        return 0
    raise ReportError(f"no report.json in {run_dir}; run `endpoint-bench run` or `probe` first")


def _command_compare(arguments: argparse.Namespace) -> int:
    baseline = load_report(arguments.baseline / "report.json")
    candidate = load_report(arguments.candidate / "report.json")
    _json_print(compare_reports(baseline, candidate))
    return 0


def _command_reprocess(arguments: argparse.Namespace) -> int:
    """Correct a published run's normalized evidence without re-executing it."""
    correction = reprocess_run(arguments.run, arguments.out)
    _json_print(
        {
            "source_run_id": correction["source_run_id"],
            "accounting_source": correction["accounting_source"],
            "counters_before": correction["counters_before"],
            "counters_after": correction["counters_after"],
            "aggregate_field_changes": len(correction["aggregate_field_changes"]),
            "unrecoverable": len(correction["unrecoverable"]),
            "out": str(arguments.out),
        }
    )
    return 0


def _command_session(
    arguments: argparse.Namespace, parser: argparse.ArgumentParser
) -> int:
    if arguments.session_command == "generate":
        return _session_generate(arguments)
    if arguments.session_command == "dry-run":
        return _session_dry_run(arguments)
    if arguments.session_command == "execute":
        return _session_execute(arguments)
    if arguments.session_command == "analyze":
        return _session_analyze(arguments)
    if arguments.session_command == "same-day":
        return _session_same_day(arguments)
    parser.error(f"unhandled session command `{arguments.session_command}`")
    return 2


def _operational_rates(deployment: Deployment | None) -> dict[str, object]:
    """Use locally configured rates for the guard; never research pricing here.

    Pricing is not a research task for this program. The existing rate card is
    reused only so the CLI's cost guard stays functional, and the resulting value
    is a conservative operational envelope, not a budget forecast.
    """
    if deployment is not None:
        prompt = deployment.pricing.get("prompt_per_million_tokens")
        completion = deployment.pricing.get("completion_per_million_tokens")
        if (
            isinstance(prompt, (int, float))
            and not isinstance(prompt, bool)
            and isinstance(completion, (int, float))
            and not isinstance(completion, bool)
            and prompt >= 0
            and completion >= 0
        ):
            return {
                "prompt_per_million_tokens": float(prompt),
                "completion_per_million_tokens": float(completion),
                "safety_multiplier": 1.5,
                "source": str(deployment.pricing.get("source")),
                "effective_date": deployment.pricing.get("effective_date"),
                "pricing_verified": deployment.pricing.get("verified") is True,
            }
    return {
        "prompt_per_million_tokens": 0.30,
        "completion_per_million_tokens": 1.20,
        "safety_multiplier": 1.5,
        "source": (
            "existing local verified rate card, 2026-11-01; target pricing is "
            "absent, so this is an operational envelope only"
        ),
        "effective_date": "2026-11-01",
        "pricing_verified": False,
    }


def _target_block(deployment: Deployment) -> dict[str, object]:
    return {
        "deployment_key": deployment.deployment_key,
        "model_id": deployment.model_id,
        "base_url": deployment.base_url,
        "protocol": deployment.protocol,
        "region": deployment.region,
        "credential_env_name": deployment.credential_env_name,
        "model_revision": deployment.model_revision,
        "pricing_verified": deployment.pricing.get("verified") is True,
        "bound": True,
    }


def _session_generate(arguments: argparse.Namespace) -> int:
    program = load_program(arguments.program)
    out = arguments.out
    out.mkdir(parents=True, exist_ok=True)
    plan = build_plan(
        program, output_dir=out, release_nonce=arguments.release_nonce
    )
    manifest = build_manifest(
        plan,
        manifest_path=out / "manifest.json",
        plan_path=Path("plan.json"),
        operational_rates=_operational_rates(None),
    )
    verify_prepared_manifest(
        manifest, plan, require_unreleased=True, check_sources=True
    )
    write_json_atomic(out / "plan.json", plan)
    write_json_atomic(out / "manifest.json", manifest)
    _json_print(
        {
            "manifest_type": manifest["manifest_type"],
            "plan_sha256": plan["plan_sha256"],
            "manifest_sha256": manifest["manifest_sha256"],
            "cells": manifest["totals"]["cells"],
            "max_inference_attempts": manifest["totals"]["max_inference_attempts"],
            "operational_usd_envelope": manifest["totals"]["operational_usd_envelope"],
            "live_released_operation_ids": manifest["release"]["live_released_operation_ids"],
            "plan_path": str(out / "plan.json"),
            "manifest_path": str(out / "manifest.json"),
            "inference": {"executed": False, "network_requests": 0},
        }
    )
    return 0


def _session_dry_run(arguments: argparse.Namespace) -> int:
    program = load_program(arguments.program)
    registry = load_registry(arguments.config)
    deployment = _resolve_target(registry, arguments.target)
    out = arguments.out
    out.mkdir(parents=True, exist_ok=True)
    plan = build_plan(
        program, output_dir=out, release_nonce=arguments.release_nonce
    )
    rates = _operational_rates(deployment)
    target_block = _target_block(deployment)
    target_block["registry_sha256"] = registry.source_sha256
    target_block["registry_path"] = str(registry.source_path)
    manifest = build_manifest(
        plan,
        manifest_path=out / "manifest.json",
        plan_path=Path("plan.json"),
        target=target_block,
        operational_rates=rates,  # type: ignore[arg-type]
    )
    verify_prepared_manifest(
        manifest, plan, require_unreleased=True, check_sources=True
    )
    envelope = float(manifest["totals"]["operational_usd_envelope"])
    if arguments.max_usd is not None and arguments.max_usd < envelope:
        raise SessionProgramError(
            f"--max-usd {arguments.max_usd:.6f} is below the conservative "
            f"operational envelope {envelope:.6f}"
        )
    write_json_atomic(out / "plan.json", plan)
    write_json_atomic(out / "manifest.json", manifest)
    _json_print(
        {
            "manifest_type": manifest["manifest_type"],
            "target": manifest["target"]["deployment_key"],
            "model_id": manifest["target"]["model_id"],
            "region": manifest["target"]["region"],
            "cells": manifest["totals"]["cells"],
            "max_inference_attempts": manifest["totals"]["max_inference_attempts"],
            "request_timeout_s": manifest["timeouts"]["request_timeout_s"],
            "cell_process_deadline_s": manifest["timeouts"]["cell_process_deadline_s"],
            "operational_usd_envelope": envelope,
            "max_usd_guard": arguments.max_usd,
            "live_released_operation_ids": manifest["release"]["live_released_operation_ids"],
            "inference": {
                "executed": False,
                "network_requests": 0,
                "credentials_sent": False,
            },
            "plan_path": str(out / "plan.json"),
            "manifest_path": str(out / "manifest.json"),
        }
    )
    return 0


def _load_session_manifest(path: Path) -> dict:
    if not path.is_file():
        raise SessionProgramError(f"session manifest does not exist: {path}")
    document = json.loads(path.read_text(encoding="utf-8"))
    validate_manifest_document(document)
    return document


def _session_operation(manifest: dict, operation_id: str) -> dict:
    for operation in manifest["operations"]:
        if operation["operation_id"] == operation_id:
            return operation
    raise SessionProgramError(
        f"operation `{operation_id}` is not in the supplied manifest"
    )


def _load_plan_for_manifest(manifest: dict, manifest_path: Path) -> dict:
    plan_path = Path(str(manifest.get("plan_path")))
    if not plan_path.is_absolute():
        plan_path = manifest_path.parent / plan_path
    if not plan_path.is_file():
        raise SessionProgramError(f"plan file does not exist: {plan_path}")
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise SessionProgramError(f"plan file is not valid JSON: {error}") from error
    if not isinstance(plan, dict):
        raise SessionProgramError("plan file must be a JSON object")
    if plan.get("plan_sha256") != manifest.get("plan_sha256"):
        raise SessionProgramError("plan file does not match the manifest's plan hash")
    if plan.get("plan_sha256") != plan_content_sha256(plan):
        raise SessionProgramError(
            "plan content does not match its declared plan_sha256; the plan was edited "
            "after generation"
        )
    validate_plan_document(plan)
    return plan


def _cell_usd_envelope(operation: dict, rates: dict, limits: dict) -> float:
    attempts = int(operation["max_inference_attempts"])
    per_call_input = int(
        operation.get("requested_input_tokens")
        or operation.get("session_max_input_tokens")
        or limits["session_input_tokens_max"]
    )
    per_call_output = int(limits["max_completion_tokens_per_call"])
    nominal = (
        attempts * per_call_input / 1_000_000 * float(rates["prompt_per_million_tokens"])
        + attempts * per_call_output / 1_000_000 * float(rates["completion_per_million_tokens"])
    )
    return round(nominal * float(rates["safety_multiplier"]), 6)


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _window_guard(
    manifest: dict, operation: dict, *, loopback: bool
) -> None:
    """Refuse a missed or not-yet-open temporal window.

    Missed windows stay missed: the harness never backfills, relabels, or shifts
    the anchor to make a late cell look on time. The same-day v2 manifest has no
    calendar anchor by construction, so it is always eligible.
    """
    run_mode = manifest.get("run_mode")
    if isinstance(run_mode, dict) and run_mode.get("kind") == "foreground_same_day":
        return
    anchor_value = manifest.get("release", {}).get("release_anchor_utc")
    phase = operation["phase"]
    if anchor_value is None:
        if not loopback:
            raise SessionProgramError(
                "live execution requires a frozen release_anchor_utc; the manifest "
                "is a preflight without a temporal anchor"
            )
        return
    anchor = _parse_utc(str(anchor_value))
    now = datetime.now(UTC)
    deadline = float(manifest["timeouts"]["cell_process_deadline_s"])
    if phase == "session_screening":
        if now >= anchor:
            raise SessionProgramError(
                "the screening phase must finish before the temporal anchor; a late "
                "screening cell is not moved into a window"
            )
        return
    if phase == "temporal_repeats":
        offset = float(operation.get("start_offset_hours") or 0.0)
        start = anchor + timedelta(seconds=offset * 3600.0)
        end = start + timedelta(seconds=6.0 * deadline)
    elif phase == "context_sentinels":
        start = anchor + timedelta(seconds=30.0 * 3600.0 + 6.0 * deadline)
        end = start + timedelta(seconds=3.0 * deadline)
    else:
        return
    if now < start:
        raise SessionProgramError(
            f"window for `{operation['operation_id']}` is not open until {start.isoformat()}; "
            "do not start early"
        )
    if now > end:
        raise SessionProgramError(
            f"window for `{operation['operation_id']}` closed at {end.isoformat()}; "
            "missed windows stay missed and are never backfilled or relabelled"
        )


def _program_budget_guard(journal: JsonJournal, manifest: dict) -> None:
    created = journal.state.get("created_at_utc")
    if isinstance(created, str):
        elapsed_s = (datetime.now(UTC) - _parse_utc(created)).total_seconds()
        if elapsed_s > float(manifest["limits"]["max_program_elapsed_hours"]) * 3600.0:
            raise SessionProgramError(
                "the program elapsed cap has been exceeded; stop and preserve "
                "partial evidence rather than starting another cell"
            )
    active_ms = sum(
        float(operation.get("duration_ms") or 0.0)
        for operation in journal.state.get("operations", {}).values()
    )
    deadline_s = float(manifest["timeouts"]["cell_process_deadline_s"])
    if active_ms / 1000.0 + deadline_s > float(
        manifest["limits"]["max_active_execution_hours"]
    ) * 3600.0:
        raise SessionProgramError(
            "starting this cell could exceed the active-execution cap"
        )


def _session_execute(arguments: argparse.Namespace) -> int:
    manifest = _load_session_manifest(arguments.manifest)
    operation = _session_operation(manifest, arguments.operation)
    plan = _load_plan_for_manifest(manifest, arguments.manifest)
    # Recompute every frozen identity before a journal intent or any HTTP. A
    # declared digest string is not evidence; the content, source files,
    # workload fixtures, program definition and operation limits must agree.
    verify_prepared_manifest(manifest, plan, check_sources=True)
    plan_cells = {cell["operation_id"]: cell for cell in plan["operations"]}
    if arguments.operation not in plan_cells:
        raise SessionProgramError(
            f"operation `{arguments.operation}` is absent from the plan"
        )
    cell = dict(plan_cells[arguments.operation])
    cell["session_max_input_tokens"] = operation["session_max_input_tokens"]
    cell["workload_type"] = operation.get("workload_type")
    for field in (
        "scenario",
        "max_inference_attempts",
        "concurrent_sessions",
        "requested_input_tokens",
        "output_path",
        "model_calls_per_session",
    ):
        if cell.get(field) != operation.get(field):
            raise SessionProgramError(
                f"manifest drift for `{arguments.operation}`: plan {field} "
                f"{cell.get(field)!r} != manifest {operation.get(field)!r}"
            )

    target = manifest.get("target") or {}
    base_url = target.get("base_url")
    if not isinstance(base_url, str) or not base_url:
        raise SessionProgramError(
            "manifest target is unbound; run `session dry-run` with a registry target first"
        )
    loopback = is_loopback(base_url)
    released = manifest.get("release", {}).get("live_released_operation_ids") or []
    if not loopback:
        if arguments.operation not in released:
            raise SessionProgramError(
                f"operation `{arguments.operation}` is not live-released; this manifest "
                "is an offline preflight contract"
            )
        if not arguments.authorize_live:
            raise SessionProgramError(
                "non-loopback execution requires --authorize-live"
            )
    if arguments.max_requests != int(operation["max_inference_attempts"]):
        raise SessionProgramError(
            f"--max-requests must equal the operation's {operation['max_inference_attempts']} "
            "planned calls; partial caps cannot complete a cell"
        )
    envelope = _cell_usd_envelope(
        operation, manifest["operational_rates"], manifest["limits"]
    )
    if not loopback:
        if arguments.max_usd is None:
            raise SessionProgramError("live execution requires --max-usd")
        if arguments.max_usd < envelope:
            raise SessionProgramError(
                f"--max-usd {arguments.max_usd:.6f} is below the cell envelope "
                f"{envelope:.6f}"
            )

    _window_guard(manifest, operation, loopback=loopback)
    out = arguments.out or arguments.manifest.parent
    canonical_state = (arguments.manifest.parent / "program-state.json").resolve()
    state_path = (
        Path(arguments.state).resolve() if arguments.state is not None else canonical_state
    )
    if state_path != canonical_state:
        raise SessionProgramError(
            f"--state `{state_path}` would bypass no-replay accounting for this released "
            f"program; the canonical journal is `{canonical_state}`"
        )
    out.mkdir(parents=True, exist_ok=True)

    with JsonJournal(
        state_path,
        program_id=manifest["program_id"],
        plan_sha256=manifest["plan_sha256"],
    ) as journal:
        _program_budget_guard(journal, manifest)
        status = journal.operation_status(arguments.operation)
        if status == "completed":
            _json_print(
                {
                    "operation_id": arguments.operation,
                    "state": "already_completed",
                    "inference": {"executed": False, "network_requests": 0},
                    "note": "a completed cell is never re-run; resume only touches unstarted cells",
                }
            )
            return 0
        if status in ("started", "interrupted", "invalid"):
            raise SessionProgramError(
                f"operation `{arguments.operation}` is {status}; partial cells are preserved "
                "and never replayed. Start only an unstarted operation."
            )
        attempted_so_far = sum(
            1
            for attempt in journal.state.get("attempts", {}).values()
            if attempt.get("status") == "finished"
        )
        if attempted_so_far + int(operation["max_inference_attempts"]) > int(
            manifest["limits"]["max_total_new_inference_attempts"]
        ):
            raise SessionProgramError(
                "executing this cell would exceed the program's "
                f"{manifest['limits']['max_total_new_inference_attempts']}-attempt "
                "finite cap"
            )

        api_key = None
        credential_env_name = target.get("credential_env_name")
        if not loopback:
            if not isinstance(credential_env_name, str) or not os.environ.get(
                credential_env_name
            ):
                raise SessionProgramError(
                    f"credential environment variable {credential_env_name} is not set"
                )
            api_key = os.environ[credential_env_name]

        session_target = SessionTarget(
            base_url=base_url,
            model_id=str(target.get("model_id")),
            api_key=api_key,
            timeout_s=float(manifest["timeouts"]["request_timeout_s"]),
            max_output_tokens=int(manifest["limits"]["max_completion_tokens_per_call"]),
            streaming=True,
        )
        if float(session_target.timeout_s) != float(
            manifest["limits"]["request_timeout_s"]
        ):
            raise SessionProgramError(
                "runtime request timeout does not equal the manifest timeouts.request_timeout_s"
            )
        journal.begin_operation(arguments.operation)
        runner = SessionCellRunner(
            cell=cell,
            target=session_target,
            journal=journal,
            idle_seconds=arguments.idle_seconds,
            max_cell_seconds=float(manifest["timeouts"]["cell_process_deadline_s"]),
            max_total_attempts=int(manifest["limits"]["max_total_new_inference_attempts"])
            - attempted_so_far,
        )
        if float(runner.max_cell_seconds) != float(
            manifest["timeouts"]["cell_process_deadline_s"]
        ):
            raise SessionProgramError(
                "runtime cell deadline does not equal the manifest cell_process_deadline_s"
            )
        try:
            evidence = runner.run()
        except BaseException:
            partial = runner.snapshot()
            if partial is not None:
                try:
                    artifacts = write_cell_evidence(out, partial)
                    journal.fail_operation(
                        arguments.operation,
                        f"interrupted_partial_cell:{artifacts.cell_path}",
                    )
                except Exception:
                    pass
            raise
        artifacts = write_cell_evidence(out, evidence)
        counters = evidence.counters()
        journal.finish_operation(
            arguments.operation,
            cell_path=str(artifacts.cell_path),
            counters=counters,
            status=evidence.status,
            stop_reason=evidence.stop_reason,
            duration_ms=evidence.elapsed_ms,
        )
        _json_print(
            {
                "operation_id": arguments.operation,
                "status": evidence.status,
                "stop_reason": evidence.stop_reason,
                "evidence_complete": evidence.evidence_complete,
                "cache_prefix_audit": evidence.cache_prefix_audit,
                "counters": counters,
                "cell_path": str(artifacts.cell_path),
                "cell_sha256": artifacts.cell_sha256,
                "state_path": str(state_path),
                "inference": {
                    "executed": True,
                    "authorized": bool(arguments.authorize_live),
                    "loopback": loopback,
                    "cell_usd_envelope": envelope,
                },
            }
        )
        return 0 if evidence.status == "completed" else 1


def _session_analyze(arguments: argparse.Namespace) -> int:
    manifest = None
    if arguments.manifest is not None:
        manifest = _load_session_manifest(arguments.manifest)
    report = analyze_cells(
        arguments.run_dir,
        manifest=manifest,
        require_complete=not arguments.allow_partial,
    )
    # `--out` keeps a historical run's original evidence directory untouched;
    # the default remains the run directory for backward compatibility.
    paths = write_analysis(arguments.out or arguments.run_dir, report)
    _json_print(
        {
            "analysis": report["analysis"],
            "cell_count": report["cell_count"],
            "missing_operations": len(report["missing_operations"]),
            "scenarios": sorted(report["scenarios"]),
            **paths,
        }
    )
    return 0


# -- same-day foreground invocation ------------------------------------------


def _same_day_clock() -> float:
    """Monotonic clock seam so the finite invocation guard is testable."""
    return time.monotonic()


def _same_day_selection(
    plan: dict, selection: str
) -> list[dict]:
    operations = list(plan["operations"])
    if selection == "all":
        return operations
    if selection == "screening":
        return [op for op in operations if op["phase"] == "session_screening"]
    return [op for op in operations if op["phase"] == "context_sentinels"]


def _same_day_target(arguments: argparse.Namespace) -> tuple[dict, dict]:
    note = (
        "Target-bound same-day run. Non-loopback execution requires an explicit "
        "--execute --authorize-live invocation; no credential value is stored in "
        "this manifest."
    )
    if arguments.config is not None:
        if not arguments.target:
            raise SessionProgramError("--config requires --target")
        registry = load_registry(arguments.config)
        deployment = _resolve_target(registry, arguments.target)
        target = _target_block(deployment)
        target["registry_sha256"] = registry.source_sha256
        target["registry_path"] = str(registry.source_path)
        target["note"] = note
        return target, _operational_rates(deployment)
    if arguments.target is not None:
        raise SessionProgramError("--target requires --config")
    return load_target_metadata(arguments.target_metadata, note=note)


def _same_day_cell_deadline(plan: dict, arguments: argparse.Namespace) -> float:
    frozen = float(plan["limits"]["cell_process_deadline_s"])
    if arguments.cell_deadline is None:
        return frozen
    value = float(arguments.cell_deadline)
    if value <= 0:
        raise SessionProgramError("--cell-deadline must be positive")
    if value > frozen:
        raise SessionProgramError(
            f"--cell-deadline {value:.1f}s exceeds the frozen {frozen:.1f}s default"
        )
    return value


def _same_day_invocation_deadline(
    plan: dict, arguments: argparse.Namespace
) -> float:
    frozen = float(plan["limits"]["max_foreground_invocation_seconds"])
    if arguments.deadline_hours is None:
        return frozen
    value = float(arguments.deadline_hours) * 3600.0
    if value <= 0:
        raise SessionProgramError("--deadline-hours must be positive")
    if value > frozen:
        raise SessionProgramError(
            f"--deadline-hours {arguments.deadline_hours:.3f}h exceeds the frozen "
            f"{frozen / 3600.0:.3f}h foreground deadline"
        )
    return value


def _same_day_live_guards(
    arguments: argparse.Namespace,
    target: dict,
    selected_attempts: int,
    selected_envelope: float,
    plan: dict,
    *,
    loopback: bool,
) -> None:
    if selected_attempts <= 0:
        raise SessionProgramError("the selection contains no executable calls")
    if selected_attempts > int(plan["limits"]["max_total_new_inference_attempts"]):
        raise SessionProgramError(
            "the selection exceeds the program's finite attempt cap"
        )
    if loopback:
        return
    if not arguments.authorize_live:
        raise SessionProgramError(
            "non-loopback execution requires --authorize-live; the same-day command "
            "is an offline preflight without it"
        )
    if arguments.max_usd is None:
        raise SessionProgramError(
            "live execution requires --max-usd covering the selected cell envelope"
        )
    if arguments.max_usd <= 0:
        raise SessionProgramError("--max-usd must be positive")
    if arguments.max_usd < selected_envelope:
        raise SessionProgramError(
            f"--max-usd {arguments.max_usd:.6f} is below the selected-cell envelope "
            f"{selected_envelope:.6f}"
        )
    credential_env_name = target.get("credential_env_name")
    if not isinstance(credential_env_name, str) or not os.environ.get(
        credential_env_name
    ):
        raise SessionProgramError(
            f"credential environment variable {credential_env_name} is not set"
        )


def _same_day_execute(
    journal: JsonJournal,
    plan: dict,
    selected: list[dict],
    target: SessionTarget,
    out: Path,
    *,
    cell_deadline: float,
    invocation_deadline: float,
    idle_seconds: float,
) -> dict:
    """Execute selected cells in frozen order under one finite foreground deadline."""
    started_mono = _same_day_clock()
    started_at = utc_now()
    deadline = started_mono + invocation_deadline
    cells = {cell["operation_id"]: cell for cell in plan["operations"]}
    total_limit = int(plan["limits"]["max_total_new_inference_attempts"])
    consumed = sum(
        1
        for attempt in journal.state.get("attempts", {}).values()
        if attempt.get("status") in ("finished", "interrupted")
    )
    results: list[dict] = []
    processed: set[str] = set()
    stop: dict | None = None
    cancelled = False

    def record(operation: dict, outcome: str, **extra: object) -> dict:
        entry = {
            "operation_id": operation["operation_id"],
            "phase": operation["phase"],
            "outcome": outcome,
            **extra,
        }
        results.append(entry)
        processed.add(operation["operation_id"])
        return entry

    for operation in selected:
        operation_id = operation["operation_id"]
        status = journal.operation_status(operation_id)
        if status == "completed":
            record(
                operation,
                "already_completed",
                attempts=0,
                note="preserved from an earlier invocation; never replayed",
            )
            continue
        if status in ("started", "interrupted", "invalid"):
            record(
                operation,
                f"preserved_{status}",
                attempts=0,
                note="partial evidence preserved; never replayed",
            )
            continue
        remaining = deadline - _same_day_clock()
        if remaining <= 0:
            stop = {
                "reason": "invocation_deadline",
                "operation_id": operation_id,
                "at_utc": utc_now(),
            }
            record(operation, "skipped_deadline", attempts=0)
            continue
        effective = min(cell_deadline, remaining)
        cell = dict(cells[operation_id])
        cell["workload_type"] = plan["workload_type"]
        journal.begin_operation(operation_id)
        runner = SessionCellRunner(
            cell=cell,
            target=target,
            journal=journal,
            idle_seconds=idle_seconds,
            max_cell_seconds=effective,
            max_total_attempts=max(total_limit - consumed, 0),
        )
        try:
            evidence = runner.run()
        except KeyboardInterrupt:
            partial = runner.snapshot()
            cell_path = None
            if partial is not None:
                try:
                    artifacts = write_cell_evidence(out, partial)
                    cell_path = str(artifacts.cell_path)
                    journal.fail_operation(
                        operation_id, f"cancelled_partial_cell:{cell_path}"
                    )
                except Exception:
                    cell_path = None
            record(
                operation,
                "cancelled",
                attempts=(
                    partial.counters()["attempted_calls"] if partial else 0
                ),
                cell_path=cell_path,
            )
            stop = {
                "reason": "cancelled",
                "operation_id": operation_id,
                "at_utc": utc_now(),
            }
            cancelled = True
            break
        except BaseException as error:
            partial = runner.snapshot()
            cell_path = None
            if partial is not None:
                try:
                    artifacts = write_cell_evidence(out, partial)
                    cell_path = str(artifacts.cell_path)
                    journal.fail_operation(
                        operation_id, f"failed_partial_cell:{cell_path}"
                    )
                except Exception:
                    cell_path = None
            record(
                operation,
                "failed",
                attempts=(
                    partial.counters()["attempted_calls"] if partial else 0
                ),
                cell_path=cell_path,
                error=type(error).__name__,
            )
            stop = {
                "reason": "cell_exception",
                "detail": str(error),
                "operation_id": operation_id,
                "at_utc": utc_now(),
            }
            break
        artifacts = write_cell_evidence(out, evidence)
        counters = evidence.counters()
        journal.finish_operation(
            operation_id,
            cell_path=str(artifacts.cell_path),
            counters=counters,
            status=evidence.status,
            stop_reason=evidence.stop_reason,
            duration_ms=evidence.elapsed_ms,
        )
        consumed += counters["attempted_calls"]
        record(
            operation,
            "executed",
            cell_status=evidence.status,
            stop_reason=evidence.stop_reason,
            evidence_complete=evidence.evidence_complete,
            attempts=counters["attempted_calls"],
            elapsed_ms=evidence.elapsed_ms,
            cell_path=str(artifacts.cell_path),
        )
        if evidence.status != "completed":
            stop = {
                "reason": evidence.stop_reason or evidence.status,
                "operation_id": operation_id,
                "at_utc": utc_now(),
            }
            break

    for operation in selected:
        if operation["operation_id"] in processed:
            continue
        reason = (
            "skipped_deadline"
            if stop is not None and stop["reason"] == "invocation_deadline"
            else "skipped_after_stop"
        )
        record(operation, reason, attempts=0)

    if cancelled:
        run_status = "CANCELLED"
    elif stop is not None:
        run_status = "PARTIAL"
    elif all(
        entry["outcome"] in ("executed", "already_completed") for entry in results
    ):
        run_status = "COMPLETED"
    else:
        run_status = "PARTIAL"

    return {
        "status": run_status,
        "stop": stop,
        "cancelled": cancelled,
        "results": results,
        "started_at_utc": started_at,
        "ended_at_utc": utc_now(),
        "wall_elapsed_s": round(_same_day_clock() - started_mono, 3),
        "attempted_calls": sum(
            int(entry.get("attempts") or 0) for entry in results
        ),
    }


def _same_day_summary(
    run: dict,
    plan: dict,
    manifest: dict,
    released_manifest: dict,
    *,
    selection: str,
    selected: list[dict],
    cell_deadline: float,
    invocation_deadline: float,
    loopback: bool,
    authorized_live: bool,
) -> dict:
    outcomes = [entry["outcome"] for entry in run["results"]]

    def count(*values: str) -> int:
        return sum(1 for outcome in outcomes if outcome in values)

    return {
        "schema_version": "session-same-day-run/1.0.0",
        "generated_at_utc": utc_now(),
        "program_id": plan["program_id"],
        "directive_id": plan["directive_id"],
        "workload_type": plan["workload_type"],
        "plan_id": plan["plan_id"],
        "plan_sha256": plan["plan_sha256"],
        "manifest_sha256": manifest["manifest_sha256"],
        "released_manifest_sha256": released_manifest["manifest_sha256"],
        "execution_identity_sha256": manifest["execution_identity_sha256"],
        "target": {
            key: manifest["target"].get(key)
            for key in (
                "deployment_key",
                "model_id",
                "base_url",
                "protocol",
                "region",
                "credential_env_name",
                "model_revision",
                "target_metadata_sha256",
            )
        },
        "selection": selection,
        "selected_operation_ids": [op["operation_id"] for op in selected],
        "selected_max_inference_attempts": sum(
            int(op["max_inference_attempts"]) for op in selected
        ),
        "cell_deadline_s": cell_deadline,
        "invocation_deadline_s": invocation_deadline,
        "started_at_utc": run["started_at_utc"],
        "ended_at_utc": run["ended_at_utc"],
        "wall_elapsed_s": run["wall_elapsed_s"],
        "status": run["status"],
        "stop": run["stop"],
        "cells": run["results"],
        "totals": {
            "selected": len(selected),
            "executed_this_invocation": count("executed"),
            "already_completed": count("already_completed"),
            "preserved_partial": count(
                "preserved_started", "preserved_interrupted", "preserved_invalid"
            ),
            "skipped_deadline": count("skipped_deadline"),
            "skipped_after_stop": count("skipped_after_stop"),
            "cancelled": count("cancelled"),
            "failed": count("failed"),
            "attempted_calls": run["attempted_calls"],
            "completed_cells_total": count("already_completed")
            + sum(
                1
                for entry in run["results"]
                if entry["outcome"] == "executed"
                and entry.get("cell_status") == "completed"
            ),
        },
        "inference": {
            "executed": True,
            "loopback": loopback,
            "authorized_live": authorized_live,
            "network_requests": run["attempted_calls"],
        },
        "limits": plan["limits"],
        "note": (
            "One finite foreground invocation. No calendar anchor, not-before window, "
            "scheduled wait, background monitor or automatic repeat block was used. "
            "A same-day repeat, if any, is not evidence of day-to-day stability."
        ),
    }


def _write_same_day_preflight(
    out: Path,
    plan: dict,
    manifest: dict,
    selected: list[dict],
    selection: str,
    *,
    command: str,
) -> Path:
    lines = [
        "# Same-day session-dynamics preflight",
        "",
        f"Generated: {plan['generated_at_utc']}",
        "",
        f"- Program: `{plan['program_id']}` ({plan['directive_id']})",
        f"- Workload: `{plan['workload_type']}`",
        f"- Target: `{manifest['target'].get('deployment_key')}` / "
        f"`{manifest['target'].get('model_id')}`",
        f"- Cells: {plan['totals']['cells']} "
        f"({plan['totals']['phases']['session_screening']} screening + "
        f"{plan['totals']['phases']['context_sentinels']} context)",
        f"- Maximum model calls: {plan['totals']['inference_attempts']}",
        f"- Cell deadline: {plan['limits']['cell_process_deadline_s']:.0f}s",
        f"- Foreground invocation deadline: "
        f"{plan['limits']['max_foreground_invocation_seconds'] / 3600.0:.2f}h",
        f"- Concurrency: C<= {plan['limits']['max_concurrent_sessions']}",
        f"- Output cap: {plan['limits']['max_completion_tokens_per_call']} tokens",
        f"- Input bounds: {plan['limits']['session_input_tokens_max']} per session, "
        f"{plan['limits']['context_input_tokens_max']} per context request",
        f"- Request timeout: {plan['limits']['request_timeout_s']:.0f}s",
        "",
        "No calendar anchor, not-before window, scheduled wait, background monitor "
        "or automatic repeat block is part of this plan.",
        "",
        "## Execution selection",
        "",
        f"`--select {selection}` selects {len(selected)} cells / "
        f"{sum(int(op['max_inference_attempts']) for op in selected)} maximum calls "
        f"for this invocation. The frozen plan remains the 30-cell matrix.",
        "",
        "## Working command",
        "",
        f"```\n{command}\n```",
        "",
        "## Release state",
        "",
        "This file was produced by the default offline preflight. "
        f"`live_released_operation_ids` = "
        f"{manifest['release']['live_released_operation_ids']}. "
        "A non-loopback run needs `--execute --authorize-live` and `--max-usd`.",
        "",
        "## Digests",
        "",
        f"- Program file SHA-256: `{manifest['program_sha256']}`",
        f"- Plan content SHA-256: `{plan['plan_sha256']}`",
        f"- Manifest content SHA-256: `{manifest['manifest_sha256']}`",
        f"- Execution identity SHA-256: `{manifest['execution_identity_sha256']}`",
        "",
        "## Context-only proposal command",
        "",
        "```",
        command.replace("--select all", "--select context"),
        "```",
        "",
        "The three context cells are `SDS-CTX-32768-C1`, `SDS-CTX-65536-C1` and "
        "`SDS-CTX-131072-C1`: 6 calls each, 18 maximum calls.",
        "",
    ]
    path = out / "PREFLIGHT.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _write_same_day_report(
    out: Path,
    summary: dict,
    analysis: dict,
    analysis_paths: dict[str, str],
) -> Path:
    totals = summary["totals"]
    lines = [
        "# Same-day session-dynamics report",
        "",
        f"Generated: {summary['generated_at_utc']}",
        "",
        f"**Status: {summary['status']}**",
        "",
        "One finite foreground invocation of "
        f"`{summary['program_id']}`. Descriptive finite-sample evidence only: no "
        "day-to-day stability, SLO, capacity limit, endpoint qualification or "
        "population-tail claim is made.",
        "",
        "## Invocation",
        "",
        f"- Target: `{summary['target'].get('deployment_key')}` / "
        f"`{summary['target'].get('model_id')}`",
        f"- Selection: `{summary['selection']}` "
        f"({len(summary['selected_operation_ids'])} cells, at most "
        f"{summary['selected_max_inference_attempts']} calls)",
        f"- Cell deadline: {summary['cell_deadline_s']:.0f}s; invocation deadline: "
        f"{summary['invocation_deadline_s'] / 3600.0:.2f}h",
        f"- Wall elapsed: {summary['wall_elapsed_s']:.3f}s",
        f"- Attempted calls: {totals['attempted_calls']}",
        f"- Stop: {summary['stop'] or 'none'}",
        "",
        "## Coverage",
        "",
        "| Cell | Phase | Outcome | Attempts | Cell status |",
        "| --- | --- | --- | ---: | --- |",
    ]
    for entry in summary["cells"]:
        lines.append(
            f"| {entry['operation_id']} | {entry['phase']} | {entry['outcome']} | "
            f"{entry.get('attempts', 0)} | {entry.get('cell_status') or ''} |"
        )
    lines.extend(
        [
            "",
            f"Selected {totals['selected']}; executed this invocation "
            f"{totals['executed_this_invocation']}; already completed "
            f"{totals['already_completed']}; preserved partial "
            f"{totals['preserved_partial']}; skipped by deadline "
            f"{totals['skipped_deadline']}; skipped after stop "
            f"{totals['skipped_after_stop']}; cancelled {totals['cancelled']}; "
            f"failed {totals['failed']}.",
            "",
            "## Analysis",
            "",
            render_markdown(analysis),
            "",
            "## Evidence",
            "",
        ]
    )
    for label, path in sorted(analysis_paths.items()):
        if label.endswith("_sha256"):
            continue
        lines.append(f"- {label}: `{path}`")
    lines.extend(
        [
            f"- run summary: `{out / 'run-summary.json'}`",
            f"- plan: `{out / 'plan.json'}`",
            f"- preflight manifest: `{out / 'manifest.json'}`",
            f"- released manifest: `{out / 'manifest.released.json'}`",
            f"- journal: `{out / 'program-state.json'}`",
            "",
            "## Limits",
            "",
            "- The same-day matrix is 30 cells / 558 maximum calls; optional scopes "
            "(C8, sustained/offered-rate, inputs above 131072, higher output caps, "
            "other deployments, AA) are not measured here.",
            "- Legacy session-dynamics-v1 temporal observations are separate and are "
            "not pooled, relabelled or extended by this report.",
            "- Missing provider telemetry is null, never zero; censored calls stay in "
            "the denominator with lower bounds.",
            "",
        ]
    )
    path = out / "REPORT.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _same_day_verify_target(manifest: dict, target: dict) -> None:
    stored = manifest.get("target") or {}
    for key in (
        "deployment_key",
        "model_id",
        "base_url",
        "protocol",
        "region",
        "credential_env_name",
    ):
        if stored.get(key) != target.get(key):
            raise SessionProgramError(
                f"target drift: manifest `{key}` is {stored.get(key)!r}, "
                f"resolved target is {target.get(key)!r}"
            )
    stored_metadata = stored.get("target_metadata_sha256")
    resolved_metadata = target.get("target_metadata_sha256")
    if stored_metadata is not None and stored_metadata != resolved_metadata:
        raise SessionProgramError(
            "target metadata hash drift; the manifest and the resolved binding "
            "must name the same committed metadata file"
        )


def _same_day_verify_output_root(plan: dict, out: Path) -> None:
    stored = Path(str(plan.get("output_root") or ""))
    if not stored.is_absolute():
        stored = Path.cwd() / stored
    if stored.resolve() != out.resolve():
        raise SessionProgramError(
            f"plan output_root `{plan.get('output_root')}` does not match --out "
            f"`{out}`; a reused preflight must run from its frozen directory"
        )


def _session_same_day(arguments: argparse.Namespace) -> int:
    program = load_program(arguments.program)
    if program.program_id != SAME_DAY_PROGRAM_ID:
        raise SessionProgramError(
            f"`session same-day` requires the same-day program "
            f"`{SAME_DAY_PROGRAM_ID}`; got `{program.program_id}`"
        )
    out = arguments.out
    target, rates = _same_day_target(arguments)
    reused = arguments.manifest is not None
    if reused:
        manifest_path = Path(arguments.manifest).resolve()
        if manifest_path.parent != out.resolve():
            raise SessionProgramError(
                f"--manifest `{manifest_path}` must live in --out `{out}`"
            )
        manifest = _load_session_manifest(manifest_path)
        plan = _load_plan_for_manifest(manifest, manifest_path)
        if manifest.get("program_id") != program.program_id:
            raise SessionProgramError(
                "the reused manifest belongs to a different program"
            )
        verify_prepared_manifest(
            manifest, plan, require_unreleased=False, check_sources=True
        )
        _same_day_verify_target(manifest, target)
        _same_day_verify_output_root(plan, out)
    else:
        out.mkdir(parents=True, exist_ok=True)
        plan = build_plan(
            program, output_dir=out, release_nonce=arguments.release_nonce
        )
        manifest = build_manifest(
            plan,
            manifest_path=out / "manifest.json",
            plan_path=Path("plan.json"),
            target=target,
            operational_rates=rates,  # type: ignore[arg-type]
        )
        verify_prepared_manifest(
            manifest, plan, require_unreleased=True, check_sources=True
        )
    selected = _same_day_selection(plan, arguments.select)
    selected_attempts = sum(
        int(operation["max_inference_attempts"]) for operation in selected
    )
    selected_envelope = round(
        sum(
            _cell_usd_envelope(operation, rates, plan["limits"])
            for operation in selected
        ),
        6,
    )
    cell_deadline = _same_day_cell_deadline(plan, arguments)
    invocation_deadline = _same_day_invocation_deadline(plan, arguments)
    loopback = is_loopback(str(target.get("base_url") or ""))

    if not reused:
        write_json_atomic(out / "plan.json", plan)
        write_json_atomic(out / "manifest.json", manifest)
    command = (
        "endpoint-bench session same-day "
        + (
            f"--manifest {Path(arguments.manifest)} --out {out} "
            if reused
            else (
                f"--program {arguments.program} --out {out} "
                f"--release-nonce {arguments.release_nonce} "
            )
        )
        + f"--select {arguments.select}"
    )
    preflight_path = _write_same_day_preflight(
        out, plan, manifest, selected, arguments.select, command=command
    )

    if not arguments.execute:
        _json_print(
            {
                "mode": "offline_preflight",
                "command": command,
                "program_id": plan["program_id"],
                "target": manifest["target"].get("deployment_key"),
                "model_id": manifest["target"].get("model_id"),
                "cells": plan["totals"]["cells"],
                "max_inference_attempts": plan["totals"]["inference_attempts"],
                "selected_operation_ids": [
                    operation["operation_id"] for operation in selected
                ],
                "selected_max_inference_attempts": selected_attempts,
                "cell_deadline_s": cell_deadline,
                "invocation_deadline_s": invocation_deadline,
                "operational_usd_envelope": manifest["totals"][
                    "operational_usd_envelope"
                ],
                "selected_usd_envelope": selected_envelope,
                "plan_sha256": plan["plan_sha256"],
                "manifest_sha256": manifest["manifest_sha256"],
                "execution_identity_sha256": manifest[
                    "execution_identity_sha256"
                ],
                "live_released_operation_ids": manifest["release"][
                    "live_released_operation_ids"
                ],
                "preflight_path": str(preflight_path),
                "plan_path": str(out / "plan.json"),
                "manifest_path": str(out / "manifest.json"),
                "inference": {
                    "executed": False,
                    "network_requests": 0,
                    "credentials_sent": False,
                },
            }
        )
        return 0

    _same_day_live_guards(
        arguments,
        target,
        selected_attempts,
        selected_envelope,
        plan,
        loopback=loopback,
    )

    released_manifest = json.loads(json.dumps(manifest))
    released_manifest["release"]["live_released_operation_ids"] = [
        operation["operation_id"] for operation in selected
    ]
    released_manifest["release"]["release_anchor_utc"] = None
    released_manifest["execution_identity_sha256"] = (
        manifest_execution_identity_sha256(released_manifest)
    )
    released_manifest["manifest_sha256"] = manifest_content_sha256(
        released_manifest
    )
    validate_manifest_document(released_manifest)
    verify_manifest_against_plan(released_manifest, plan)
    released_path = out / "manifest.released.json"
    write_json_atomic(released_path, released_manifest)
    released_file_sha256 = hashlib.sha256(
        released_path.read_bytes()
    ).hexdigest()
    write_json_atomic(
        out / "release-record.json",
        {
            "schema_version": "session-same-day-release/1.0.0",
            "generated_at_utc": utc_now(),
            "program_id": plan["program_id"],
            "plan_sha256": plan["plan_sha256"],
            "manifest_sha256": released_manifest["manifest_sha256"],
            "released_manifest_file_sha256": released_file_sha256,
            "execution_mode": "immediate_foreground",
            "selected_operation_ids": [
                operation["operation_id"] for operation in selected
            ],
            "selected_max_inference_attempts": selected_attempts,
            "execution_authorized": bool(arguments.authorize_live),
            "loopback": loopback,
            "credential_env_name": target.get("credential_env_name"),
            "note": (
                "Release record only. No credential value is stored. The released "
                "manifest differs from the preflight only in the two documented "
                "release-mutable fields."
            ),
        },
    )

    canonical_state = (out / "program-state.json").resolve()
    state_path = (
        Path(arguments.state).resolve()
        if arguments.state is not None
        else canonical_state
    )
    if state_path != canonical_state:
        raise SessionProgramError(
            f"--state `{state_path}` would bypass no-replay accounting; the "
            f"canonical journal is `{canonical_state}`"
        )
    credential_env_name = target.get("credential_env_name")
    api_key = (
        None
        if loopback
        else os.environ[str(credential_env_name)]
    )
    session_target = SessionTarget(
        base_url=str(target.get("base_url")),
        model_id=str(target.get("model_id")),
        api_key=api_key,
        timeout_s=float(manifest["timeouts"]["request_timeout_s"]),
        max_output_tokens=int(manifest["limits"]["max_completion_tokens_per_call"]),
        streaming=True,
    )
    with JsonJournal(
        state_path,
        program_id=manifest["program_id"],
        plan_sha256=manifest["plan_sha256"],
    ) as journal:
        run = _same_day_execute(
            journal,
            plan,
            selected,
            session_target,
            out,
            cell_deadline=cell_deadline,
            invocation_deadline=invocation_deadline,
            idle_seconds=arguments.idle_seconds,
        )

    analysis = analyze_cells(out, manifest=manifest, require_complete=False)
    analysis_paths = write_analysis(out, analysis)
    summary = _same_day_summary(
        run,
        plan,
        manifest,
        released_manifest,
        selection=arguments.select,
        selected=selected,
        cell_deadline=cell_deadline,
        invocation_deadline=invocation_deadline,
        loopback=loopback,
        authorized_live=bool(arguments.authorize_live),
    )
    write_json_atomic(out / "run-summary.json", summary)
    report_path = _write_same_day_report(
        out, summary, analysis, analysis_paths
    )
    _json_print(
        {
            "mode": "foreground_same_day",
            "status": summary["status"],
            "selection": summary["selection"],
            "cells": summary["totals"]["selected"],
            "executed_this_invocation": summary["totals"][
                "executed_this_invocation"
            ],
            "already_completed": summary["totals"]["already_completed"],
            "skipped_deadline": summary["totals"]["skipped_deadline"],
            "skipped_after_stop": summary["totals"]["skipped_after_stop"],
            "attempted_calls": summary["totals"]["attempted_calls"],
            "stop": summary["stop"],
            "run_summary_path": str(out / "run-summary.json"),
            "report_path": str(report_path),
            **analysis_paths,
            "inference": summary["inference"],
        }
    )
    if run["cancelled"]:
        return 130
    return 0 if summary["status"] == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
