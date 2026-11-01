from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .network_guard import is_loopback, local_environment
from .sanitize import collect_secret_values, redact_secrets
from .trials import (
    HttpAttempt,
    LifecycleState,
    OutcomeState,
    RunCounters,
    TokenAccounting,
    Trial,
    count_trials,
    outcome_summary,
    utc_now,
)

#: Verified AIPerf release. Any other version is rejected rather than guessed at.
SUPPORTED_AIPERF_VERSION = "0.13.0"
#: Verified summary-export schema major. AIPerf writes schema_version "1.4".
SUPPORTED_SUMMARY_SCHEMA_MAJOR = 1
#: AIPerf config envelope version for the envelope/body shape.
SUPPORTED_CONFIG_SCHEMA_VERSION = "2.0"

SUMMARY_FILE = "profile_export_aiperf.json"
RECORDS_FILE = "profile_export.jsonl"


class AiperfError(RuntimeError):
    """Integration failure: the harness could not run or could not be trusted."""


class AiperfSchemaError(AiperfError):
    """Parsed artifacts do not match the pinned AIPerf version or schema."""


class AiperfBenchmarkFailure(AiperfError):
    """The run executed but produced no successful responses."""


@dataclass(frozen=True)
class ClientProfile:
    """Explicit, recorded client configuration.

    AIPerf sends `X-Session-Affinity` by default, which can pin a session to one
    replica and improve cache locality. That makes AIPerf's default a distinct
    client identity from a normal client baseline, so the two are never merged.
    """

    name: str
    session_affinity: bool
    connection_reuse: str = "pooled"
    request_timeout_s: float = 60.0
    tokenizer: str = "builtin"
    random_seed: int = 42
    enable_server_metrics: bool = False
    enable_gpu_telemetry: bool = False
    wait_for_model_timeout_s: float = 0.0

    def env(self) -> dict[str, str]:
        """Transport environment that must accompany every invocation."""
        return {
            "AIPERF_HTTP_X_SESSION_AFFINITY_FROM_CORRELATION_ID": (
                "true" if self.session_affinity else "false"
            )
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "session_affinity": self.session_affinity,
            "connection_reuse": self.connection_reuse,
            "request_timeout_s": self.request_timeout_s,
            "tokenizer": self.tokenizer,
            "random_seed": self.random_seed,
            "enable_server_metrics": self.enable_server_metrics,
            "enable_gpu_telemetry": self.enable_gpu_telemetry,
            "wait_for_model_timeout_s": self.wait_for_model_timeout_s,
            "sends_affinity_header": self.session_affinity,
        }

    def identity(self) -> str:
        material = json.dumps(self.to_dict(), sort_keys=True).encode("utf-8")
        return hashlib.sha256(material).hexdigest()[:16]


BASELINE_CLIENT = ClientProfile(name="baseline-normal-client", session_affinity=False)
AFFINITY_CLIENT = ClientProfile(name="affinity-enabled-experiment", session_affinity=True)


# -- configuration generation ------------------------------------------------


def build_benchmark_config(
    *,
    model_id: str,
    base_url: str,
    client: ClientProfile,
    requests: int,
    concurrency: int,
    input_tokens: int,
    output_tokens: int,
    artifact_dir: Path,
    credential_env_name: str | None,
    streaming: bool = True,
    capture_raw: bool = False,
) -> dict[str, Any]:
    """Build an AIPerf YAML envelope as a plain mapping.

    Credentials appear only as a `${VAR}` reference. The literal secret never
    enters this document, so the generated config is safe to hash and store.
    """
    if requests <= 0 or concurrency <= 0:
        raise AiperfError("requests and concurrency must be positive")
    if concurrency > requests:
        raise AiperfError("concurrency cannot exceed requests")

    endpoint: dict[str, Any] = {
        "url": base_url.rstrip("/"),
        "type": "chat",
        "streaming": streaming,
        "timeout": client.request_timeout_s,
        "connection_reuse": client.connection_reuse,
    }
    if credential_env_name:
        endpoint["api_key"] = f"${{{credential_env_name}}}"

    return {
        "schema_version": SUPPORTED_CONFIG_SCHEMA_VERSION,
        "random_seed": client.random_seed,
        "benchmark": {
            "model": model_id,
            "endpoint": endpoint,
            # Explicit list form, not the singular `dataset:` shorthand. Sweep
            # expansion adds `datasets:` alongside the shorthand, and AIPerf
            # rejects the two together, so the shorthand is unsafe in any config
            # that may be swept.
            "datasets": [
                {
                    "name": "main",
                    "type": "synthetic",
                    "entries": requests,
                    "prompts": {"isl": input_tokens, "osl": output_tokens},
                }
            ],
            "phases": {
                "type": "concurrency",
                "concurrency": concurrency,
                "requests": requests,
            },
            "tokenizer": {"name": client.tokenizer},
            "gpu_telemetry": {"enabled": client.enable_gpu_telemetry},
            "server_metrics": {"enabled": client.enable_server_metrics},
            "artifacts": {
                "dir": str(artifact_dir),
                "records": ["jsonl"],
                "raw": capture_raw,
                "auto_plot": False,
            },
        },
    }


def dump_yaml(value: Any, indent: int = 0) -> str:
    """Deterministic YAML emitter for the subset this project generates.

    Scalars are rendered JSON-style, which is valid YAML. Supports nested
    mappings and block sequences of mappings, which is all the generated AIPerf
    envelope needs. Avoids adding a YAML dependency for a shape we control.
    """
    if not isinstance(value, dict):
        raise AiperfError("YAML document root must be a mapping")
    return _dump_mapping(value, indent)


def _dump_mapping(value: dict[str, Any], indent: int) -> str:
    pad = "  " * indent
    lines: list[str] = []
    for key, item in value.items():
        lines.append(_dump_key(pad, indent, key, item))
    return "\n".join(lines)


def _dump_key(pad: str, indent: int, key: str, item: Any) -> str:
    if isinstance(item, dict):
        if not item:
            return f"{pad}{key}: {{}}"
        return f"{pad}{key}:\n{_dump_mapping(item, indent + 1)}"
    if isinstance(item, list) and item and all(isinstance(entry, dict) for entry in item):
        blocks: list[str] = []
        for entry in item:
            body = _dump_mapping(entry, indent + 2).split("\n")
            blocks.append(f"{pad}  - {body[0].lstrip()}")
            blocks.extend(body[1:])
        return f"{pad}{key}:\n" + "\n".join(blocks)
    return f"{pad}{key}: {_scalar(item)}"


def _scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value) if isinstance(value, float) else str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_scalar(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{}"
    return json.dumps(str(value))


def write_config(path: Path, config: dict[str, Any]) -> str:
    """Write the config and return its SHA-256."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = dump_yaml(config) + "\n"
    path.write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# -- invocation --------------------------------------------------------------


def build_argv(
    *,
    executable: str,
    config_path: Path,
    artifact_dir: Path,
    client: ClientProfile,
    extra_argv: tuple[str, ...] = (),
) -> list[str]:
    """Build the subprocess argv, refusing any argument that could carry a secret."""
    if client.wait_for_model_timeout_s:
        raise AiperfError(
            "readiness probing is disabled by invariant: all traffic must be accounted for"
        )

    argv = [
        executable,
        "profile",
        "--config",
        str(config_path),
        "--output-artifact-dir",
        str(artifact_dir),
        "--wait-for-model-timeout",
        "0",
        "--no-auto-plot",
    ]
    argv.extend(extra_argv)

    forbidden = ("--api-key",)
    for argument in argv:
        if argument in forbidden or argument.startswith("--api-key="):
            raise AiperfError(
                "refusing to pass a credential on the command line; "
                "use the generated config's ${VAR} reference instead"
            )
    return argv


@dataclass
class TerminationReport:
    """How a subprocess run ended, preserved as evidence.

    Distinguishes a clean exit, a deadline kill, and a cancellation so a stalled
    engine is never reported as a benchmark result.
    """

    reason: str = "completed"
    sigterm_sent: bool = False
    sigkill_sent: bool = False
    process_group_gone: bool = True
    reaped: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "sigterm_sent": self.sigterm_sent,
            "sigkill_sent": self.sigkill_sent,
            "process_group_gone": self.process_group_gone,
            "reaped": self.reaped,
        }


@dataclass
class AiperfRun:
    """Outcome of one AIPerf subprocess invocation."""

    exit_code: int | None
    timed_out: bool
    duration_ms: float
    argv: list[str]
    artifact_dir: Path
    stdout_path: Path
    stderr_path: Path
    client_identity: str
    config_sha256: str
    environment_names: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    termination: TerminationReport = field(default_factory=TerminationReport)

    @property
    def completed(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    @property
    def cancelled(self) -> bool:
        return self.termination.reason == "cancelled"

    def to_dict(self) -> dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "cancelled": self.cancelled,
            "duration_ms": self.duration_ms,
            "argv": self.argv,
            "artifact_dir": str(self.artifact_dir),
            "stdout_path": str(self.stdout_path),
            "stderr_path": str(self.stderr_path),
            "client_identity": self.client_identity,
            "config_sha256": self.config_sha256,
            "environment_names": self.environment_names,
            "termination": self.termination.to_dict(),
            "notes": list(self.notes),
        }


def _process_group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def terminate_process_group(
    process: subprocess.Popen[str],
    *,
    grace_s: float = 5.0,
) -> tuple[bool, bool, bool, bool]:
    """Terminate an isolated process group politely, then forcefully.

    Returns (sigterm_sent, sigkill_sent, group_gone, reaped).

    AIPerf runs a controller plus worker processes. Killing only the parent would
    leave orphans still generating traffic, which would corrupt both accounting
    and any later run against the same endpoint, so the whole group is signalled.
    """
    try:
        pgid = os.getpgid(process.pid)
    except ProcessLookupError:
        return False, False, True, process.poll() is not None

    sigterm_sent = False
    sigkill_sent = False

    try:
        os.killpg(pgid, signal.SIGTERM)
        sigterm_sent = True
    except ProcessLookupError:
        return False, False, True, process.poll() is not None

    try:
        process.wait(timeout=grace_s)
    except subprocess.TimeoutExpired:
        pass

    # A cooperative parent can exit on SIGTERM while worker children survive it.
    # Group liveness is therefore checked independently of the parent's status.
    if _process_group_alive(pgid):
        try:
            os.killpg(pgid, signal.SIGKILL)
            sigkill_sent = True
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=grace_s)
        except subprocess.TimeoutExpired:
            pass
        # Give the kernel a moment to reap the remaining group members.
        for _ in range(40):
            if not _process_group_alive(pgid):
                break
            time.sleep(0.05)

    reaped = process.poll() is not None
    return sigterm_sent, sigkill_sent, not _process_group_alive(pgid), reaped


def _wait_for_exit(
    process: subprocess.Popen[str],
    *,
    timeout_s: float,
    cancel_event: Any | None = None,
    poll_interval_s: float = 0.05,
) -> str:
    """Wait for exit, honouring a deadline and an optional cancellation signal.

    Returns "completed", "deadline", or "cancelled".
    """
    deadline = time.monotonic() + timeout_s
    while True:
        if process.poll() is not None:
            return "completed"
        if cancel_event is not None and cancel_event.is_set():
            return "cancelled"
        if time.monotonic() >= deadline:
            return "deadline"
        time.sleep(poll_interval_s)


def run_aiperf(
    *,
    executable: str,
    config_path: Path,
    artifact_dir: Path,
    client: ClientProfile,
    secrets: dict[str, str] | None = None,
    process_timeout_s: float = 900.0,
    extra_argv: tuple[str, ...] = (),
    config_sha256: str = "",
    cancel_event: Any | None = None,
    termination_grace_s: float = 5.0,
    target_url: str | None = None,
) -> AiperfRun:
    """Run AIPerf with an explicit per-request timeout and process deadline.

    A stalled process is signalled politely, escalated if it ignores that, and
    recorded as terminated rather than allowed to hang the harness. Partial
    output survives because stdio goes straight to files.
    """
    if process_timeout_s <= 0:
        raise AiperfError("process timeout must be positive")

    artifact_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = artifact_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = logs_dir / "endpoint-bench-aiperf-stdout.log"
    stderr_path = logs_dir / "endpoint-bench-aiperf-stderr.log"

    argv = build_argv(
        executable=executable,
        config_path=config_path,
        artifact_dir=artifact_dir,
        client=client,
        extra_argv=extra_argv,
    )

    environment = dict(os.environ)
    secrets = dict(secrets or {})
    environment.update(secrets)
    environment.update(client.env())

    # Defence in depth: AIPerf must not consult proxy settings, and a local
    # target must not be reachable through one. Proxies are removed, not
    # overridden, so a stale ALL_PROXY cannot be honoured by the transport.
    environment["AIPERF_HTTP_TRUST_ENV"] = "false"
    if target_url is not None and is_loopback(target_url):
        environment = local_environment(environment)
    # AIPerf must never reach the network for a local tokenizer or dataset.
    environment.setdefault("HF_HUB_OFFLINE", "1")
    environment.setdefault("TRANSFORMERS_OFFLINE", "1")

    secret_values = list(secrets.values()) + collect_secret_values(environment)
    started = time.perf_counter()
    termination = TerminationReport()

    # AIPerf spawns worker subprocesses that inherit its stdio. Capturing through
    # pipes would make a killed parent leave those children holding the write end,
    # so `communicate()` would block forever. Writing straight to files keeps
    # partial output on disk and lets the deadline be enforced reliably.
    with (
        stdout_path.open("w", encoding="utf-8") as stdout_file,
        stderr_path.open("w", encoding="utf-8") as stderr_file,
    ):
        process = subprocess.Popen(  # noqa: S603 - argv is built above, never a shell string
            argv,
            stdout=stdout_file,
            stderr=stderr_file,
            env=environment,
            text=True,
            start_new_session=True,
        )
        stop_reason = _wait_for_exit(
            process, timeout_s=process_timeout_s, cancel_event=cancel_event
        )
        exit_code = process.returncode if stop_reason == "completed" else None
        if stop_reason != "completed":
            sigterm, sigkill, gone, reaped = terminate_process_group(
                process, grace_s=termination_grace_s
            )
            exit_code = process.returncode
            termination = TerminationReport(
                reason=stop_reason,
                sigterm_sent=sigterm,
                sigkill_sent=sigkill,
                process_group_gone=gone,
                reaped=reaped,
            )

    duration_ms = (time.perf_counter() - started) * 1000.0

    for path in (stdout_path, stderr_path):
        path.write_text(
            redact_secrets(path.read_text(encoding="utf-8", errors="replace"), secret_values),
            encoding="utf-8",
        )

    notes: list[str] = []
    if stop_reason == "deadline":
        notes.append(f"process exceeded deadline of {process_timeout_s}s and was terminated")
    elif stop_reason == "cancelled":
        notes.append("process was cancelled and terminated")
    if termination.sigkill_sent:
        notes.append("process ignored SIGTERM and required SIGKILL")
    if not termination.process_group_gone:
        notes.append("WARNING: process group still alive after termination; possible orphan workers")

    return AiperfRun(
        exit_code=exit_code,
        timed_out=termination.reason == "deadline",
        duration_ms=duration_ms,
        argv=argv,
        artifact_dir=artifact_dir,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        client_identity=client.identity(),
        config_sha256=config_sha256,
        environment_names=sorted(secrets) + sorted(client.env()),
        notes=notes,
        termination=termination,
    )


# -- artifact parsing --------------------------------------------------------


@dataclass
class AiperfResults:
    """Parsed AIPerf evidence with explicit provenance."""

    aiperf_version: str | None
    summary_schema_version: str | None
    schema_validated: bool
    summary: dict[str, Any] | None
    trials: list[Trial]
    counters: RunCounters
    benchmark_failed: bool
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "aiperf_version": self.aiperf_version,
            "summary_schema_version": self.summary_schema_version,
            "schema_validated": self.schema_validated,
            "benchmark_failed": self.benchmark_failed,
            "counters": self.counters.to_dict(),
            "outcomes": outcome_summary(self.trials),
            "notes": list(self.notes),
        }


def _validate_summary_identity(summary: dict[str, Any]) -> tuple[str, str]:
    version = summary.get("aiperf_version")
    if version != SUPPORTED_AIPERF_VERSION:
        raise AiperfSchemaError(
            f"unsupported AIPerf version `{version}`; pinned to `{SUPPORTED_AIPERF_VERSION}`"
        )
    schema = summary.get("schema_version")
    if not isinstance(schema, str) or not schema:
        raise AiperfSchemaError("summary export has no schema_version")
    try:
        major = int(schema.split(".")[0])
    except ValueError as error:
        raise AiperfSchemaError(f"unparseable summary schema_version `{schema}`") from error
    if major != SUPPORTED_SUMMARY_SCHEMA_MAJOR:
        raise AiperfSchemaError(
            f"unsupported summary schema `{schema}`; expected major "
            f"{SUPPORTED_SUMMARY_SCHEMA_MAJOR}"
        )
    return str(version), schema


def _metric(metrics: dict[str, Any], name: str) -> Any:
    entry = metrics.get(name)
    if isinstance(entry, dict):
        return entry.get("value")
    return None


def _classify_error(error: dict[str, Any]) -> tuple[OutcomeState, str]:
    error_type = str(error.get("type") or "")
    lowered = error_type.lower()
    if "timeout" in lowered:
        return OutcomeState.TIMEOUT, error_type
    if "disconnect" in lowered or "reset" in lowered or "brokenpipe" in lowered:
        return OutcomeState.STREAM_INTERRUPTED, error_type
    if "connector" in lowered or "connect" in lowered:
        return OutcomeState.CLIENT_ERROR, error_type
    return OutcomeState.UNKNOWN, error_type or "unclassified"


def _trial_from_record(
    record: dict[str, Any],
    *,
    deployment_key: str,
    profile_name: str,
    index: int,
    target_input_tokens: int | None = None,
    token_tolerance_pct: float = 10.0,
) -> Trial:
    metadata = record.get("metadata") or {}
    metrics = record.get("metrics") or {}
    trial = Trial(
        trial_id=f"aiperf-{index:04d}",
        deployment_key=deployment_key,
        profile_name=profile_name,
    )
    trial.observe(LifecycleState.PLANNED)

    if metadata.get("context_overflow_skip"):
        trial.observe(LifecycleState.SKIPPED)
        trial.outcome = OutcomeState.CONTEXT_REJECTED
        trial.outcome_detail = "context_overflow_skip"
        return trial

    if metadata.get("was_cancelled"):
        trial.observe(LifecycleState.STARTED)
        trial.observe(LifecycleState.CANCELLED)
        trial.outcome = OutcomeState.UNKNOWN
        trial.outcome_detail = "cancelled"
        trial.attempts.append(HttpAttempt(attempt_index=1, started_at=utc_now()))
        return trial

    trial.observe(LifecycleState.STARTED)
    trial.attempts.append(HttpAttempt(attempt_index=1, started_at=utc_now(), ended_at=utc_now()))

    error = record.get("error")
    if isinstance(error, dict):
        outcome, detail = _classify_error(error)
        trial.outcome = outcome
        trial.outcome_detail = detail
        return trial

    trial.observe(LifecycleState.TRANSPORT_COMPLETED)
    trial.observe(LifecycleState.PROTOCOL_COMPLETED)
    trial.token_accounting = TokenAccounting(
        target_input_tokens=target_input_tokens,
        client_estimated_input_tokens=_metric(metrics, "input_sequence_length"),
        provider_input_tokens=_metric(metrics, "usage_prompt_tokens"),
        provider_output_tokens=_metric(metrics, "usage_completion_tokens"),
        observed_output_tokens=_metric(metrics, "output_sequence_length"),
        tolerance_pct=token_tolerance_pct,
    )
    trial.ttft_ms = _metric(metrics, "time_to_first_token")
    trial.duration_ms = _metric(metrics, "request_latency")
    trial.input_tokens = _metric(metrics, "usage_prompt_tokens")
    trial.output_tokens = _metric(metrics, "usage_completion_tokens")
    trial.total_tokens = _metric(metrics, "usage_total_tokens")
    trial.outcome = OutcomeState.SUCCESS
    trial.outcome_detail = "completed"

    input_diff = _metric(metrics, "usage_prompt_tokens_diff_pct")
    if isinstance(input_diff, (int, float)) and input_diff >= 10:
        trial.notes.append(
            f"client-vs-provider input token count differs by {input_diff:.1f}%; "
            "do not make token-normalized claims from the client count"
        )
    return trial


def parse_artifacts(
    artifact_dir: Path,
    *,
    deployment_key: str,
    profile_name: str,
    target_input_tokens: int | None = None,
    token_tolerance_pct: float = 10.0,
) -> AiperfResults:
    """Parse AIPerf artifacts, validating version and schema before trusting them."""
    summary_path = artifact_dir / SUMMARY_FILE
    records_path = artifact_dir / RECORDS_FILE
    notes: list[str] = []

    summary: dict[str, Any] | None = None
    version: str | None = None
    schema_version: str | None = None
    schema_validated = False

    if summary_path.is_file():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise AiperfSchemaError(f"summary export is not valid JSON: {error}") from error
        version, schema_version = _validate_summary_identity(summary)
        schema_validated = True
    else:
        notes.append(
            "summary export absent; AIPerf writes none when every request fails. "
            "Only per-request error evidence is available."
        )

    records: list[dict[str, Any]] = []
    if records_path.is_file():
        for line in records_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise AiperfSchemaError(f"records export is not valid JSONL: {error}") from error
    elif summary is None:
        raise AiperfError(
            f"no AIPerf artifacts found in `{artifact_dir}`; integration failure, not a result"
        )

    trials = [
        _trial_from_record(
            record,
            deployment_key=deployment_key,
            profile_name=profile_name,
            index=index,
            target_input_tokens=target_input_tokens,
            token_tolerance_pct=token_tolerance_pct,
        )
        for index, record in enumerate(records)
    ]
    counters = count_trials(trials)
    benchmark_failed = summary is None

    return AiperfResults(
        aiperf_version=version,
        summary_schema_version=schema_version,
        schema_validated=schema_validated,
        summary=summary,
        trials=trials,
        counters=counters,
        benchmark_failed=benchmark_failed,
        notes=notes,
    )
