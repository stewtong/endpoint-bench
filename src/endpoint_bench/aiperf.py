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
    ACCOUNTING_CLIENT_TOKENIZER,
    ACCOUNTING_PROVIDER_USAGE,
    EVIDENCE_FINISH_REASON_UNOBSERVED,
    EVIDENCE_HTTP_STATUS_UNOBSERVED,
    EVIDENCE_USAGE_UNOBSERVED,
    FINISH_ABSENT,
    FINISH_CEILING,
    FINISH_CONTENT_FILTER,
    FINISH_UNKNOWN,
    HttpAttempt,
    LifecycleState,
    OutcomeState,
    ProviderTelemetry,
    RunCounters,
    TokenAccounting,
    Trial,
    classify_finish_reason,
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
RAW_RECORDS_FILE = "profile_export_raw.jsonl"


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
    #: Account tokens from provider `usage` instead of client tokenization.
    #: This makes the throughput denominator authoritative for the served model,
    #: but it stops verifying that the delivered workload matched the target size.
    use_server_token_count: bool = False

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
            "use_server_token_count": self.use_server_token_count,
            "token_accounting_source": (
                "provider_usage" if self.use_server_token_count else "client_tokenizer"
            ),
            "sends_affinity_header": self.session_affinity,
        }

    def identity(self) -> str:
        material = json.dumps(self.to_dict(), sort_keys=True).encode("utf-8")
        return hashlib.sha256(material).hexdigest()[:16]


BASELINE_CLIENT = ClientProfile(name="baseline-normal-client", session_affinity=False)
#: Same client, but token counts come from provider usage rather than the built-in
#: tokenizer. Required whenever the client tokenizer does not match the served model.
BASELINE_SERVER_ACCOUNTING = ClientProfile(
    name="baseline-normal-client-server-accounting",
    session_affinity=False,
    use_server_token_count=True,
)
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
    trace_file: Path | None = None,
    trace_type: str = "mooncake-trace",
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
        # Registry base URLs follow the OpenAI convention and end in `/v1`.
        # AIPerf appends the endpoint path itself (`/v1/chat/completions` for
        # chat), so passing the `/v1` base through would duplicate the segment.
        "url": _aiperf_url(base_url),
        "type": "chat",
        "streaming": streaming,
        "timeout": client.request_timeout_s,
        "connection_reuse": client.connection_reuse,
        "use_server_token_count": client.use_server_token_count,
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
            "datasets": [_dataset_spec(requests, input_tokens, output_tokens, trace_file, trace_type)],
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


def _dataset_spec(
    requests: int,
    input_tokens: int,
    output_tokens: int,
    trace_file: Path | None,
    trace_type: str,
) -> dict[str, Any]:
    """Dataset block: synthetic prompts, or recorded trace replay.

    Trace replay is not synthetic traffic and must never be described as live
    agent execution. The workload label in the report records the distinction.
    """
    if trace_file is None:
        return {
            "name": "main",
            "type": "synthetic",
            "entries": requests,
            "prompts": {"isl": input_tokens, "osl": output_tokens},
        }
    # AIPerf's dataset discriminator is `type: file`; the trace format lives in
    # `format:` and uses an underscore enum value (`mooncake_trace`).
    return {
        "name": "main",
        "type": "file",
        "path": str(Path(trace_file).resolve()),
        "format": trace_type.replace("-", "_"),
    }


def _aiperf_url(base_url: str) -> str:
    """Convert an OpenAI-style base URL into the server root AIPerf expects."""
    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        root = root[: -len("/v1")]
    return root


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


def _classify_http_status(
    status: int, error: dict[str, Any] | None, body_hint: str | None
) -> tuple[OutcomeState, str]:
    """Map an observed non-2xx status onto an outcome.

    A non-2xx response is never a success, whatever its body looked like. The
    status is the authority; the body only refines the detail string.
    """
    detail_parts = [f"http_{status}"]
    error_type = str((error or {}).get("type") or "").strip()
    if error_type:
        detail_parts.append(error_type)
    hint = (body_hint or "").lower()
    if status == 429:
        return OutcomeState.RATE_LIMITED, " ".join(detail_parts)
    if status in (413, 422) or (
        status == 400 and ("context" in hint or "too long" in hint or "maximum" in hint)
    ):
        return OutcomeState.CONTEXT_REJECTED, " ".join(detail_parts)
    if status >= 500:
        return OutcomeState.SERVER_ERROR, " ".join(detail_parts)
    if 400 <= status < 500:
        return OutcomeState.CLIENT_ERROR, " ".join(detail_parts)
    # 1xx/3xx are not terminal responses a benchmark can score.
    return OutcomeState.PROTOCOL_FAILURE, " ".join(detail_parts)


@dataclass(frozen=True)
class RawEvidence:
    """Per-request facts recoverable only from the raw export.

    AIPerf's normalized records carry metrics but not the HTTP status, not the
    terminal `finish_reason`, and not the provider's cache-write count. All three
    are needed to decide whether a trial succeeded, whether its latency is
    censored, and what the provider actually billed.
    """

    http_status: int | None = None
    finish_reason: str | None = None
    finish_class: str = FINISH_ABSENT
    #: The stream carried an explicit `[DONE]` sentinel.
    saw_done: bool = False
    data_frames: int = 0
    malformed_frames: int = 0
    #: Characters of assistant `content`, excluding reasoning text.
    content_chars: int = 0
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None
    requested_max_completion_tokens: int | None = None
    error_type: str | None = None
    error_message: str | None = None
    #: First bytes of an error body, for classifying 4xx without storing a secret.
    body_hint: str | None = None

    @property
    def usage_observed(self) -> bool:
        return any(
            value is not None
            for value in (self.cache_read_tokens, self.cache_write_tokens, self.reasoning_tokens)
        )

    @property
    def stream_terminated(self) -> bool:
        """A terminal finish reason or the protocol's done sentinel was seen."""
        return self.finish_class != FINISH_ABSENT or self.saw_done


def _merge_usage(target: dict[str, Any], usage: dict[str, Any]) -> None:
    """Keep the last observed non-null value for each usage detail.

    Streaming providers repeat `usage` on many chunks and only populate the
    detail objects on some of them, so a single-frame read would lose fields.
    Last-writer-wins per key preserves the final accounting without inventing
    values that were never sent.
    """
    if isinstance(usage.get("prompt_tokens_details"), dict):
        details = usage["prompt_tokens_details"]
        for key, field_name in (
            ("cached_tokens", "cache_read"),
            ("created_cache_tokens", "cache_write"),
        ):
            value = details.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                target[field_name] = value
    if isinstance(usage.get("completion_tokens_details"), dict):
        value = usage["completion_tokens_details"].get("reasoning_tokens")
        if isinstance(value, int) and not isinstance(value, bool):
            target["reasoning"] = value
    # Some providers report reasoning at the top level instead.
    value = usage.get("reasoning_tokens")
    if isinstance(value, int) and not isinstance(value, bool):
        target["reasoning"] = value


def _parse_raw_record(raw: dict[str, Any]) -> RawEvidence:
    status = raw.get("status")
    http_status = status if isinstance(status, int) and not isinstance(status, bool) else None

    payload = raw.get("payload")
    requested_max: int | None = None
    if isinstance(payload, dict):
        for key in ("max_completion_tokens", "max_tokens"):
            value = payload.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                requested_max = value
                break

    finish_reason: str | None = None
    saw_done = False
    data_frames = 0
    malformed = 0
    content_chars = 0
    usage: dict[str, Any] = {}
    body_lines: list[str] = []

    responses = raw.get("responses")
    if isinstance(responses, list):
        for group in responses:
            if not isinstance(group, dict):
                continue
            for packet in group.get("packets") or []:
                if not isinstance(packet, dict) or packet.get("name") != "data":
                    continue
                value = packet.get("value")
                if not isinstance(value, str):
                    continue
                if value.strip() == "[DONE]":
                    saw_done = True
                    continue
                data_frames += 1
                try:
                    document = json.loads(value)
                except json.JSONDecodeError:
                    malformed += 1
                    if len(body_lines) < 4:
                        body_lines.append(value)
                    continue
                if not isinstance(document, dict):
                    continue
                choices = document.get("choices")
                if isinstance(choices, list) and choices:
                    choice = choices[0]
                    if isinstance(choice, dict):
                        reason = choice.get("finish_reason")
                        if isinstance(reason, str) and reason:
                            finish_reason = reason
                        delta = choice.get("delta")
                        if isinstance(delta, dict):
                            content = delta.get("content")
                            if isinstance(content, str):
                                content_chars += len(content)
                if isinstance(document.get("usage"), dict):
                    _merge_usage(usage, document["usage"])
                if isinstance(document.get("error"), dict):
                    error = document["error"]
                    body_lines.append(str(error.get("message") or error.get("type") or ""))

    error_type = None
    error_message = None
    error = raw.get("error")
    if isinstance(error, dict):
        error_type = str(error.get("type") or "") or None
        error_message = str(error.get("message") or "") or None

    hint = " ".join(part for part in body_lines if part)[:400] or None

    return RawEvidence(
        http_status=http_status,
        finish_reason=finish_reason,
        finish_class=classify_finish_reason(finish_reason),
        saw_done=saw_done,
        data_frames=data_frames,
        malformed_frames=malformed,
        content_chars=content_chars,
        cache_read_tokens=usage.get("cache_read"),
        cache_write_tokens=usage.get("cache_write"),
        reasoning_tokens=usage.get("reasoning"),
        requested_max_completion_tokens=requested_max,
        error_type=error_type,
        error_message=error_message,
        body_hint=hint,
    )


def _load_raw_evidence(artifact_dir: Path) -> tuple[dict[str, RawEvidence], list[str]]:
    """Map request id to raw per-request evidence.

    Joined by `x_request_id` rather than by position, because AIPerf writes the
    normalized records per worker and their order is not guaranteed.
    """
    evidence: dict[str, RawEvidence] = {}
    notes: list[str] = []
    raw_path = artifact_dir / RAW_RECORDS_FILE
    if not raw_path.is_file():
        notes.append(
            "raw export absent; HTTP status, finish_reason, and cache-write counts "
            "cannot be recovered, so no trial can be asserted successful"
        )
        return evidence, notes
    unreadable = 0
    for line in raw_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            unreadable += 1
            continue
        if not isinstance(raw, dict):
            unreadable += 1
            continue
        request_id = (raw.get("metadata") or {}).get("x_request_id")
        if not isinstance(request_id, str) or not request_id:
            unreadable += 1
            continue
        evidence[request_id] = _parse_raw_record(raw)
    if unreadable:
        notes.append(
            f"{unreadable} raw record(s) could not be joined to a normalized record; "
            "their HTTP status and finish reason are unavailable"
        )
    return evidence, notes


def _metric(metrics: dict[str, Any], name: str) -> Any:
    entry = metrics.get(name)
    if isinstance(entry, dict):
        return entry.get("value")
    return None


def _token_accounting(
    *,
    metrics: dict[str, Any],
    raw: RawEvidence | None,
    accounting_source: str,
    target_input_tokens: int | None,
    token_tolerance_pct: float,
) -> TokenAccounting:
    """Build token accounting without manufacturing an independent client count.

    Under provider accounting AIPerf populates `input_sequence_length` and
    `output_sequence_length` from the server's `usage`, so treating those as
    client estimates makes `client_vs_provider` compare a number with itself.
    The client fields are therefore left `None` and the note records why.
    """
    provider_input = _metric(metrics, "usage_prompt_tokens")
    provider_output = _metric(metrics, "usage_completion_tokens")
    if accounting_source == ACCOUNTING_PROVIDER_USAGE:
        client_estimate = None
        observed_output = None
        note = (
            "provider accounting: AIPerf derives input_sequence_length and "
            "output_sequence_length from provider usage, so they are not "
            "independent client estimates and are not recorded as such"
        )
    else:
        client_estimate = _metric(metrics, "input_sequence_length")
        observed_output = _metric(metrics, "output_sequence_length")
        note = None
    return TokenAccounting(
        target_input_tokens=target_input_tokens,
        client_estimated_input_tokens=client_estimate,
        provider_input_tokens=provider_input,
        provider_output_tokens=provider_output,
        observed_output_tokens=observed_output,
        tolerance_pct=token_tolerance_pct,
        accounting_source=accounting_source,
        client_estimate_note=note,
    )


def _provider_telemetry(
    *,
    metrics: dict[str, Any],
    raw: RawEvidence | None,
    provider_output_tokens: int | None,
) -> ProviderTelemetry:
    """Prefer AIPerf's normalized metrics; fall back to the raw stream.

    AIPerf 0.13.0 exposes cache-read and reasoning counts but no cache-write
    metric, so cache-write can only come from the raw `usage` details. Each field
    records which source produced it.
    """
    sources: dict[str, str] = {}

    def pick(metric_name: str, raw_value: int | None, field_name: str) -> int | None:
        value = _metric(metrics, metric_name)
        if isinstance(value, int) and not isinstance(value, bool):
            sources[field_name] = "aiperf_metric"
            return value
        if raw_value is not None:
            sources[field_name] = "raw_response_usage"
            return raw_value
        return None

    cache_read = pick(
        "usage_prompt_cache_read_tokens",
        raw.cache_read_tokens if raw else None,
        "cache_read_tokens",
    )
    reasoning = pick(
        "usage_reasoning_tokens",
        raw.reasoning_tokens if raw else None,
        "reasoning_tokens",
    )
    cache_write = raw.cache_write_tokens if raw else None
    if cache_write is not None:
        sources["cache_write_tokens"] = "raw_response_usage"

    if provider_output_tokens is not None and reasoning is not None:
        visible: int | None = max(provider_output_tokens - reasoning, 0)
        sources["visible_output_tokens"] = "derived:provider_output_minus_reasoning"
    else:
        visible = None

    return ProviderTelemetry(
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        reasoning_tokens=reasoning,
        visible_output_tokens=visible,
        sources=sources,
    )


def _trial_from_record(
    record: dict[str, Any],
    *,
    deployment_key: str,
    profile_name: str,
    index: int,
    target_input_tokens: int | None = None,
    token_tolerance_pct: float = 10.0,
    accounting_source: str = ACCOUNTING_CLIENT_TOKENIZER,
    raw: RawEvidence | None = None,
) -> Trial:
    """Build one trial, requiring positive evidence before asserting success."""
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
        trial.attribution = "harness"
        return trial

    http_status = raw.http_status if raw else None
    if metadata.get("was_cancelled"):
        trial.observe(LifecycleState.STARTED)
        trial.observe(LifecycleState.CANCELLED)
        trial.outcome = OutcomeState.UNKNOWN
        trial.outcome_detail = "cancelled"
        trial.attempts.append(
            HttpAttempt(attempt_index=1, started_at=utc_now(), http_status=http_status)
        )
        return trial

    trial.observe(LifecycleState.STARTED)
    trial.attempts.append(
        HttpAttempt(
            attempt_index=1,
            started_at=utc_now(),
            ended_at=utc_now(),
            http_status=http_status,
        )
    )

    if raw is not None:
        trial.finish_reason = raw.finish_reason
        trial.finish_class = raw.finish_class
        trial.requested_max_completion_tokens = raw.requested_max_completion_tokens

    error = record.get("error")
    error_document = error if isinstance(error, dict) else None

    # A non-2xx status is decisive and takes precedence over any body content.
    if http_status is not None and not (200 <= http_status < 300):
        trial.observe(LifecycleState.TRANSPORT_COMPLETED)
        outcome, detail = _classify_http_status(
            http_status, error_document, raw.body_hint if raw else None
        )
        trial.outcome = outcome
        trial.outcome_detail = detail
        trial.attribution = "provider" if http_status >= 500 or http_status == 429 else "unknown"
        if not trial.finish_class or trial.finish_class == FINISH_ABSENT:
            trial.finish_class = FINISH_ABSENT
        return trial

    # An explicit transport error is positive evidence of failure. A connection
    # error legitimately has no HTTP status, so it is classified from the error
    # rather than from the absence of a status.
    if error_document is not None:
        trial.observe(LifecycleState.TRANSPORT_COMPLETED)
        outcome, detail = _classify_error(error_document)
        trial.outcome = outcome
        trial.outcome_detail = detail
        trial.attribution = "harness" if outcome is OutcomeState.CLIENT_ERROR else "provider"
        if outcome is OutcomeState.UNKNOWN:
            trial.evidence_gaps.append(EVIDENCE_FINISH_REASON_UNOBSERVED)
        return trial

    if http_status is None:
        trial.observe(LifecycleState.TRANSPORT_COMPLETED)
        trial.evidence_gaps.append(EVIDENCE_HTTP_STATUS_UNOBSERVED)
        trial.outcome = OutcomeState.UNKNOWN
        trial.outcome_detail = "http_status_unobserved"
        trial.attribution = "harness"
        trial.notes.append(
            "no HTTP status was recoverable for this request, so success cannot be "
            "asserted even though a response body was recorded"
        )
        return trial

    trial.observe(LifecycleState.TRANSPORT_COMPLETED)
    if raw is not None and not raw.malformed_frames and raw.finish_class != FINISH_ABSENT:
        trial.observe(LifecycleState.PROTOCOL_COMPLETED)

    trial.token_accounting = _token_accounting(
        metrics=metrics,
        raw=raw,
        accounting_source=accounting_source,
        target_input_tokens=target_input_tokens,
        token_tolerance_pct=token_tolerance_pct,
    )
    trial.provider_telemetry = _provider_telemetry(
        metrics=metrics,
        raw=raw,
        provider_output_tokens=trial.token_accounting.provider_output_tokens,
    )
    trial.ttft_ms = _metric(metrics, "time_to_first_token")
    trial.duration_ms = _metric(metrics, "request_latency")
    trial.input_tokens = _metric(metrics, "usage_prompt_tokens")
    trial.output_tokens = _metric(metrics, "usage_completion_tokens")
    trial.total_tokens = _metric(metrics, "usage_total_tokens")

    if raw is not None and raw.malformed_frames:
        trial.outcome = OutcomeState.PROTOCOL_FAILURE
        trial.outcome_detail = "malformed_sse_json"
        trial.attribution = "provider"
        trial.notes.append(f"{raw.malformed_frames} malformed SSE data frame(s) observed")
        return trial

    if raw is None:
        # No finish reason means no way to distinguish a natural completion from
        # a ceiling cut, so the stricter reading wins.
        trial.evidence_gaps.append(EVIDENCE_FINISH_REASON_UNOBSERVED)
        trial.outcome = OutcomeState.UNKNOWN
        trial.outcome_detail = "finish_reason_unobserved"
        trial.attribution = "harness"
        trial.notes.append(
            "response completed at the transport level but no finish_reason was "
            "recoverable; natural completion is unverified"
        )
        return trial

    if not trial.provider_telemetry.cache_telemetry_observed:
        trial.notes.append(EVIDENCE_USAGE_UNOBSERVED)

    if trial.finish_class == FINISH_CEILING:
        trial.outcome = OutcomeState.OUTPUT_TRUNCATED
        trial.outcome_detail = (
            f"finish_reason_{trial.finish_reason}"
            if trial.requested_max_completion_tokens is None
            else f"finish_reason_{trial.finish_reason}_at_max_completion_tokens_"
            f"{trial.requested_max_completion_tokens}"
        )
        # Our own ceiling caused this, so it is a harness configuration outcome,
        # not an endpoint defect.
        trial.attribution = "harness"
        trial.notes.append(
            "response stopped at the configured output ceiling; request_latency is "
            "time-to-ceiling, a lower bound on time-to-completion"
        )
        return trial

    if trial.finish_class == FINISH_CONTENT_FILTER:
        trial.outcome = OutcomeState.PROTOCOL_FAILURE
        trial.outcome_detail = f"finish_reason_{trial.finish_reason}"
        trial.attribution = "provider"
        trial.notes.append("provider stopped the response on a content policy")
        return trial

    if trial.finish_class == FINISH_ABSENT:
        interrupted = not raw.saw_done
        trial.outcome = (
            OutcomeState.STREAM_INTERRUPTED if interrupted else OutcomeState.PROTOCOL_FAILURE
        )
        trial.outcome_detail = (
            "stream_ended_without_finish_reason"
            if interrupted
            else "response_without_finish_reason"
        )
        trial.attribution = "provider"
        trial.evidence_gaps.append(EVIDENCE_FINISH_REASON_UNOBSERVED)
        trial.notes.append(
            "no terminal finish_reason was observed; the response may be incomplete"
        )
        return trial

    if trial.finish_class == FINISH_UNKNOWN:
        trial.outcome = OutcomeState.UNKNOWN
        trial.outcome_detail = f"unrecognized_finish_reason_{trial.finish_reason}"
        trial.attribution = "provider"
        trial.notes.append(
            f"finish_reason `{trial.finish_reason}` is not in the known vocabulary; "
            "it is reported rather than assumed natural"
        )
        return trial

    # FINISH_NATURAL: 2xx, terminal natural finish reason.
    trial.outcome = OutcomeState.SUCCESS
    trial.outcome_detail = "completed"

    input_diff = _metric(metrics, "usage_prompt_tokens_diff_pct")
    if isinstance(input_diff, (int, float)) and input_diff >= 10:
        trial.notes.append(
            f"client-vs-provider input token count differs by {input_diff:.1f}%; "
            "do not make token-normalized claims from the client count"
        )
    if trial.provider_telemetry.all_reasoning:
        trial.notes.append(
            "the entire completion budget was reasoning tokens; no visible answer "
            "content was emitted"
        )
    return trial


def _load_http_statuses(artifact_dir: Path) -> dict[str, int]:
    """Map request id to HTTP status from raw evidence.

    Retained as a thin view over `_load_raw_evidence` for callers that only need
    the status. The normalized records carry metrics but not the HTTP status;
    only the raw export has it.
    """
    evidence, _ = _load_raw_evidence(artifact_dir)
    return {
        request_id: item.http_status
        for request_id, item in evidence.items()
        if item.http_status is not None
    }


def parse_artifacts(
    artifact_dir: Path,
    *,
    deployment_key: str,
    profile_name: str,
    target_input_tokens: int | None = None,
    token_tolerance_pct: float = 10.0,
    accounting_source: str = ACCOUNTING_CLIENT_TOKENIZER,
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

    raw_by_request, raw_notes = _load_raw_evidence(artifact_dir)
    notes.extend(raw_notes)

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
            accounting_source=accounting_source,
            raw=raw_by_request.get(str((record.get("metadata") or {}).get("x_request_id"))),
        )
        for index, record in enumerate(records)
    ]
    counters = count_trials(trials)
    # A benchmark failure means the endpoint produced nothing scoreable. A run in
    # which every response was cut by our own output ceiling is not a failure of
    # the endpoint or of the benchmark: it measured real time-to-first-token and
    # token rates, and it is reported as truncated rather than successful.
    benchmark_failed = summary is None or (
        bool(trials) and counters.succeeded == 0 and counters.truncated == 0
    )
    if counters.succeeded == 0 and counters.truncated:
        notes.append(
            f"no trial completed naturally: {counters.truncated} of {counters.started} "
            "started trials stopped at the configured output ceiling. Timings and token "
            "rates remain measurable; completion latency is censored."
        )

    if raw_by_request and len(raw_by_request) != len(records):
        notes.append(
            f"raw export has {len(raw_by_request)} request(s) against {len(records)} "
            "normalized record(s); unmatched trials carry no status or finish reason"
        )

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
