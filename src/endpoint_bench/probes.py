from __future__ import annotations

import http.client
import json
import time
from dataclasses import dataclass, field, replace
from typing import Any
from urllib.parse import urlparse

from .trials import (
    FINISH_CEILING,
    FINISH_NATURAL,
    HttpAttempt,
    LifecycleState,
    OutcomeState,
    Trial,
    classify_finish_reason,
    utc_now,
)

#: SSE field prefix for OpenAI-compatible streaming responses.
_DATA_PREFIX = "data:"
_CHAT_PATH = "/v1/chat/completions"


class ProbeError(ValueError):
    """Raised for invalid probe configuration, not for endpoint behavior."""


@dataclass(frozen=True)
class ProbeConfig:
    """Everything a single probe needs. Values come from registry/profile."""

    base_url: str
    model_id: str
    timeout_s: float = 10.0
    max_output_tokens: int | None = 32
    streaming: bool = True
    extra_headers: tuple[tuple[str, str], ...] = ()


# -- tool definitions --------------------------------------------------------

READ_FILE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Read a sandboxed file and return its contents.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "max_bytes": {"type": "integer"},
            },
            "required": ["path", "max_bytes"],
            "additionalProperties": False,
        },
    },
}

LIST_DIR_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "list_dir",
        "description": "List a sandboxed directory.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "recursive": {"type": "boolean"},
            },
            "required": ["path", "recursive"],
            "additionalProperties": False,
        },
    },
}

TOOL_DEFINITIONS: tuple[dict[str, Any], ...] = (READ_FILE_TOOL, LIST_DIR_TOOL)

#: Minimal argument contract per tool. `bool` is checked before `int` because
#: Python treats bool as a subclass of int.
_TOOL_ARGUMENT_TYPES: dict[str, dict[str, type]] = {
    "read_file": {"path": str, "max_bytes": int},
    "list_dir": {"path": str, "recursive": bool},
}

#: Synthetic tool observation returned to the endpoint on the second turn.
DEFAULT_TOOL_RESULT = '{"ok": true, "bytes_read": 42}'

#: Purpose-built instruction for tool-call probes. A generic synthetic fixture is
#: ambiguous, and an ambiguous prompt makes a reasoning model spend its whole
#: output budget deliberating instead of emitting the call. That measures the
#: fixture, not the endpoint.
DEFAULT_TOOL_PROMPT = (
    "Read the file /tmp/example.txt using the provided tool and report its size. "
    "Call the tool now; do not ask for clarification."
)

#: Minimum tool-directed prompt length is not meaningful; keep the instruction short.

#: Minimum output budget for a tool-call turn.
#:
#: Reasoning-capable models emit reasoning deltas before the call, and those
#: tokens count against `max_completion_tokens`. Observed on DeepSeek V4.1 Flash:
#: ~46 reasoning chunks preceded a 10-token tool call, so a 32-token text budget
#: truncated the turn entirely and a 256-token budget was marginal. A profile
#: tuned for short text replies must not silently decide this.
TOOL_CALL_MIN_OUTPUT_TOKENS = 512


def parse_base_url(base_url: str) -> tuple[str, str, int | None]:
    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https"):
        raise ProbeError(f"unsupported URL scheme in `{base_url}`")
    if not parsed.hostname:
        raise ProbeError(f"`{base_url}` has no host")
    return parsed.scheme, parsed.hostname, parsed.port


def _connection(config: ProbeConfig) -> http.client.HTTPConnection:
    scheme, host, port = parse_base_url(config.base_url)
    connection_class = (
        http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
    )
    return connection_class(host, port, timeout=config.timeout_s)


def _base_payload(config: ProbeConfig, messages: list[dict[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": config.model_id,
        "messages": messages,
        "stream": config.streaming,
    }
    if config.streaming:
        payload["stream_options"] = {"include_usage": True}
    if config.max_output_tokens is not None:
        payload["max_completion_tokens"] = config.max_output_tokens
    return payload


def _headers(config: ProbeConfig, api_key: str | None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream" if config.streaming else "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    for name, value in config.extra_headers:
        headers[name] = value
    return headers


# -- transport ---------------------------------------------------------------


def _send(
    config: ProbeConfig,
    payload: dict[str, Any],
    trial: Trial,
    *,
    attempt_index: int,
    detail_prefix: str = "",
    api_key: str | None = None,
) -> tuple[Any | None, float]:
    """Send one request, appending exactly one HTTP attempt.

    Returns (response, started) when a response arrived with a non-error status,
    otherwise (None, started) with the trial already classified.
    """
    body = json.dumps(payload).encode()
    started_at = utc_now()
    started = time.perf_counter()
    trial.observe(LifecycleState.STARTED)

    connection = _connection(config)
    try:
        connection.request("POST", _CHAT_PATH, body=body, headers=_headers(config, api_key))
        response = connection.getresponse()
    except TimeoutError:
        trial.attempts.append(
            HttpAttempt(attempt_index, started_at, utc_now(), None, "TimeoutError", "no response")
        )
        return None, _fail(
            trial, OutcomeState.TIMEOUT, f"{detail_prefix}timeout_before_response", started, "provider"
        )
    except (OSError, http.client.HTTPException) as error:
        trial.attempts.append(
            HttpAttempt(attempt_index, started_at, utc_now(), None, type(error).__name__, str(error))
        )
        return None, _fail(
            trial, OutcomeState.CLIENT_ERROR, f"{detail_prefix}transport_error", started, "unknown"
        )

    trial.attempts.append(HttpAttempt(attempt_index, started_at, None, response.status))
    trial.observe(LifecycleState.TRANSPORT_COMPLETED)

    if 300 <= response.status < 400:
        # A redirect is the simplest way for an ostensibly local target to move
        # a request elsewhere, so it is a protocol violation, not a followable
        # instruction.
        return None, _fail(
            trial,
            OutcomeState.PROTOCOL_FAILURE,
            f"{detail_prefix}redirect_not_permitted",
            started,
            "provider",
        )
    if response.status == 429:
        return None, _fail(
            trial, OutcomeState.RATE_LIMITED, f"{detail_prefix}http_429", started, "provider"
        )
    if 500 <= response.status <= 599:
        return None, _fail(
            trial,
            OutcomeState.SERVER_ERROR,
            f"{detail_prefix}http_{response.status}",
            started,
            "provider",
        )
    if 400 <= response.status <= 499:
        return None, _fail(
            trial,
            OutcomeState.CLIENT_ERROR,
            f"{detail_prefix}http_{response.status}",
            started,
            "harness",
        )
    return response, started


def _fail(
    trial: Trial,
    outcome: OutcomeState,
    detail: str,
    started: float,
    attribution: str,
) -> Trial:
    trial.finish_class = classify_finish_reason(trial.finish_reason)
    if outcome is OutcomeState.OUTPUT_TRUNCATED:
        trial.observe(LifecycleState.PROTOCOL_COMPLETED)
    trial.outcome = outcome
    trial.outcome_detail = detail
    trial.attribution = attribution
    trial.duration_ms = (time.perf_counter() - started) * 1000.0
    return trial


def _succeed(trial: Trial, detail: str, started: float) -> Trial:
    trial.finish_class = classify_finish_reason(trial.finish_reason)
    if trial.finish_class != FINISH_NATURAL:
        ceiling = trial.finish_class == FINISH_CEILING
        return _fail(
            trial,
            OutcomeState.OUTPUT_TRUNCATED if ceiling else OutcomeState.PROTOCOL_FAILURE,
            f"finish_reason_{trial.finish_reason or 'absent'}",
            started,
            "harness" if ceiling else "provider",
        )
    trial.observe(LifecycleState.PROTOCOL_COMPLETED)
    trial.outcome = OutcomeState.SUCCESS
    trial.outcome_detail = detail
    trial.attribution = "unknown"
    trial.duration_ms = (time.perf_counter() - started) * 1000.0
    return trial


# -- single-turn probe -------------------------------------------------------


def probe(config: ProbeConfig, *, api_key: str | None, prompt: str, trial: Trial) -> Trial:
    """Run one conformance probe and classify exactly one HTTP attempt.

    Retries are deliberately absent. One trial is one attempt, so attempt counts
    can never diverge from trial counts through implicit client behavior.
    """
    reader = _read_streaming if config.streaming else _read_nonstreaming
    response, started = _send(
        config,
        _base_payload(config, [{"role": "user", "content": prompt}]),
        trial,
        attempt_index=1,
        api_key=api_key,
    )
    if response is None:
        return trial
    return reader(trial, response, started)


def _first_choice(document: dict[str, Any]) -> dict[str, Any] | None:
    choices = document.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    return first if isinstance(first, dict) else None


def _apply_usage(trial: Trial, document: dict[str, Any]) -> None:
    usage = document.get("usage")
    if not isinstance(usage, dict):
        return
    if isinstance(usage.get("prompt_tokens"), int):
        trial.input_tokens = usage["prompt_tokens"]
    if isinstance(usage.get("completion_tokens"), int):
        trial.output_tokens = usage["completion_tokens"]
    if isinstance(usage.get("total_tokens"), int):
        trial.total_tokens = usage["total_tokens"]


def _read_nonstreaming(trial: Trial, response: Any, started: float) -> Trial:
    try:
        raw = response.read()
    except TimeoutError:
        return _fail(trial, OutcomeState.TIMEOUT, "timeout_during_body", started, "provider")
    except (OSError, http.client.HTTPException) as error:
        return _fail(
            trial,
            OutcomeState.STREAM_INTERRUPTED,
            f"body_error:{type(error).__name__}",
            started,
            "provider",
        )

    try:
        document = json.loads(raw)
    except json.JSONDecodeError:
        return _fail(trial, OutcomeState.PROTOCOL_FAILURE, "malformed_json", started, "provider")

    choice = _first_choice(document)
    if choice is None:
        return _fail(trial, OutcomeState.PROTOCOL_FAILURE, "missing_choices", started, "provider")

    _apply_usage(trial, document)
    trial.finish_reason = choice.get("finish_reason")
    if trial.finish_reason is None:
        return _fail(
            trial, OutcomeState.PROTOCOL_FAILURE, "missing_finish_reason", started, "provider"
        )
    if trial.finish_reason == "length":
        return _fail(
            trial, OutcomeState.OUTPUT_TRUNCATED, "finish_reason_length", started, "harness"
        )
    return _succeed(trial, "completed", started)


def _read_streaming(trial: Trial, response: Any, started: float) -> Trial:
    content_type = (response.getheader("Content-Type") or "").lower()
    if content_type and "event-stream" not in content_type:
        return _fail(
            trial,
            OutcomeState.PROTOCOL_FAILURE,
            f"unexpected_content_type:{content_type}",
            started,
            "provider",
        )

    saw_done = False
    saw_chunk = False
    content_chunks = 0

    while True:
        try:
            line = response.readline()
        except TimeoutError:
            detail = "timeout_after_first_token" if content_chunks else "timeout_before_first_token"
            return _fail(trial, OutcomeState.TIMEOUT, detail, started, "provider")
        except (OSError, http.client.HTTPException) as error:
            return _fail(
                trial,
                OutcomeState.STREAM_INTERRUPTED,
                f"stream_error:{type(error).__name__}",
                started,
                "provider",
            )

        if not line:
            break
        text = line.decode("utf-8", errors="replace").strip()
        if not text or not text.startswith(_DATA_PREFIX):
            continue
        data = text[len(_DATA_PREFIX) :].strip()
        if data == "[DONE]":
            saw_done = True
            break
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            return _fail(
                trial, OutcomeState.PROTOCOL_FAILURE, "malformed_sse_json", started, "provider"
            )

        saw_chunk = True
        if trial.ttft_ms is None:
            trial.ttft_ms = (time.perf_counter() - started) * 1000.0
        _apply_usage(trial, chunk)
        choice = _first_choice(chunk)
        if choice is not None:
            if choice.get("finish_reason"):
                trial.finish_reason = choice["finish_reason"]
            delta = choice.get("delta") or {}
            if delta.get("content"):
                content_chunks += 1

    if not saw_chunk:
        return _fail(trial, OutcomeState.PROTOCOL_FAILURE, "empty_stream", started, "provider")
    if trial.finish_reason is None or not saw_done:
        return _fail(
            trial, OutcomeState.STREAM_INTERRUPTED, "unterminated_stream", started, "provider"
        )
    if trial.finish_reason == "length":
        return _fail(
            trial, OutcomeState.OUTPUT_TRUNCATED, "finish_reason_length", started, "harness"
        )
    return _succeed(trial, "completed", started)


# -- tool-call round trip ----------------------------------------------------


@dataclass
class AssembledToolCall:
    """A tool call rebuilt from streamed fragments."""

    index: int
    identifiers: list[str] = field(default_factory=list)
    name: str | None = None
    argument_fragments: list[str] = field(default_factory=list)

    @property
    def call_id(self) -> str | None:
        """The single identifier observed, or None when absent or conflicting."""
        distinct = {value for value in self.identifiers if value}
        if len(distinct) == 1:
            return distinct.pop()
        return None

    @property
    def has_identifier_conflict(self) -> bool:
        return len({value for value in self.identifiers if value}) > 1

    @property
    def arguments(self) -> str:
        return "".join(self.argument_fragments)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "call_id": self.call_id,
            "identifiers_observed": list(self.identifiers),
            "identifier_conflict": self.has_identifier_conflict,
            "name": self.name,
            "arguments": self.arguments,
            "fragment_count": len(self.argument_fragments),
        }


def _assemble_from_delta(
    assembled: dict[int, AssembledToolCall], delta: dict[str, Any]
) -> None:
    calls = delta.get("tool_calls")
    if not isinstance(calls, list):
        return
    for entry in calls:
        if not isinstance(entry, dict):
            continue
        index = entry.get("index")
        if not isinstance(index, int):
            index = 0
        slot = assembled.setdefault(index, AssembledToolCall(index=index))
        call_id = entry.get("id")
        if isinstance(call_id, str):
            slot.identifiers.append(call_id)
        function = entry.get("function")
        if isinstance(function, dict):
            name = function.get("name")
            if isinstance(name, str) and name:
                slot.name = name
            fragment = function.get("arguments")
            if isinstance(fragment, str):
                slot.argument_fragments.append(fragment)


def validate_tool_arguments(name: str | None, arguments: str) -> str | None:
    """Return a problem description, or None when arguments are usable."""
    if not name:
        return "tool_call_name_missing"
    expected = _TOOL_ARGUMENT_TYPES.get(name)
    if expected is None:
        return f"tool_call_unknown_name:{name}"
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        return "tool_call_arguments_invalid_json"
    if not isinstance(parsed, dict):
        return "tool_call_arguments_not_object"
    for key, expected_type in expected.items():
        if key not in parsed:
            return f"tool_call_arguments_missing:{key}"
        value = parsed[key]
        if expected_type is int and isinstance(value, bool):
            return f"tool_call_arguments_wrong_type:{key}"
        if not isinstance(value, expected_type):
            return f"tool_call_arguments_wrong_type:{key}"
    extra = set(parsed) - set(expected)
    if extra:
        return f"tool_call_arguments_unexpected:{sorted(extra)[0]}"
    return None


def validate_tool_history(messages: list[dict[str, Any]]) -> str | None:
    """Check that every requested tool call is answered exactly once."""
    requested: list[str] = []
    answered: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                if isinstance(call, dict) and isinstance(call.get("id"), str):
                    requested.append(call["id"])
        elif message.get("role") == "tool":
            call_id = message.get("tool_call_id")
            if isinstance(call_id, str):
                answered.append(call_id)
    if len(set(answered)) != len(answered):
        return "tool_result_duplicated"
    for call_id in requested:
        if call_id not in answered:
            return "tool_result_missing"
    for call_id in answered:
        if call_id not in requested:
            return "tool_result_orphaned"
    return None


def probe_tool_round_trip(
    config: ProbeConfig,
    *,
    api_key: str | None,
    prompt: str,
    trial: Trial,
    tool_result_content: str = DEFAULT_TOOL_RESULT,
    tool_choice: str = "required",
) -> Trial:
    """Exercise the full tool-call contract across two HTTP attempts.

    1. request a tool call with a declared schema;
    2. consume streamed, fragmented arguments;
    3. reassemble them in order;
    4. validate identifiers, JSON, and argument types;
    5. return a synthetic tool result for the correct call id;
    6. resubmit the complete conversation;
    7. require a complete, terminated continuation.

    One logical trial, two HTTP attempts. Attempt counts stay observable.
    """
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    tool_config = replace(
        config,
        max_output_tokens=max(
            config.max_output_tokens or 0, TOOL_CALL_MIN_OUTPUT_TOKENS
        ),
    )
    first_payload = _base_payload(tool_config, messages)
    first_payload["tools"] = list(TOOL_DEFINITIONS)
    first_payload["tool_choice"] = tool_choice

    response, started = _send(
        config, first_payload, trial, attempt_index=1, api_key=api_key
    )
    if response is None:
        return trial

    calls, problem, finish_reason = _collect_tool_calls(config, response)
    trial.finish_reason = finish_reason
    if problem is not None:
        outcome = (
            OutcomeState.STREAM_INTERRUPTED
            if problem.startswith("unterminated")
            else OutcomeState.PROTOCOL_FAILURE
        )
        return _fail(trial, outcome, problem, started, "provider")
    if not calls:
        if finish_reason == "length":
            # Our own output ceiling cut the turn off before a usable call could
            # be emitted. Blaming the model here would be wrong.
            return _fail(
                trial,
                OutcomeState.OUTPUT_TRUNCATED,
                "tool_call_truncated_by_output_limit",
                started,
                "harness",
            )
        # A capable model may simply answer instead of calling a tool. That is a
        # semantic outcome, not an infrastructure fault.
        return _fail(
            trial, OutcomeState.PROTOCOL_FAILURE, "tool_call_absent", started, "model"
        )

    for call in calls:
        problem = validate_tool_arguments(call.name, call.arguments)
        if problem is not None:
            return _fail(trial, OutcomeState.PROTOCOL_FAILURE, problem, started, "provider")

    assistant_message: dict[str, Any] = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call.call_id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in calls
        ],
    }
    messages.append(assistant_message)
    for call in calls:
        messages.append(
            {
                "role": "tool",
                "tool_call_id": call.call_id,
                "content": tool_result_content,
            }
        )

    history_problem = validate_tool_history(messages)
    if history_problem is not None:
        return _fail(trial, OutcomeState.PROTOCOL_FAILURE, history_problem, started, "harness")

    trial.finish_reason = None
    trial.finish_class = classify_finish_reason(None)
    second_payload = _base_payload(tool_config, messages)
    second_payload["tools"] = list(TOOL_DEFINITIONS)
    response, started = _send(
        config,
        second_payload,
        trial,
        attempt_index=2,
        detail_prefix="continuation_",
        api_key=api_key,
    )
    if response is None:
        return trial

    return _read_continuation(config, response, trial, started)


def _collect_tool_calls(
    config: ProbeConfig, response: Any
) -> tuple[list[AssembledToolCall], str | None, str | None]:
    """Parse the first turn into assembled tool calls.

    Returns (calls, problem, finish_reason). The finish reason is returned so a
    caller can tell "the model chose not to call" apart from "our own output
    ceiling cut the call off".
    """
    if not config.streaming:
        try:
            raw = response.read()
        except (TimeoutError, OSError, http.client.HTTPException):
            return [], "unterminated_tool_response", None
        try:
            document = json.loads(raw)
        except json.JSONDecodeError:
            return [], "malformed_json", None
        choice = _first_choice(document)
        if choice is None:
            return [], "missing_choices", None
        message = choice.get("message") or {}
        slots: dict[int, AssembledToolCall] = {}
        for index, call in enumerate(message.get("tool_calls") or []):
            if not isinstance(call, dict):
                continue
            slot = AssembledToolCall(index=index)
            if isinstance(call.get("id"), str):
                slot.identifiers.append(call["id"])
            function = call.get("function") or {}
            if isinstance(function.get("name"), str):
                slot.name = function["name"]
            if isinstance(function.get("arguments"), str):
                slot.argument_fragments.append(function["arguments"])
            slots[index] = slot
        calls, problem = _finalize_calls(slots)
        return calls, problem, choice.get("finish_reason")

    slots = {}
    saw_done = False
    finish_reason: str | None = None
    while True:
        try:
            line = response.readline()
        except (TimeoutError, OSError, http.client.HTTPException):
            return [], "unterminated_tool_stream", None
        if not line:
            break
        text = line.decode("utf-8", errors="replace").strip()
        if not text or not text.startswith(_DATA_PREFIX):
            continue
        data = text[len(_DATA_PREFIX) :].strip()
        if data == "[DONE]":
            saw_done = True
            break
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            return [], "malformed_sse_json", None
        choice = _first_choice(chunk)
        if choice is not None:
            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]
            _assemble_from_delta(slots, choice.get("delta") or {})

    if not saw_done or finish_reason is None:
        return [], "unterminated_tool_stream", finish_reason
    calls, problem = _finalize_calls(slots)
    return calls, problem, finish_reason


def _finalize_calls(
    slots: dict[int, AssembledToolCall],
) -> tuple[list[AssembledToolCall], str | None]:
    calls = [slots[index] for index in sorted(slots)]
    if not calls:
        return [], None
    for call in calls:
        if call.has_identifier_conflict:
            return [], "tool_call_id_inconsistent"
        if call.call_id is None:
            return [], "tool_call_id_missing"
    return calls, None


def _read_continuation(
    config: ProbeConfig, response: Any, trial: Trial, started: float
) -> Trial:
    """Require a complete, terminated assistant continuation."""
    if not config.streaming:
        try:
            raw = response.read()
        except TimeoutError:
            return _fail(
                trial, OutcomeState.TIMEOUT, "continuation_timeout", started, "provider"
            )
        except (OSError, http.client.HTTPException):
            return _fail(
                trial,
                OutcomeState.STREAM_INTERRUPTED,
                "continuation_interrupted",
                started,
                "provider",
            )
        try:
            document = json.loads(raw)
        except json.JSONDecodeError:
            return _fail(
                trial, OutcomeState.PROTOCOL_FAILURE, "continuation_malformed_json", started, "provider"
            )
        choice = _first_choice(document)
        if choice is None:
            return _fail(
                trial,
                OutcomeState.PROTOCOL_FAILURE,
                "continuation_missing_choices",
                started,
                "provider",
            )
        _apply_usage(trial, document)
        message = choice.get("message") or {}
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            return _fail(
                trial,
                OutcomeState.PROTOCOL_FAILURE,
                "continuation_empty_content",
                started,
                "provider",
            )
        trial.finish_reason = choice.get("finish_reason")
        if trial.finish_reason is None:
            return _fail(
                trial,
                OutcomeState.PROTOCOL_FAILURE,
                "continuation_missing_finish_reason",
                started,
                "provider",
            )
        return _succeed(trial, "tool_round_trip_completed", started)

    saw_done = False
    finish_reason: str | None = None
    content_parts: list[str] = []
    while True:
        try:
            line = response.readline()
        except TimeoutError:
            return _fail(
                trial, OutcomeState.TIMEOUT, "continuation_timeout", started, "provider"
            )
        except (OSError, http.client.HTTPException):
            return _fail(
                trial,
                OutcomeState.STREAM_INTERRUPTED,
                "continuation_interrupted",
                started,
                "provider",
            )
        if not line:
            break
        text = line.decode("utf-8", errors="replace").strip()
        if not text or not text.startswith(_DATA_PREFIX):
            continue
        data = text[len(_DATA_PREFIX) :].strip()
        if data == "[DONE]":
            saw_done = True
            break
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            return _fail(
                trial, OutcomeState.PROTOCOL_FAILURE, "continuation_malformed_sse", started, "provider"
            )
        _apply_usage(trial, chunk)
        choice = _first_choice(chunk)
        if choice is not None:
            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]
            content = (choice.get("delta") or {}).get("content")
            if isinstance(content, str):
                content_parts.append(content)

    if not saw_done or finish_reason is None:
        return _fail(
            trial, OutcomeState.STREAM_INTERRUPTED, "continuation_unterminated", started, "provider"
        )
    if not "".join(content_parts).strip():
        return _fail(
            trial, OutcomeState.PROTOCOL_FAILURE, "continuation_empty_content", started, "provider"
        )
    trial.finish_reason = finish_reason
    return _succeed(trial, "tool_round_trip_completed", started)
