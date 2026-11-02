from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import replace
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
from .trials import (
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

    return parser


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

        parser.error(f"unhandled command `{arguments.command}`")
    except (RegistryError, ProfileError, AiperfError, ReportError) as error:
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
    run_id = f"run-{preview_sha[:12]}"
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


if __name__ == "__main__":
    raise SystemExit(main())
