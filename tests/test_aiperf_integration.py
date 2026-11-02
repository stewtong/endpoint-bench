from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from endpoint_bench.aiperf import (
    AFFINITY_CLIENT,
    BASELINE_CLIENT,
    AiperfError,
    AiperfSchemaError,
    build_argv,
    build_benchmark_config,
    parse_artifacts,
    run_aiperf,
    write_config,
)
from endpoint_bench.mock_provider import MockBehavior, MockProvider

AIPERF_EXECUTABLE = Path(".venv-aiperf/bin/aiperf")
requires_aiperf = pytest.mark.skipif(
    not AIPERF_EXECUTABLE.exists(),
    reason="pinned AIPerf environment not installed; see docs/decisions/0002",
)

SECRET = "sk-invariant-test-secret-value"


def _config(tmp_path: Path, base_url: str, client=BASELINE_CLIENT, requests: int = 3) -> Path:
    artifact_dir = tmp_path / client.name
    config = build_benchmark_config(
        model_id="mock-model",
        base_url=base_url,
        client=client,
        requests=requests,
        concurrency=1,
        input_tokens=64,
        output_tokens=16,
        artifact_dir=artifact_dir,
        credential_env_name="ENDPOINT_BENCH_OFFLINE_API_KEY",
    )
    path = artifact_dir / "aiperf-config.yaml"
    write_config(path, config)
    return path


# Invariant 2: never pass authentication through command-line arguments.


def test_argv_refuses_api_key() -> None:
    with pytest.raises(AiperfError, match="credential on the command line"):
        build_argv(
            executable="aiperf",
            config_path=Path("c.yaml"),
            artifact_dir=Path("a"),
            client=BASELINE_CLIENT,
            extra_argv=("--api-key", SECRET),
        )


def test_argv_refuses_api_key_equals_form() -> None:
    with pytest.raises(AiperfError, match="credential on the command line"):
        build_argv(
            executable="aiperf",
            config_path=Path("c.yaml"),
            artifact_dir=Path("a"),
            client=BASELINE_CLIENT,
            extra_argv=(f"--api-key={SECRET}",),
        )


def test_argv_pins_readiness_probe_off() -> None:
    argv = build_argv(
        executable="aiperf",
        config_path=Path("c.yaml"),
        artifact_dir=Path("a"),
        client=BASELINE_CLIENT,
    )

    assert "--wait-for-model-timeout" in argv
    assert argv[argv.index("--wait-for-model-timeout") + 1] == "0"
    assert "--config" in argv
    assert "profile" in argv


def test_client_profile_env_sets_affinity_explicitly() -> None:
    assert BASELINE_CLIENT.env() == {
        "AIPERF_HTTP_X_SESSION_AFFINITY_FROM_CORRELATION_ID": "false"
    }
    assert AFFINITY_CLIENT.env() == {
        "AIPERF_HTTP_X_SESSION_AFFINITY_FROM_CORRELATION_ID": "true"
    }
    assert BASELINE_CLIENT.identity() != AFFINITY_CLIENT.identity()


def test_generated_config_has_no_literal_secret(tmp_path: Path) -> None:
    config_path = _config(tmp_path, "http://127.0.0.1:1")

    text = config_path.read_text()

    assert "${ENDPOINT_BENCH_OFFLINE_API_KEY}" in text
    assert SECRET not in text


# Invariant 7: validate engine version and schema before parsing.


def test_parse_rejects_wrong_aiperf_version(tmp_path: Path) -> None:
    artifact = tmp_path / "run"
    artifact.mkdir()
    (artifact / "profile_export_aiperf.json").write_text(
        json.dumps({"schema_version": "1.4", "aiperf_version": "0.14.0"})
    )

    with pytest.raises(AiperfSchemaError, match="unsupported AIPerf version"):
        parse_artifacts(artifact, deployment_key="d", profile_name="p")


def test_parse_rejects_wrong_summary_schema(tmp_path: Path) -> None:
    artifact = tmp_path / "run"
    artifact.mkdir()
    (artifact / "profile_export_aiperf.json").write_text(
        json.dumps({"schema_version": "2.0", "aiperf_version": "0.13.0"})
    )

    with pytest.raises(AiperfSchemaError, match="unsupported summary schema"):
        parse_artifacts(artifact, deployment_key="d", profile_name="p")


# Invariant 6: nonzero exits and absent summaries are supported failure paths.


def test_missing_summary_is_a_benchmark_failure_not_an_integration_error(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "failed"
    artifact.mkdir()
    (artifact / "profile_export.jsonl").write_text(
        json.dumps(
            {
                "metadata": {"context_overflow_skip": False, "was_cancelled": False},
                "error": {"type": "ClientConnectorError", "message": "refused"},
            }
        )
        + "\n"
    )

    results = parse_artifacts(artifact, deployment_key="d", profile_name="p")

    assert results.benchmark_failed is True
    assert results.schema_validated is False
    assert results.counters.succeeded == 0
    assert results.counters.failed == 1
    assert results.trials[0].outcome.value == "client_error"


def test_no_artifacts_at_all_is_an_integration_error(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()

    with pytest.raises(AiperfError, match="integration failure"):
        parse_artifacts(empty, deployment_key="d", profile_name="p")


# Invariant 9 / 4: attempts and request accounting.


def _raw_stream_frame(
    *,
    request_id: str,
    status: int = 200,
    finish_reason: str | None = "stop",
    content: str = "ok",
    usage: dict | None = None,
    max_completion_tokens: int | None = 16,
    saw_done: bool = True,
) -> dict:
    """One raw request record shaped like AIPerf's `profile_export_raw.jsonl`."""
    packets: list[dict] = []
    if content:
        packets.append(
            {
                "name": "data",
                "value": json.dumps(
                    {
                        "choices": [
                            {"index": 0, "delta": {"content": content}, "finish_reason": None}
                        ]
                    }
                ),
            }
        )
    final: dict = {"choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}]}
    if usage is not None:
        final["usage"] = usage
    packets.append({"name": "data", "value": json.dumps(final)})
    if saw_done:
        packets.append({"name": "data", "value": "[DONE]"})
    payload: dict = {"messages": [{"role": "user", "content": "x"}], "stream": True}
    if max_completion_tokens is not None:
        payload["max_completion_tokens"] = max_completion_tokens
    return {
        "metadata": {"x_request_id": request_id},
        "payload": payload,
        "status": status,
        "responses": [{"perf_ns": 1, "packets": packets}],
    }


def test_parse_preserves_attempt_count_separately_from_trials(tmp_path: Path) -> None:
    artifact = tmp_path / "run"
    artifact.mkdir()
    (artifact / "profile_export_aiperf.json").write_text(
        json.dumps({"schema_version": "1.4", "aiperf_version": "0.13.0"})
    )
    (artifact / "profile_export.jsonl").write_text(
        "\n".join(
            json.dumps(
                {
                    "metadata": {
                        "was_cancelled": False,
                        "context_overflow_skip": False,
                        "x_request_id": f"req-{index}",
                    },
                    "metrics": {"time_to_first_token": {"value": 1.0, "unit": "ms"}},
                }
            )
            for index in range(2)
        )
        + "\n"
    )
    (artifact / "profile_export_raw.jsonl").write_text(
        "\n".join(
            json.dumps(_raw_stream_frame(request_id=f"req-{index}")) for index in range(2)
        )
        + "\n"
    )

    results = parse_artifacts(artifact, deployment_key="d", profile_name="p")

    assert results.counters.planned == 2
    assert results.counters.http_attempts == 2
    assert results.counters.succeeded == 2
    assert results.counters.truncated == 0


# Invariants 1, 3, 4, 10: real subprocess against the local mock.


@requires_aiperf
def test_baseline_sends_no_affinity_header_and_no_secret_leaks(tmp_path: Path) -> None:
    artifact_dir = tmp_path / BASELINE_CLIENT.name
    with MockProvider() as mock:
        config_path = _config(tmp_path, mock.base_url)
        run = run_aiperf(
            executable=str(AIPERF_EXECUTABLE.resolve()),
            config_path=config_path,
            artifact_dir=artifact_dir,
            client=BASELINE_CLIENT,
            secrets={"ENDPOINT_BENCH_OFFLINE_API_KEY": SECRET},
            process_timeout_s=180,
        )

        # Invariant 1: absence verified at the receiving server, not inferred.
        assert mock.sent_header("X-Session-Affinity") == [None] * len(mock.requests)
        # Invariant 4: no readiness probe, no warmup, every request accounted.
        assert len(mock.requests) == len(mock.inference_requests) == 3
        # Invariant 3: credential delivered through the environment.
        assert all(request.has_header("Authorization") for request in mock.inference_requests)

    assert run.exit_code == 0
    assert run.timed_out is False
    leaked = [
        path
        for path in artifact_dir.rglob("*")
        if path.is_file() and SECRET in path.read_text(errors="ignore")
    ]
    assert leaked == [], f"secret leaked into {leaked}"


@requires_aiperf
def test_affinity_profile_is_a_separate_identity_with_the_header(tmp_path: Path) -> None:
    with MockProvider() as mock:
        config_path = _config(tmp_path, mock.base_url, client=AFFINITY_CLIENT)
        run = run_aiperf(
            executable=str(AIPERF_EXECUTABLE.resolve()),
            config_path=config_path,
            artifact_dir=tmp_path / AFFINITY_CLIENT.name,
            client=AFFINITY_CLIENT,
            secrets={"ENDPOINT_BENCH_OFFLINE_API_KEY": SECRET},
            process_timeout_s=180,
        )

        values = mock.sent_header("X-Session-Affinity")
        assert all(value is not None for value in values)

    assert run.exit_code == 0
    assert run.client_identity != BASELINE_CLIENT.identity()


@requires_aiperf
def test_all_failing_endpoint_yields_benchmark_failure(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "fail"
    with MockProvider(behavior=MockBehavior.SERVER_ERROR) as mock:
        config_path = _config(tmp_path, mock.base_url)
        run = run_aiperf(
            executable=str(AIPERF_EXECUTABLE.resolve()),
            config_path=config_path,
            artifact_dir=artifact_dir,
            client=BASELINE_CLIENT,
            secrets={"ENDPOINT_BENCH_OFFLINE_API_KEY": SECRET},
            process_timeout_s=180,
        )

    results = parse_artifacts(artifact_dir, deployment_key="d", profile_name="p")

    assert run.exit_code != 0
    assert results.benchmark_failed is True
    assert results.counters.succeeded == 0


@requires_aiperf
def test_process_deadline_terminates_stalled_run(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "stall"
    with MockProvider(behavior=MockBehavior.HANG_BEFORE_FIRST_TOKEN, hang_seconds=60) as mock:
        config = build_benchmark_config(
            model_id="mock-model",
            base_url=mock.base_url,
            client=BASELINE_CLIENT,
            requests=1,
            concurrency=1,
            input_tokens=32,
            output_tokens=8,
            artifact_dir=artifact_dir,
            credential_env_name="ENDPOINT_BENCH_OFFLINE_API_KEY",
            # The CLI always captures raw evidence; without it no HTTP status or
            # finish_reason is recoverable and success cannot be asserted.
            capture_raw=True,
        )
        config["benchmark"]["endpoint"]["timeout"] = 30.0
        config_path = artifact_dir / "aiperf-config.yaml"
        write_config(config_path, config)

        run = run_aiperf(
            executable=str(AIPERF_EXECUTABLE.resolve()),
            config_path=config_path,
            artifact_dir=artifact_dir,
            client=BASELINE_CLIENT,
            secrets={"ENDPOINT_BENCH_OFFLINE_API_KEY": SECRET},
            process_timeout_s=8,
        )

    assert run.timed_out is True
    assert any("deadline" in note for note in run.notes)
    assert run.stdout_path.is_file()
    assert run.stderr_path.is_file()


@requires_aiperf
def test_sweep_survives_credential_injection(tmp_path: Path) -> None:
    """Invariant 3: secrets must survive a sweep, not only a single run."""
    artifact_dir = tmp_path / "sweep"
    with MockProvider() as mock:
        config = build_benchmark_config(
            model_id="mock-model",
            base_url=mock.base_url,
            client=BASELINE_CLIENT,
            requests=2,
            concurrency=1,
            input_tokens=32,
            output_tokens=8,
            artifact_dir=artifact_dir,
            credential_env_name="ENDPOINT_BENCH_OFFLINE_API_KEY",
            # The CLI always captures raw evidence; without it no HTTP status or
            # finish_reason is recoverable and success cannot be asserted.
            capture_raw=True,
        )
        # Sweep paths address dataset entries by name, not by index.
        config["sweep"] = {"type": "grid", "parameters": {"datasets.main.prompts.osl": [8, 16]}}
        config_path = artifact_dir / "aiperf-config.yaml"
        write_config(config_path, config)

        run = run_aiperf(
            executable=str(AIPERF_EXECUTABLE.resolve()),
            config_path=config_path,
            artifact_dir=artifact_dir,
            client=BASELINE_CLIENT,
            secrets={"ENDPOINT_BENCH_OFFLINE_API_KEY": SECRET},
            process_timeout_s=240,
        )

        assert mock.inference_requests, "sweep produced no traffic"
        assert all(request.has_header("Authorization") for request in mock.inference_requests)

    assert run.exit_code == 0, run.stderr_path.read_text()
    leaked = [
        path
        for path in artifact_dir.rglob("*")
        if path.is_file() and SECRET in path.read_text(errors="ignore")
    ]
    assert leaked == []


def test_aiperf_executable_version_is_pinned() -> None:
    """Guard against a silently upgraded environment."""
    if not AIPERF_EXECUTABLE.exists():
        pytest.skip("pinned AIPerf environment not installed")
    result = subprocess.run(
        [str(AIPERF_EXECUTABLE.resolve()), "--version"],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "HF_HUB_OFFLINE": "1"},
    )

    assert result.stdout.strip() == "0.13.0"


@pytest.fixture(autouse=True)
def _no_stray_artifacts(tmp_path: Path):
    yield
    shutil.rmtree(tmp_path, ignore_errors=True)


# Invariant 8: distinct token measures, no correction factors.


@requires_aiperf
def test_token_accounting_records_four_distinct_measures(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "tokens"
    with MockProvider() as mock:
        config = build_benchmark_config(
            model_id="mock-model",
            base_url=mock.base_url,
            client=BASELINE_CLIENT,
            requests=2,
            concurrency=1,
            input_tokens=64,
            output_tokens=16,
            artifact_dir=artifact_dir,
            credential_env_name="ENDPOINT_BENCH_OFFLINE_API_KEY",
            # The CLI always captures raw evidence; without it no HTTP status or
            # finish_reason is recoverable and success cannot be asserted.
            capture_raw=True,
        )
        config_path = artifact_dir / "aiperf-config.yaml"
        write_config(config_path, config)
        run = run_aiperf(
            executable=str(AIPERF_EXECUTABLE.resolve()),
            config_path=config_path,
            artifact_dir=artifact_dir,
            client=BASELINE_CLIENT,
            secrets={"ENDPOINT_BENCH_OFFLINE_API_KEY": SECRET},
            process_timeout_s=180,
        )

    assert run.exit_code == 0
    results = parse_artifacts(
        artifact_dir,
        deployment_key="d",
        profile_name="p",
        target_input_tokens=64,
        token_tolerance_pct=10.0,
    )
    accounting = results.trials[0].token_accounting

    # Target and client estimate must agree: the workload was generated to spec.
    assert accounting.target_input_tokens == 64
    assert accounting.client_estimated_input_tokens == 64
    # The provider's count is recorded separately and never overwritten.
    assert accounting.provider_input_tokens is not None
    assert accounting.observed_output_tokens is not None
    payload = accounting.to_dict()
    assert set(payload) == {
        "accounting_source",
        "target_input_tokens",
        "client_estimated_input_tokens",
        "provider_input_tokens",
        "provider_output_tokens",
        "observed_output_tokens",
        "client_estimate_note",
        "provider_vs_target",
        "client_vs_provider",
        "discrepancy_pct",
        "tolerance_pct",
        "primary_check",
        "sizing_verified",
    }
    # Client accounting keeps the client-versus-provider check authoritative.
    assert payload["primary_check"] == "client_vs_provider"
    # The local mock estimates usage as characters/4 rather than running the served
    # model's tokenizer, so the two counts differ by roughly 10% here. That is a
    # fixture limitation, not an endpoint finding.
    #
    # The estimate lands close to the 10% tolerance boundary and wobbles by a token
    # or two between runs, so the exact verdict is not asserted. What must hold is
    # that the verdict follows the measured discrepancy rather than being assumed,
    # and that the boolean form agrees with the authoritative check.
    discrepancy = payload["client_vs_provider"]["discrepancy_pct"]
    assert discrepancy is not None
    assert discrepancy > 0, "the mock cannot agree exactly with the tokenizer by construction"
    assert (payload["client_vs_provider"]["verdict"] == "failed") == (
        discrepancy > payload["tolerance_pct"]
    )
    assert payload["sizing_verified"] is (
        payload["client_vs_provider"]["verdict"] == "verified"
    )
    # Both checks see the same two numbers under client accounting.
    assert payload["provider_vs_target"]["discrepancy_pct"] == pytest.approx(
        abs(accounting.provider_input_tokens - 64) / 64 * 100.0
    )


@requires_aiperf
def test_deliberate_token_mismatch_is_detected_and_blocks_claims(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "mismatch"
    with MockProvider(prompt_tokens_override=1) as mock:
        config = build_benchmark_config(
            model_id="mock-model",
            base_url=mock.base_url,
            client=BASELINE_CLIENT,
            requests=2,
            concurrency=1,
            input_tokens=64,
            output_tokens=16,
            artifact_dir=artifact_dir,
            credential_env_name="ENDPOINT_BENCH_OFFLINE_API_KEY",
            # The CLI always captures raw evidence; without it no HTTP status or
            # finish_reason is recoverable and success cannot be asserted.
            capture_raw=True,
        )
        config_path = artifact_dir / "aiperf-config.yaml"
        write_config(config_path, config)
        run = run_aiperf(
            executable=str(AIPERF_EXECUTABLE.resolve()),
            config_path=config_path,
            artifact_dir=artifact_dir,
            client=BASELINE_CLIENT,
            secrets={"ENDPOINT_BENCH_OFFLINE_API_KEY": SECRET},
            process_timeout_s=180,
        )

    assert run.exit_code == 0
    results = parse_artifacts(
        artifact_dir,
        deployment_key="d",
        profile_name="p",
        target_input_tokens=64,
        token_tolerance_pct=10.0,
    )
    accounting = results.trials[0].token_accounting

    assert accounting.provider_input_tokens == 1
    assert accounting.client_estimated_input_tokens == 64
    assert accounting.discrepancy_pct is not None
    assert accounting.discrepancy_pct > 90
    assert accounting.sizing_verified is False
    # The reported number is preserved verbatim: no correction factor applied.
    assert results.trials[0].input_tokens == 1


@requires_aiperf
def test_provenance_reconciles_requests_for_a_real_run(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "prov"
    with MockProvider() as mock:
        config = build_benchmark_config(
            model_id="mock-model",
            base_url=mock.base_url,
            client=BASELINE_CLIENT,
            requests=3,
            concurrency=1,
            input_tokens=32,
            output_tokens=8,
            artifact_dir=artifact_dir,
            credential_env_name="ENDPOINT_BENCH_OFFLINE_API_KEY",
            # The CLI always captures raw evidence; without it no HTTP status or
            # finish_reason is recoverable and success cannot be asserted.
            capture_raw=True,
        )
        config_path = artifact_dir / "aiperf-config.yaml"
        write_config(config_path, config)
        run = run_aiperf(
            executable=str(AIPERF_EXECUTABLE.resolve()),
            config_path=config_path,
            artifact_dir=artifact_dir,
            client=BASELINE_CLIENT,
            secrets={"ENDPOINT_BENCH_OFFLINE_API_KEY": SECRET},
            process_timeout_s=180,
            target_url=mock.base_url,
        )
        observed = len(mock.inference_requests)

    results = parse_artifacts(artifact_dir, deployment_key="d", profile_name="p")

    assert run.exit_code == 0
    assert observed == results.counters.http_attempts == 3
    assert run.termination.reason == "completed"


def test_openai_base_url_is_normalised_for_aiperf(tmp_path: Path) -> None:
    """AIPerf appends /v1/chat/completions, so the /v1 base must not survive."""
    config = build_benchmark_config(
        model_id="mock-model",
        base_url="https://api.example.invalid/v1",
        client=BASELINE_CLIENT,
        requests=2,
        concurrency=1,
        input_tokens=32,
        output_tokens=8,
        artifact_dir=tmp_path,
        credential_env_name="ENDPOINT_BENCH_OFFLINE_API_KEY",
    )

    assert config["benchmark"]["endpoint"]["url"] == "https://api.example.invalid"


def test_base_url_without_v1_is_unchanged(tmp_path: Path) -> None:
    config = build_benchmark_config(
        model_id="mock-model",
        base_url="http://127.0.0.1:18080",
        client=BASELINE_CLIENT,
        requests=1,
        concurrency=1,
        input_tokens=8,
        output_tokens=4,
        artifact_dir=tmp_path,
        credential_env_name=None,
    )

    assert config["benchmark"]["endpoint"]["url"] == "http://127.0.0.1:18080"


def test_http_status_is_joined_from_raw_evidence(tmp_path: Path) -> None:
    """The normalized record has metrics only; status lives in the raw export."""
    artifact = tmp_path / "joined"
    artifact.mkdir()
    (artifact / "profile_export_aiperf.json").write_text(
        json.dumps({"schema_version": "1.4", "aiperf_version": "0.13.0"})
    )
    (artifact / "profile_export.jsonl").write_text(
        json.dumps(
            {
                "metadata": {"x_request_id": "req-1", "was_cancelled": False},
                "metrics": {"time_to_first_token": {"value": 1.0, "unit": "ms"}},
            }
        )
        + "\n"
    )
    (artifact / "profile_export_raw.jsonl").write_text(
        json.dumps({"metadata": {"x_request_id": "req-1"}, "status": 200}) + "\n"
    )

    results = parse_artifacts(artifact, deployment_key="d", profile_name="p")

    assert results.trials[0].attempts[0].http_status == 200


def test_missing_raw_evidence_leaves_status_unknown(tmp_path: Path) -> None:
    artifact = tmp_path / "noraw"
    artifact.mkdir()
    (artifact / "profile_export_aiperf.json").write_text(
        json.dumps({"schema_version": "1.4", "aiperf_version": "0.13.0"})
    )
    (artifact / "profile_export.jsonl").write_text(
        json.dumps(
            {
                "metadata": {"x_request_id": "req-1", "was_cancelled": False},
                "metrics": {"time_to_first_token": {"value": 1.0, "unit": "ms"}},
            }
        )
        + "\n"
    )

    results = parse_artifacts(artifact, deployment_key="d", profile_name="p")

    # Unknown is None, never a fabricated 200.
    assert results.trials[0].attempts[0].http_status is None


def test_trace_dataset_uses_file_type_and_underscore_format(tmp_path: Path) -> None:
    """AIPerf's discriminator is `type: file`; the format is a separate enum."""
    trace = tmp_path / "trace.jsonl"
    trace.write_text('{"input_length": 10, "output_length": 4}\n')

    config = build_benchmark_config(
        model_id="mock-model",
        base_url="http://127.0.0.1:18080",
        client=BASELINE_CLIENT,
        requests=1,
        concurrency=1,
        input_tokens=8,
        output_tokens=4,
        artifact_dir=tmp_path,
        credential_env_name=None,
        trace_file=trace,
        trace_type="mooncake-trace",
    )
    dataset = config["benchmark"]["datasets"][0]

    assert dataset["type"] == "file"
    assert dataset["format"] == "mooncake_trace"
    assert dataset["path"] == str(trace.resolve())
    # A replay dataset must not carry synthetic prompt sizing.
    assert "prompts" not in dataset
