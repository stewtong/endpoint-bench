from __future__ import annotations

import json

import pytest

from endpoint_bench.mock_provider import MockBehavior, MockProvider
from endpoint_bench.probes import (
    ProbeConfig,
    probe_tool_round_trip,
    validate_tool_arguments,
    validate_tool_history,
)
from endpoint_bench.trials import OutcomeState, Trial

PROMPT = "Read /tmp/example.txt and report its size."
EXPECTED_ARGUMENTS = '{"path": "/tmp/example.txt", "max_bytes": 4096}'


def run_tool_probe(
    behavior: MockBehavior,
    *,
    config_options: dict | None = None,
    call_options: dict | None = None,
    **mock_options,
) -> tuple[Trial, MockProvider]:
    with MockProvider(behavior=behavior, **mock_options) as mock:
        config = ProbeConfig(
            base_url=mock.base_url,
            model_id="mock-model",
            **{"timeout_s": 2.0, **(config_options or {})},
        )
        trial = Trial(trial_id="t", deployment_key="mock", profile_name="smoke")
        probe_tool_round_trip(
            config,
            api_key=None,
            prompt=PROMPT,
            trial=trial,
            **(call_options or {}),
        )
    return trial, mock


# -- happy paths -------------------------------------------------------------


def test_streamed_tool_call_completes_round_trip() -> None:
    trial, mock = run_tool_probe(MockBehavior.TOOL_CALL_STREAM)

    assert trial.outcome is OutcomeState.SUCCESS
    assert trial.outcome_detail == "tool_round_trip_completed"
    assert trial.finish_reason == "stop"
    # One logical trial, two HTTP attempts.
    assert trial.http_attempts == 2
    assert len(mock.inference_requests) == 2


def test_nonstreaming_tool_call_completes_round_trip() -> None:
    trial, _ = run_tool_probe(
        MockBehavior.TOOL_CALL_STREAM, config_options={"streaming": False}
    )

    assert trial.outcome is OutcomeState.SUCCESS
    assert trial.http_attempts == 2


def test_fragmented_arguments_are_reassembled_in_order() -> None:
    trial, mock = run_tool_probe(MockBehavior.TOOL_CALL_STREAM)

    assert trial.outcome is OutcomeState.SUCCESS
    # The second request must carry the fully reassembled arguments and the
    # matching tool result, not raw fragments.
    continuation = mock.inference_requests[1].body
    assistant = continuation["messages"][1]
    assert assistant["role"] == "assistant"
    assert assistant["tool_calls"][0]["function"]["arguments"] == EXPECTED_ARGUMENTS
    tool_message = continuation["messages"][2]
    assert tool_message["role"] == "tool"
    assert tool_message["tool_call_id"] == assistant["tool_calls"][0]["id"]


def test_multiple_tool_calls_are_assembled_independently() -> None:
    trial, mock = run_tool_probe(MockBehavior.TOOL_CALL_MULTI_STREAM)

    assert trial.outcome is OutcomeState.SUCCESS
    assistant = mock.inference_requests[1].body["messages"][1]
    calls = assistant["tool_calls"]
    assert len(calls) == 2
    assert calls[0]["id"] == "call_mock_1"
    assert calls[1]["id"] == "call_mock_2"
    assert calls[0]["function"]["name"] == "read_file"
    assert calls[1]["function"]["name"] == "list_dir"
    # Both calls must have their own result message.
    results = [m for m in mock.inference_requests[1].body["messages"] if m["role"] == "tool"]
    assert {result["tool_call_id"] for result in results} == {"call_mock_1", "call_mock_2"}


def test_large_tool_observation_round_trips() -> None:
    large = "x" * 200_000
    trial, mock = run_tool_probe(
        MockBehavior.TOOL_CALL_STREAM, call_options={"tool_result_content": large}
    )

    assert trial.outcome is OutcomeState.SUCCESS
    tool_message = mock.inference_requests[1].body["messages"][2]
    assert len(tool_message["content"]) == len(large)


# -- protocol failures -------------------------------------------------------


def test_missing_call_id_is_a_provider_protocol_failure() -> None:
    trial, mock = run_tool_probe(MockBehavior.TOOL_CALL_MISSING_ID)

    assert trial.outcome is OutcomeState.PROTOCOL_FAILURE
    assert trial.outcome_detail == "tool_call_id_missing"
    assert trial.attribution == "provider"
    # Fails before a continuation is attempted.
    assert trial.http_attempts == 1
    assert len(mock.inference_requests) == 1


def test_inconsistent_call_id_is_detected() -> None:
    trial, _ = run_tool_probe(MockBehavior.TOOL_CALL_INCONSISTENT_ID)

    assert trial.outcome is OutcomeState.PROTOCOL_FAILURE
    assert trial.outcome_detail == "tool_call_id_inconsistent"
    assert trial.attribution == "provider"


def test_invalid_json_arguments_are_detected() -> None:
    trial, _ = run_tool_probe(MockBehavior.TOOL_CALL_INVALID_JSON)

    assert trial.outcome is OutcomeState.PROTOCOL_FAILURE
    assert trial.outcome_detail == "tool_call_arguments_invalid_json"
    assert trial.attribution == "provider"


def test_interrupted_tool_stream_is_not_success() -> None:
    trial, _ = run_tool_probe(MockBehavior.TOOL_CALL_INTERRUPTED)

    assert trial.outcome is OutcomeState.STREAM_INTERRUPTED
    assert trial.attribution == "provider"
    assert trial.outcome_detail is not None


def test_absent_tool_call_is_attributed_to_the_model() -> None:
    """A model that answers in prose is not an infrastructure fault."""
    trial, _ = run_tool_probe(MockBehavior.TOOL_CALL_ABSENT)

    assert trial.outcome is OutcomeState.PROTOCOL_FAILURE
    assert trial.outcome_detail == "tool_call_absent"
    assert trial.attribution == "model"


def test_malformed_continuation_is_recorded() -> None:
    trial, _ = run_tool_probe(MockBehavior.CONTINUATION_MALFORMED)

    assert trial.outcome is not OutcomeState.SUCCESS
    assert trial.http_attempts == 2
    assert trial.attribution == "provider"


def test_continuation_timeout_is_distinguished() -> None:
    trial, _ = run_tool_probe(
        MockBehavior.CONTINUATION_HANG,
        config_options={"timeout_s": 0.5},
        hang_seconds=5,
    )

    assert trial.outcome is OutcomeState.TIMEOUT
    assert (trial.outcome_detail or "").startswith("continuation_timeout")
    assert trial.http_attempts == 2


# -- validators --------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "arguments", "expected"),
    [
        ("read_file", EXPECTED_ARGUMENTS, None),
        ("read_file", '{"path": "/a"}', "tool_call_arguments_missing:max_bytes"),
        ("read_file", '{"path": "/a", "max_bytes": "4"}', "tool_call_arguments_wrong_type:max_bytes"),
        ("read_file", '{"path": "/a", "max_bytes": true}', "tool_call_arguments_wrong_type:max_bytes"),
        ("read_file", '{"path": "/a", "max_bytes": 1, "x": 2}', "tool_call_arguments_unexpected:x"),
        ("read_file", "not json", "tool_call_arguments_invalid_json"),
        ("read_file", "[1, 2]", "tool_call_arguments_not_object"),
        ("unknown_tool", "{}", "tool_call_unknown_name:unknown_tool"),
        (None, "{}", "tool_call_name_missing"),
    ],
)
def test_argument_validation(name, arguments, expected) -> None:
    assert validate_tool_arguments(name, arguments) == expected


def test_history_validator_accepts_a_complete_exchange() -> None:
    messages = [
        {"role": "user", "content": "go"},
        {
            "role": "assistant",
            "tool_calls": [{"id": "c1", "function": {"name": "read_file", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
    ]

    assert validate_tool_history(messages) is None


def test_history_validator_detects_missing_result() -> None:
    messages = [
        {"role": "assistant", "tool_calls": [{"id": "c1"}, {"id": "c2"}]},
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
    ]

    assert validate_tool_history(messages) == "tool_result_missing"


def test_history_validator_detects_duplicate_and_orphan() -> None:
    duplicated = [
        {"role": "assistant", "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "tool_call_id": "c1", "content": "a"},
        {"role": "tool", "tool_call_id": "c1", "content": "b"},
    ]
    assert validate_tool_history(duplicated) == "tool_result_duplicated"

    orphaned = [
        {"role": "assistant", "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "tool_call_id": "c1", "content": "a"},
        {"role": "tool", "tool_call_id": "c9", "content": "b"},
    ]
    assert validate_tool_history(orphaned) == "tool_result_orphaned"


# -- evidence is inspectable -------------------------------------------------


def test_tool_probe_emits_inspectable_normalized_evidence() -> None:
    trial, _ = run_tool_probe(MockBehavior.TOOL_CALL_STREAM)

    record = trial.to_dict()
    assert record["outcome"] == "success"
    assert record["attribution"] == "unknown"
    assert record["http_attempts"] == 2
    assert [attempt["attempt_index"] for attempt in record["attempts"]] == [1, 2]
    assert all(attempt["http_status"] == 200 for attempt in record["attempts"])
    assert "token_accounting" in record
    # Round-trippable as JSONL evidence.
    json.dumps(record)
