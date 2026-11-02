from __future__ import annotations

import json
from pathlib import Path

import pytest

from endpoint_bench.cli import main
from endpoint_bench.mock_provider import MockBehavior, MockProvider
from endpoint_bench.probes import ProbeConfig, probe
from endpoint_bench.trials import (
    HttpAttempt,
    LifecycleState,
    OutcomeState,
    Trial,
    TrialSchemaError,
    count_trials,
)

PROMPT = "conformance probe payload"


def run_probe(behavior: MockBehavior, **kwargs) -> Trial:
    options = {"timeout_s": 2.0, **kwargs}
    with MockProvider(behavior=behavior) as mock:
        config = ProbeConfig(base_url=mock.base_url, model_id="mock-model", **options)
        trial = Trial(trial_id="t", deployment_key="mock", profile_name="smoke")
        probe(config, api_key=None, prompt=PROMPT, trial=trial)
    return trial


# -- streaming happy path ----------------------------------------------------


def test_streaming_success_records_ttft_usage_and_finish() -> None:
    trial = run_probe(MockBehavior.STREAM_SUCCESS)

    assert trial.outcome is OutcomeState.SUCCESS
    assert trial.lifecycle is LifecycleState.PROTOCOL_COMPLETED
    assert trial.ttft_ms is not None and trial.ttft_ms >= 0
    assert trial.duration_ms is not None
    assert trial.finish_reason == "stop"
    # The mock derives usage from the payload, so the count is coherent rather
    # than a magic constant. Assert the relationship, not a fabricated number.
    assert trial.input_tokens is not None and trial.input_tokens > 0
    assert trial.output_tokens is not None
    assert trial.total_tokens == trial.input_tokens + trial.output_tokens
    assert trial.http_attempts == 1


def test_deliberate_usage_override_is_reported_verbatim() -> None:
    with MockProvider(
        behavior=MockBehavior.STREAM_SUCCESS, prompt_tokens_override=1
    ) as mock:
        config = ProbeConfig(base_url=mock.base_url, model_id="mock-model", timeout_s=2.0)
        trial = Trial(trial_id="t", deployment_key="mock", profile_name="smoke")
        probe(config, api_key=None, prompt="x" * 400, trial=trial)

    assert trial.outcome is OutcomeState.SUCCESS
    assert trial.input_tokens == 1


def test_nonstreaming_success_is_classified_the_same() -> None:
    trial = run_probe(MockBehavior.NONSTREAM_SUCCESS, streaming=False)

    assert trial.outcome is OutcomeState.SUCCESS
    assert trial.finish_reason == "stop"
    assert trial.ttft_ms is None  # TTFT is not observable without streaming


# -- missing telemetry stays unknown ----------------------------------------


def test_missing_usage_is_unknown_not_zero() -> None:
    trial = run_probe(MockBehavior.MISSING_USAGE)

    assert trial.outcome is OutcomeState.SUCCESS
    assert trial.total_tokens is None
    assert trial.input_tokens is None
    assert trial.output_tokens is None


# -- error classification ----------------------------------------------------


@pytest.mark.parametrize(
    ("behavior", "expected", "detail"),
    [
        (MockBehavior.RATE_LIMITED, OutcomeState.RATE_LIMITED, "http_429"),
        (MockBehavior.SERVER_ERROR, OutcomeState.SERVER_ERROR, "http_503"),
        (MockBehavior.TRUNCATE_NO_FINISH, OutcomeState.STREAM_INTERRUPTED, "unterminated_stream"),
        (MockBehavior.INTERRUPT_MID_STREAM, OutcomeState.STREAM_INTERRUPTED, "unterminated_stream"),
        (MockBehavior.MALFORMED_JSON, OutcomeState.PROTOCOL_FAILURE, None),
    ],
)
def test_error_paths_classify_distinctly(
    behavior: MockBehavior, expected: OutcomeState, detail: str | None
) -> None:
    trial = run_probe(behavior)

    assert trial.outcome is expected
    if detail is not None:
        assert trial.outcome_detail == detail
    assert trial.http_attempts == 1


def test_rate_limit_records_http_status() -> None:
    trial = run_probe(MockBehavior.RATE_LIMITED)

    assert trial.attempts[0].http_status == 429
    assert trial.outcome is not OutcomeState.SUCCESS


# -- timeouts ----------------------------------------------------------------


def test_timeout_before_first_token() -> None:
    trial = run_probe(
        MockBehavior.HANG_BEFORE_FIRST_TOKEN, timeout_s=0.5
    )

    assert trial.outcome is OutcomeState.TIMEOUT
    assert "before" in (trial.outcome_detail or "")


def test_timeout_after_first_token_is_distinguished() -> None:
    trial = run_probe(
        MockBehavior.HANG_AFTER_FIRST_TOKEN, timeout_s=0.5
    )

    assert trial.outcome is OutcomeState.TIMEOUT
    assert trial.outcome_detail == "timeout_after_first_token"
    assert trial.ttft_ms is not None


# -- transport failure -------------------------------------------------------


def test_connection_refused_is_a_client_error(monkeypatch: pytest.MonkeyPatch) -> None:
    config = ProbeConfig(base_url="http://127.0.0.1:1", model_id="mock-model", timeout_s=1.0)
    trial = Trial(trial_id="t", deployment_key="mock", profile_name="smoke")

    probe(config, api_key=None, prompt=PROMPT, trial=trial)

    assert trial.outcome in (OutcomeState.CLIENT_ERROR, OutcomeState.TIMEOUT)
    assert trial.http_attempts == 1


# -- accounting --------------------------------------------------------------


def test_attempts_equal_trials_because_there_are_no_implicit_retries() -> None:
    with MockProvider(behavior=MockBehavior.SERVER_ERROR) as mock:
        config = ProbeConfig(base_url=mock.base_url, model_id="mock-model", timeout_s=2.0)
        trials = []
        for index in range(4):
            trial = Trial(trial_id=f"t{index}", deployment_key="mock", profile_name="smoke")
            trials.append(probe(config, api_key=None, prompt=PROMPT, trial=trial))

        # One attempt per trial, and the server saw exactly those requests.
        assert len(mock.inference_requests) == 4

    counters = count_trials(trials)
    assert counters.planned == 4
    assert counters.started == 4
    assert counters.succeeded == 0
    assert counters.failed == 4
    assert counters.http_attempts == 4


def test_skipped_trial_cannot_claim_attempts() -> None:
    trial = Trial(trial_id="t", deployment_key="mock", profile_name="smoke")
    trial.observe(LifecycleState.SKIPPED)
    trial.attempts.append(HttpAttempt(1))

    with pytest.raises(TrialSchemaError, match="skipped trial recorded"):
        count_trials([trial])


def test_success_without_protocol_completion_is_rejected() -> None:
    trial = Trial(trial_id="t", deployment_key="mock", profile_name="smoke")
    trial.observe(LifecycleState.PLANNED)
    trial.observe(LifecycleState.STARTED)
    trial.attempts.append(HttpAttempt(1))
    trial.outcome = OutcomeState.SUCCESS

    with pytest.raises(TrialSchemaError, match="requires lifecycle"):
        count_trials([trial])


# -- CLI end-to-end ----------------------------------------------------------


def _write_registry(tmp_path: Path, base_url: str) -> Path:
    document = json.loads(Path("configs/registry.example.json").read_text())
    document["providers"][0]["base_url"] = base_url
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(document))
    return path


def test_probe_cli_writes_normalized_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with MockProvider() as mock:
        registry_path = _write_registry(tmp_path, mock.base_url)
        profiles_dir = tmp_path / "configs" / "profiles"
        profiles_dir.mkdir(parents=True)
        (profiles_dir / "smoke.json").write_text(Path("configs/profiles/smoke.json").read_text())

        monkeypatch.chdir(tmp_path)
        exit_code = main(
            [
                "probe",
                "--config",
                str(registry_path),
                "--target",
                "offline-mock-smoke",
                "--profile",
                "smoke",
                "--requests",
                "2",
            ]
        )

    assert exit_code == 0
    manifest = json.loads(capsys.readouterr().out)
    assert manifest["manifest_type"] == "probe"
    assert manifest["counters"]["planned_trials"] == 2
    assert manifest["counters"]["successful_trials"] == 2

    trials_path = Path(manifest["trials_path"])
    records = [json.loads(line) for line in trials_path.read_text().splitlines()]
    assert len(records) == 2
    assert all(record["outcome"] == "success" for record in records)
    assert all(record["http_attempts"] == 1 for record in records)
