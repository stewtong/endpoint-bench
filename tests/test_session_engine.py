"""Session engine behavior: isolation, scheduling, timing, cache, tools, bounds."""

from __future__ import annotations

import socket
import ssl
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from endpoint_bench import session_engine
from endpoint_bench.mock_provider import MockBehavior, MockProvider
from endpoint_bench.session_engine import (
    OUTCOME_INELIGIBLE_INPUT_BOUND,
    OUTCOME_OUTPUT_TRUNCATED,
    OUTCOME_TOOL_ABSENT,
    SESSION_STATUS_CENSORED,
    SESSION_STATUS_INELIGIBLE_INPUT_BOUND,
    SESSION_STATUS_UNSUPPORTED_TOOL,
    SessionCellRunner,
    SessionTarget,
)
from endpoint_bench.session_program import build_plan, load_program, sha256_text

PROGRAM = Path(__file__).resolve().parents[1] / "configs/session-programs/session-dynamics-v1.json"


@pytest.fixture(scope="module")
def plan():
    program = load_program(PROGRAM)
    return build_plan(program, output_dir=Path("/tmp/unused-session-plan"), release_nonce="test")


def _cell(plan: dict, operation_id: str, *, bound: int | None = None) -> dict:
    cell = dict(next(c for c in plan["operations"] if c["operation_id"] == operation_id))
    cell["session_max_input_tokens"] = bound or (
        131072 if cell["scenario"] == "context_sentinels" else 32768
    )
    return cell


def _run(
    plan: dict,
    operation_id: str,
    behavior: MockBehavior = MockBehavior.STREAM_SUCCESS,
    *,
    bound: int | None = None,
    api_key: str | None = None,
    idle_seconds: float = 0.01,
    max_cell_seconds: float | None = None,
    mock_kwargs: dict | None = None,
):
    provider = MockProvider(behavior=behavior, **(mock_kwargs or {}))
    url = provider.start()
    try:
        runner = SessionCellRunner(
            cell=_cell(plan, operation_id, bound=bound),
            target=SessionTarget(
                base_url=url, model_id="mock-model", timeout_s=30, api_key=api_key
            ),
            idle_seconds=idle_seconds,
            max_cell_seconds=max_cell_seconds,
        )
        evidence = runner.run()
        return evidence, list(provider.requests)
    finally:
        provider.stop()


def _tags(body: dict) -> set[str]:
    """All request-tag session prefixes present in a request body."""
    import json
    import re

    text = json.dumps(body.get("messages"))
    return set(re.findall(r"Request tag: ([0-9a-f]{12})", text))


def _tag_for(request_id: str) -> str:
    return sha256_text(request_id)[:12]


def test_growing_transcript_retains_replies_and_isolates_sessions(plan) -> None:
    evidence, requests = _run(plan, "SD-growing_transcript-C1")

    assert evidence.status == "completed"
    assert len(requests) == 20
    assert all(request.method == "POST" for request in requests)
    tag_to_session = {
        _tag_for(call.request_id): session.session_id
        for session in evidence.sessions
        for call in session.calls
    }
    for request in requests:
        tags = _tags(request.body or {})
        assert tags
        # Every history tag in one request belongs to one session.
        assert len({tag_to_session[tag] for tag in tags if tag in tag_to_session}) == 1

    for session in evidence.sessions:
        assert [call.turn_index for call in session.calls] == [1, 2, 3, 4]
        assert all(call.visible_content for call in session.calls)
        # Dependent turns carry the real assistant reply in their sent messages.
        for turn in (2, 3, 4):
            sent = session.transcript[turn - 1]["messages_sent"]
            assert any(
                message.get("role") == "assistant"
                and message.get("content") == session.calls[turn - 2].visible_content
                for message in sent
            )


def test_independent_has_no_conversation_history(plan) -> None:
    evidence, requests = _run(plan, "SD-independent-C1")

    assert evidence.status == "completed"
    assert len(requests) == 20
    for request in requests:
        messages = request.body["messages"]
        # Role structure is matched with stable-prefix: one system prefix plus
        # one user task. No assistant history is present.
        assert [message["role"] for message in messages] == ["system", "user"]
        assert "assistant" not in {message.get("role") for message in messages}
    tags = {_tag_for(call.request_id) for session in evidence.sessions for call in session.calls}
    assert len(tags) == 20
    leading = {
        request.body["messages"][0]["content"] for request in requests
    }
    assert len(leading) == 20
    assert evidence.cache_prefix_audit["consistent"] is True


def test_stable_prefix_keeps_a_matched_prefix_and_invalidation_changes_it(plan) -> None:
    stable, stable_requests = _run(plan, "SD-stable_prefix-C1")
    invalidated, invalidated_requests = _run(plan, "SD-prefix_invalidation-C1")

    assert stable.status == invalidated.status == "completed"
    for request in stable_requests:
        prefix = request.body["messages"][0]["content"]
        assert len(prefix) == 32768
        assert "Designated facts:" in prefix
    # One namespace per session: five sessions, each with a stable prefix.
    assert len({request.body["messages"][0]["content"] for request in stable_requests}) == 5
    stable_by_session: dict[str, set[str]] = {}
    tag_to_session = {
        _tag_for(call.request_id): call.session_id
        for session in stable.sessions
        for call in session.calls
    }
    for request in stable_requests:
        tags = _tags(request.body or {})
        sessions = {tag_to_session[tag] for tag in tags if tag in tag_to_session}
        assert len(sessions) == 1
        stable_by_session.setdefault(next(iter(sessions)), set()).add(
            request.body["messages"][0]["content"]
        )
    assert all(len(prefixes) == 1 for prefixes in stable_by_session.values())
    assert stable.cache_prefix_audit["consistent"] is True

    by_turn: dict[int, set[str]] = {1: set(), 2: set(), 3: set(), 4: set()}
    tag_to_turn = {
        _tag_for(call.request_id): call.turn_index
        for session in invalidated.sessions
        for call in session.calls
    }
    for request in invalidated_requests:
        tags = _tags(request.body or {})
        turns = {tag_to_turn[tag] for tag in tags if tag in tag_to_turn}
        assert len(turns) == 1
        turn = next(iter(turns))
        by_turn[turn].add(request.body["messages"][0]["content"])
    assert by_turn[1] == by_turn[2]
    assert by_turn[3] == by_turn[4]
    assert by_turn[1] != by_turn[3]
    assert {len(prefix) for prefixes in by_turn.values() for prefix in prefixes} == {32768}
    assert invalidated.cache_prefix_audit["consistent"] is True


def test_interleaved_c4_reaches_four_inflight_calls(plan) -> None:
    evidence, requests = _run(plan, "SD-interleaved_sessions-C4")

    assert evidence.status == "completed"
    assert len(requests) == 20
    assert evidence.achieved_max_inflight_calls == 4
    assert evidence.achieved_max_concurrent_sessions == 4
    assert len({session.session_id for session in evidence.sessions}) == 5
    assert all(entry.endswith("-t01") for entry in evidence.planned_turn_order[:5])
    assert all(entry.endswith("-t02") for entry in evidence.planned_turn_order[5:10])
    assert evidence.actual_turn_order
    assert all(session.attempted_calls == 4 for session in evidence.sessions)


def test_client_reconnect_changes_connection_but_keeps_transcript(plan) -> None:
    evidence, _ = _run(
        plan, "SD-client_reconnect-C1", mock_kwargs={"keep_alive": True}
    )
    session = evidence.sessions[0]

    assert [call.connection_id for call in session.calls] == ["c1", "c1", "c2", "c2"]
    assert [call.reconnected for call in session.calls] == [False, False, True, False]
    # Observed reuse: turn 3 really opens a second transport, and only then.
    assert [call.connection_opened for call in session.calls] == [
        True,
        False,
        True,
        False,
    ]
    assert [call.connection_reused for call in session.calls] == [
        False,
        True,
        False,
        True,
    ]
    assert [call.connections_opened_total for call in session.calls] == [1, 1, 2, 2]
    assert len(session.transcript[2]["messages_sent"]) >= 5
    assert any(
        message.get("role") == "assistant" and message.get("content")
        for message in session.transcript[2]["messages_sent"]
    )


def test_server_closed_stream_records_no_reuse(plan) -> None:
    """The default mock closes each streamed response; reuse must not be claimed."""
    evidence, _ = _run(plan, "SD-growing_transcript-C1")
    session = evidence.sessions[0]

    assert evidence.status == "completed"
    assert [call.connection_opened for call in session.calls] == [True] * 4
    assert [call.connection_reused for call in session.calls] == [False] * 4
    assert [call.connections_opened_total for call in session.calls] == [1, 2, 3, 4]


_SESSION_CHUNK = (
    b'data: {"choices":[{"index":0,"delta":{"content":"ok"},'
    b'"finish_reason":"stop"}],"usage":{"prompt_tokens":10,'
    b'"completion_tokens":1,"total_tokens":11}}\n\ndata: [DONE]\n\n'
)


class _PortServer:
    """Loopback HTTP/1.1 SSE peer that records the client port per request.

    Modes:
      keep_alive  — Content-Length response; the socket stays open.
      close_header— explicit `Connection: close`.
      silent_close— complete response, then a write-side FIN with no close header.
      hang_first  — the first request never answers; later requests do.
    """

    def __init__(self, mode: str = "keep_alive") -> None:
        self.mode = mode
        self.url = ""
        self.requests: list[int] = []
        self._server: ThreadingHTTPServer | None = None
        self._lock = threading.Lock()

    def __enter__(self) -> _PortServer:
        mode = self.mode
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args) -> None:  # silence test output
                return

            def do_POST(self) -> None:  # noqa: N802 - stdlib naming
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                with owner._lock:
                    owner.requests.append(self.client_address[1])
                    index = len(owner.requests)
                if mode == "hang_first" and index == 1:
                    time.sleep(3.0)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(_SESSION_CHUNK)))
                if mode == "close_header":
                    self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(_SESSION_CHUNK)
                self.wfile.flush()
                if mode == "silent_close":
                    self.connection.shutdown(socket.SHUT_WR)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        self._server = server
        threading.Thread(target=server.serve_forever, daemon=True).start()
        host, port = server.server_address[:2]
        self.url = f"http://{host}:{port}"
        return self

    def __exit__(self, *exc: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


def _run_port_cell(plan: dict, url: str, operation_id: str):
    cell = _cell(plan, operation_id)
    cell["concurrent_sessions"] = 1
    cell["session_count"] = 1
    cell["session_order"] = [cell["sessions"][0]["session_id"]]
    runner = SessionCellRunner(
        cell=cell,
        target=SessionTarget(base_url=url, model_id="mock-model", timeout_s=5),
        idle_seconds=0.0,
        max_cell_seconds=10.0,
    )
    return runner.run()


def test_persistent_session_reuses_one_tcp_connection(plan) -> None:
    with _PortServer("keep_alive") as server:
        evidence = _run_port_cell(plan, server.url, "SD-growing_transcript-C1")
        ports = list(server.requests)

    session = evidence.sessions[0]
    assert evidence.status == "completed"
    assert ports == [ports[0]] * 4
    assert [call.connection_measured for call in session.calls] == [True] * 4
    assert [call.connection_opened for call in session.calls] == [True] + [False] * 3
    assert [call.connection_reused for call in session.calls] == [False] + [True] * 3
    assert [call.connections_opened_total for call in session.calls] == [1, 1, 1, 1]
    # Resolution and connect belong to the first turn only; reuse skips both.
    assert session.calls[0].connection_resolve_ms is not None
    assert session.calls[0].connection_connect_ms is not None
    assert all(call.connection_resolve_ms is None for call in session.calls[1:])
    assert all(call.connection_connect_ms is None for call in session.calls[1:])


def test_turn_three_reconnect_opens_exactly_two_connections(plan) -> None:
    with _PortServer("keep_alive") as server:
        evidence = _run_port_cell(plan, server.url, "SD-client_reconnect-C1")
        ports = list(server.requests)

    session = evidence.sessions[0]
    assert ports[0] == ports[1]
    assert ports[2] == ports[3]
    assert ports[0] != ports[2]
    assert [call.connection_opened for call in session.calls] == [
        True,
        False,
        True,
        False,
    ]
    assert [call.connection_reused for call in session.calls] == [
        False,
        True,
        False,
        True,
    ]
    assert [call.connections_opened_total for call in session.calls] == [1, 1, 2, 2]


def test_connection_close_header_is_observed_as_a_new_transport(plan) -> None:
    with _PortServer("close_header") as server:
        evidence = _run_port_cell(plan, server.url, "SD-growing_transcript-C1")
        ports = list(server.requests)

    session = evidence.sessions[0]
    assert evidence.status == "completed"
    assert [call.connection_opened for call in session.calls] == [True] * 4
    assert [call.connection_reused for call in session.calls] == [False] * 4
    assert ports == sorted(ports)
    assert len(set(ports)) == 4


def test_silently_closed_connection_is_not_reused(plan) -> None:
    """An idle socket the peer half-closed is replaced before the next send."""
    with _PortServer("silent_close") as server:
        evidence = _run_port_cell(plan, server.url, "SD-growing_transcript-C1")
        ports = list(server.requests)

    session = evidence.sessions[0]
    assert evidence.status == "completed"
    assert [call.connection_prior_closed for call in session.calls] == [
        False,
        True,
        True,
        True,
    ]
    assert [call.connection_opened for call in session.calls] == [True] * 4
    assert [call.connection_reused for call in session.calls] == [False] * 4
    assert len(set(ports)) == 4
    assert all(call.connection_reuse_error is None for call in session.calls)


def test_guard_fired_socket_is_not_reused_by_a_later_call(plan) -> None:
    """A shut-down socket never carries the next request."""
    with _PortServer("hang_first") as server:
        cell = _cell(plan, "SD-growing_transcript-C1")
        cell["concurrent_sessions"] = 1
        runner = SessionCellRunner(
            cell=cell,
            target=SessionTarget(
                base_url=server.url, model_id="mock-model", timeout_s=0.15
            ),
            idle_seconds=0.0,
            max_cell_seconds=10.0,
        )
        runner._cell_start = runner.clock()
        runner._cell_deadline = runner._cell_start + 10.0
        state = runner._build_states(None)[0]
        request_id = state.evidence.session_id

        def call(turn: int):
            return runner._call(
                state,
                turn=turn,
                request_id=f"{request_id}-t{turn:02d}",
                messages=state.messages,
                ready_at=runner.clock(),
                idle_before_ms=0.0,
                tools=None,
                cache_group=state.spec["turn_group_ids"][turn - 1],
            )

        first = call(1)
        assert first.outcome == "timeout"
        assert first.outcome_detail == "request_timeout_during_transport"
        assert first.connection_opened is True
        # The guard fired, so the runner must not hold the shut-down socket.
        assert state.connection is None

        second = call(2)
        ports = list(server.requests)

    assert second.outcome == "success"
    assert second.connection_opened is True
    assert second.connection_reused is False
    assert second.connections_opened_total == 2
    assert len(ports) == 2
    assert ports[0] != ports[1]



def test_idle_resume_reports_idle_separately(plan) -> None:
    evidence, _ = _run(plan, "SD-idle_resume-C1", idle_seconds=0.05)
    session = evidence.sessions[0]

    assert evidence.status == "completed"
    assert session.idle_ms >= 40
    assert session.active_ms is not None and session.elapsed_ms is not None
    assert session.active_ms < session.elapsed_ms
    assert session.calls[2].idle_before_ms >= 40
    assert session.calls[0].idle_before_ms == 0


def test_explicit_compaction_is_local_and_preserves_facts(plan) -> None:
    evidence, _ = _run(plan, "SD-explicit_compaction-C1")
    session = evidence.sessions[0]
    compaction = session.compaction

    assert evidence.status == "completed"
    assert compaction["kind"] == "deterministic_local"
    assert compaction["no_inference"] is True
    assert compaction["verified"] is True
    assert compaction["after_tokens_estimated"] < compaction["before_tokens_estimated"]
    turn_three = session.transcript[2]["messages_sent"]
    text = __import__("json").dumps(turn_three)
    for fact in compaction["preserved_facts"]:
        assert fact in text
    assert "Compacted transcript" in text
    assert session.attempted_calls == 4


def test_tool_roundtrip_executes_only_fixed_in_process_tools(plan) -> None:
    evidence, _ = _run(plan, "SD-tool_roundtrip-C1", MockBehavior.TOOL_CALL_STREAM)

    assert evidence.status == "completed"
    assert evidence.counters()["attempted_calls"] == 10
    for session in evidence.sessions:
        assert session.attempted_calls == 2
        first, second = session.calls
        assert first.tools_sent is True
        assert first.tool_calls
        assert first.tool_results
        result = first.tool_results[0]["result"]
        assert result["ok"] is True
        assert "alpha" in result["content"]
        assert second.tool_calls == []
        assert second.visible_content


def test_unknown_tool_is_recorded_as_unsupported_without_substitution(plan) -> None:
    evidence, _ = _run(plan, "SD-tool_roundtrip-C1", MockBehavior.TOOL_CALL_UNKNOWN)

    assert evidence.counters()["unsupported_tool_calls"] == 5
    for session in evidence.sessions:
        assert session.status == SESSION_STATUS_UNSUPPORTED_TOOL
        assert session.attempted_calls == 1
        assert session.calls[0].unsupported_tool == "read_secret"


def test_absent_tool_call_is_an_outcome_not_a_retry(plan) -> None:
    evidence, requests = _run(plan, "SD-tool_roundtrip-C1", MockBehavior.TOOL_CALL_ABSENT)

    assert len(requests) == 5
    assert evidence.counters()["attempted_calls"] == 5
    for session in evidence.sessions:
        assert session.attempted_calls == 1
        assert session.stop_reason == OUTCOME_TOOL_ABSENT


def test_visible_content_timing_is_separate_from_reasoning_and_any_token(plan) -> None:
    evidence, _ = _run(
        plan, "SD-growing_transcript-C1", MockBehavior.STREAM_REASONING_THEN_CONTENT
    )
    call = evidence.sessions[0].calls[0]

    assert call.ttft_any_ms is not None
    assert call.ttft_reasoning_ms is not None
    assert call.ttft_visible_ms is not None
    assert call.ttft_any_ms <= call.ttft_reasoning_ms <= call.ttft_visible_ms
    assert call.reasoning_chars > 0
    assert call.visible_content
    assert call.provider_reasoning_tokens == 5


def test_fragmented_sse_events_are_reassembled(plan) -> None:
    evidence, _ = _run(plan, "SD-growing_transcript-C1", MockBehavior.STREAM_FRAGMENTED_SSE)

    assert evidence.status == "completed"
    assert evidence.counters()["successful_calls"] == 20
    assert all(session.calls[-1].visible_content for session in evidence.sessions)


def test_visible_truncation_keeps_content_and_censors_the_session(plan) -> None:
    evidence, requests = _run(
        plan, "SD-growing_transcript-C1", MockBehavior.STREAM_VISIBLE_TRUNCATED
    )

    assert len(requests) == 5
    assert evidence.counters()["truncated_calls"] == 5
    for session in evidence.sessions:
        assert session.status == SESSION_STATUS_CENSORED
        call = session.calls[0]
        assert call.outcome == OUTCOME_OUTPUT_TRUNCATED
        assert call.truncated is True
        assert call.visible_content
        assert call.ttft_visible_ms is not None


def test_pre_send_input_bound_marks_ineligible_without_a_request(plan) -> None:
    evidence, requests = _run(plan, "SD-independent-C1", bound=100)

    assert requests == []
    assert evidence.counters()["attempted_calls"] == 0
    assert evidence.counters()["skipped_calls"] >= 1
    first_session = evidence.sessions[0]
    assert first_session.status == SESSION_STATUS_INELIGIBLE_INPUT_BOUND
    assert first_session.calls[0].outcome == OUTCOME_INELIGIBLE_INPUT_BOUND
    assert first_session.calls[0].input_bound_exceeded is True


def test_provider_reported_input_over_bound_stops_the_session(plan) -> None:
    evidence, requests = _run(
        plan,
        "SD-growing_transcript-C1",
        bound=32768,
        mock_kwargs={"prompt_tokens_override": 40000},
    )

    assert len(requests) == 5
    for session in evidence.sessions:
        assert session.attempted_calls == 1
        assert session.calls[0].input_bound_exceeded is True
        assert session.status == SESSION_STATUS_INELIGIBLE_INPUT_BOUND


def test_rate_limit_stops_the_cell_without_retry(plan) -> None:
    evidence, requests = _run(plan, "SD-independent-C1", MockBehavior.RATE_LIMITED)

    assert len(requests) == 1
    assert evidence.status == "stopped_rate_limit"
    assert evidence.counters()["failed_calls"] == 1


def test_server_error_is_counted_and_not_retried(plan) -> None:
    evidence, requests = _run(plan, "SD-independent-C1", MockBehavior.SERVER_ERROR)

    assert len(requests) == 5
    assert evidence.counters()["failed_calls"] == 5
    for session in evidence.sessions:
        assert session.calls[0].http_status == 503


def test_cell_deadline_stops_before_a_request(plan) -> None:
    evidence, requests = _run(
        plan, "SD-independent-C1", max_cell_seconds=0.0
    )

    assert requests == []
    assert evidence.status == "stopped_deadline"
    assert evidence.counters()["attempted_calls"] == 0


def test_cache_headers_are_explicit_and_affinity_is_absent(plan) -> None:
    evidence, requests = _run(plan, "SD-independent-C1", api_key="secret-value")

    assert len(requests) == 20
    for request in requests:
        assert request.has_header("X-Cache-Group")
        assert request.has_header("X-Session-Id")
        assert not request.has_header("X-Session-Affinity")
    for session in evidence.sessions:
        for call in session.calls:
            assert "X-Cache-Group" in call.request_headers
            assert call.request_headers["Authorization"] == "<redacted>"
            assert "secret-value" not in str(call.to_dict())


def test_context_sentinel_echo_is_demonstrated_effective(plan) -> None:
    evidence, _ = _run(plan, "CTX-32768-C1", MockBehavior.SENTINEL_ECHO)

    assert evidence.counters()["context_api_accepted"] == 6
    assert evidence.counters()["context_demonstrated_effective"] == 6
    assert evidence.counters()["context_unverified"] == 0
    assert len(evidence.context_checks) == 6


def test_context_accepted_but_wrong_answer_is_unverified_not_truncated(plan) -> None:
    evidence, _ = _run(plan, "CTX-32768-C1", MockBehavior.STREAM_SUCCESS)

    assert evidence.counters()["context_api_accepted"] == 6
    assert evidence.counters()["context_unverified"] == 6
    assert evidence.counters()["context_truncated"] == 0
    assert evidence.status == "completed"


def test_context_malformed_response_is_separate_from_unverified(plan) -> None:
    evidence, _ = _run(plan, "CTX-32768-C1", MockBehavior.MALFORMED_JSON)

    assert evidence.counters()["context_api_accepted"] == 6
    assert evidence.counters()["context_malformed_response"] == 6
    assert evidence.counters()["context_unverified"] == 0
    assert evidence.counters()["context_truncated"] == 0


# -- F1/F2/F3 regressions ---------------------------------------------------


def test_131k_context_cell_sends_all_six_calls_within_its_bound(plan) -> None:
    """The reviewed implementation sent zero requests and called it completed."""
    evidence, requests = _run(
        plan, "CTX-131072-C1", MockBehavior.SENTINEL_ECHO, bound=131072
    )

    assert evidence.status == "completed"
    assert evidence.evidence_complete is True
    assert len(requests) == 6
    assert evidence.counters()["context_api_accepted"] == 6
    assert evidence.counters()["context_demonstrated_effective"] == 6
    for check in evidence.context_checks:
        assert check["requested_input_tokens"] == 131072
        assert check["client_estimated_input_tokens"] <= 131072
        assert check["delivered_size_conformance"] is True
    assert evidence.cache_prefix_audit["consistent"] is True


def test_all_ineligible_context_cell_is_not_reported_as_measured(plan) -> None:
    evidence, requests = _run(plan, "CTX-32768-C1", bound=100)

    assert requests == []
    assert evidence.status == "not_measured"
    assert evidence.stop_reason == "all_calls_ineligible_input_bound"
    assert evidence.counters()["attempted_calls"] == 0
    assert evidence.counters()["skipped_calls"] == 6
    # Every planned slot is accounted for, but nothing was measured: the two
    # statements stay distinct.
    assert evidence.evidence_complete is True


def test_context_cell_cannot_send_after_its_deadline(plan) -> None:
    evidence, requests = _run(
        plan, "CTX-32768-C1", max_cell_seconds=0.0
    )

    assert requests == []
    assert evidence.status == "stopped_deadline"
    assert evidence.stop_reason == "cell_deadline"
    assert evidence.evidence_complete is False


def test_slow_stream_is_terminated_at_the_cell_deadline(plan) -> None:
    import time

    started = time.monotonic()
    evidence, requests = _run(
        plan,
        "SD-independent-C1",
        MockBehavior.STREAM_SUCCESS,
        max_cell_seconds=0.25,
        mock_kwargs={"chunk_delay_s": 0.3},
    )
    elapsed = time.monotonic() - started

    assert evidence.status == "stopped_deadline"
    assert evidence.stop_reason == "cell_deadline"
    assert len(requests) <= 1
    assert elapsed < 3.0
    assert all(
        call.outcome in ("timeout", "skipped")
        for session in evidence.sessions
        for call in session.calls
    )


class _DribbleServer:
    """Loopback peer that keeps one HTTP phase incomplete byte by byte.

    The socket timeout is refreshed by every arriving byte, which is exactly the
    condition that bypasses a per-read timeout. The response is intentionally
    never completed, so only an absolute cancellation bound can end the call.
    """

    def __init__(self, mode: str, *, interval: float = 0.02, byte_count: int = 300) -> None:
        self.mode = mode
        self.interval = interval
        self.byte_count = byte_count
        self._server: ThreadingHTTPServer | None = None

    def __enter__(self) -> str:
        mode = self.mode
        interval = self.interval
        byte_count = self.byte_count

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args) -> None:  # silence test output
                return

            def do_POST(self) -> None:  # noqa: N802 - stdlib naming
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                try:
                    if mode == "headers":
                        self.wfile.write(b"HTTP/1.1 200 OK\r\n")
                        payload = (
                            b"Content-Type: text/event-stream\r\nX-Dribble: "
                            + b"a" * byte_count
                        )
                    elif mode == "sse":
                        self.wfile.write(
                            b"HTTP/1.1 200 OK\r\n"
                            b"Content-Type: text/event-stream\r\n"
                            b"Connection: close\r\n\r\n"
                        )
                        self.wfile.flush()
                        payload = b'data: {"id":"dribble","choices":'
                    else:
                        self.wfile.write(
                            b"HTTP/1.1 200 OK\r\n"
                            b"Content-Type: application/json\r\n"
                            b"Content-Length: 100000\r\n"
                            b"Connection: close\r\n\r\n"
                        )
                        self.wfile.flush()
                        payload = b'{"id":"dribble","choices":'
                    for byte in payload[:byte_count]:
                        self.wfile.write(bytes((byte,)))
                        self.wfile.flush()
                        time.sleep(interval)
                except (BrokenPipeError, ConnectionResetError):
                    return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        self._server = server
        threading.Thread(target=server.serve_forever, daemon=True).start()
        host, port = server.server_address[:2]
        return f"http://{host}:{port}"

    def __exit__(self, *exc: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


def _run_one_call(
    plan: dict,
    url: str,
    *,
    streaming: bool = True,
    timeout_s: float = 1.0,
    max_cell_seconds: float = 0.12,
    max_total_attempts: int | None = None,
):
    cell = _cell(plan, "SD-independent-C1")
    cell["concurrent_sessions"] = 1
    runner = SessionCellRunner(
        cell=cell,
        target=SessionTarget(
            base_url=url,
            model_id="mock-model",
            timeout_s=timeout_s,
            streaming=streaming,
        ),
        max_cell_seconds=max_cell_seconds,
        max_total_attempts=max_total_attempts,
    )
    started = time.monotonic()
    evidence = runner.run()
    return evidence, time.monotonic() - started


@pytest.mark.parametrize(
    ("mode", "streaming"),
    [("headers", True), ("sse", True), ("json", False)],
)
def test_byte_dribble_cannot_extend_the_absolute_cell_deadline(
    plan, mode: str, streaming: bool
) -> None:
    """Continuous bytes in a header or body line are still bounded."""
    with _DribbleServer(mode) as url:
        evidence, elapsed = _run_one_call(plan, url, streaming=streaming)

    assert evidence.status == "stopped_deadline"
    assert evidence.stop_reason == "cell_deadline"
    assert evidence.attempted_calls == 1
    assert evidence.evidence_complete is False
    assert elapsed < 0.8
    call = evidence.sessions[0].calls[0]
    assert call.outcome == "timeout"
    assert call.outcome_detail == "cell_deadline_during_transport"


def test_byte_dribble_is_bounded_by_the_absolute_request_timeout(plan) -> None:
    """A long cell does not turn a slow provider into an unbounded request."""
    with _DribbleServer("sse") as url:
        evidence, elapsed = _run_one_call(
            plan,
            url,
            timeout_s=0.12,
            max_cell_seconds=10.0,
            max_total_attempts=1,
        )

    call = evidence.sessions[0].calls[0]
    assert call.outcome == "timeout"
    assert call.outcome_detail == "request_timeout_during_transport"
    assert elapsed < 0.8


class _JumpClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class _SlowJournal:
    """Advances a fake clock inside the durable-intent call."""

    def __init__(self, clock: _JumpClock, jump: float) -> None:
        self.clock = clock
        self.jump = jump

    def attempt_started(self, request_id: str, metadata: dict) -> None:
        self.clock.now += self.jump

    def attempt_finished(self, call: object) -> None:
        return None


def test_deadline_expiry_during_journal_work_never_sends(plan) -> None:
    """The pre-send recheck runs after durable intent, not just before it."""
    clock = _JumpClock()
    provider = MockProvider(behavior=MockBehavior.STREAM_SUCCESS)
    url = provider.start()
    cell = _cell(plan, "SD-independent-C1")
    cell["concurrent_sessions"] = 1
    try:
        runner = SessionCellRunner(
            cell=cell,
            target=SessionTarget(base_url=url, model_id="mock-model", timeout_s=1),
            journal=_SlowJournal(clock, jump=0.5),
            clock=clock,
            sleep=clock.sleep,
            idle_seconds=0.0,
            max_cell_seconds=0.25,
        )
        evidence = runner.run()
    finally:
        provider.stop()

    assert provider.requests == []
    assert evidence.status == "stopped_deadline"
    assert evidence.stop_reason == "cell_deadline"
    assert evidence.attempted_calls == 0


def test_request_budget_expiry_during_journal_work_never_sends(plan) -> None:
    """A long cell does not let journal work consume the request budget."""
    clock = _JumpClock()
    provider = MockProvider(behavior=MockBehavior.STREAM_SUCCESS)
    url = provider.start()
    cell = _cell(plan, "SD-independent-C1")
    cell["concurrent_sessions"] = 1
    try:
        runner = SessionCellRunner(
            cell=cell,
            target=SessionTarget(base_url=url, model_id="mock-model", timeout_s=1),
            journal=_SlowJournal(clock, jump=2.0),
            clock=clock,
            sleep=clock.sleep,
            idle_seconds=0.0,
            max_cell_seconds=10.0,
        )
        evidence = runner.run()
    finally:
        provider.stop()

    assert provider.requests == []
    assert evidence.attempted_calls == 0
    assert evidence.stop_reason == "request_budget_exhausted"


class _FakeSock:
    def __init__(self) -> None:
        self.released = threading.Event()

    def settimeout(self, seconds: float) -> None:
        return None

    def shutdown(self, how: int) -> None:
        self.released.set()


class _BlockingSend:
    """A real connection object whose send blocks until the guard shuts it down."""

    def __init__(self, target: object, timeout_s: float | None = None) -> None:
        self.sock = _FakeSock()
        self.timeout = timeout_s

    def connect(self) -> None:
        return None

    def request(self, *args, **kwargs) -> None:
        self.sock.released.wait(5)
        raise OSError("send cancelled")

    def close(self) -> None:
        return None


def test_a_blocked_send_is_cancelled_at_the_cell_deadline(plan, monkeypatch) -> None:
    monkeypatch.setattr(
        "endpoint_bench.session_engine._open_connection",
        lambda target, timeout_s=None: _BlockingSend(None),
    )
    cell = _cell(plan, "SD-independent-C1")
    cell["concurrent_sessions"] = 1
    runner = SessionCellRunner(
        cell=cell,
        target=SessionTarget(
            base_url="http://127.0.0.1:1", model_id="mock-model", timeout_s=1
        ),
        max_cell_seconds=0.12,
    )
    started = time.monotonic()
    evidence = runner.run()
    elapsed = time.monotonic() - started

    assert evidence.status == "stopped_deadline"
    assert evidence.attempted_calls == 1
    assert elapsed < 0.8
    call = evidence.sessions[0].calls[0]
    assert call.outcome == "timeout"
    assert call.outcome_detail == "cell_deadline_during_transport"


def _helper_process(code: str):
    return subprocess.Popen(
        [sys.executable, "-I", "-c", code],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )


def test_slow_resolution_is_bounded_before_a_socket_exists(plan, monkeypatch) -> None:
    """Inject a slow fresh interpreter rather than inheriting a monkeypatch."""
    children = []

    def launch(host, port):
        child = _helper_process("import time; time.sleep(5)")
        children.append(child)
        return child

    monkeypatch.setattr(session_engine, "_start_resolver", launch)
    with MockProvider(keep_alive=True) as provider:
        evidence, elapsed = _run_one_call(plan, provider.base_url)
        assert provider.requests == []
    assert evidence.status == "stopped_deadline"
    assert evidence.attempted_calls == 1
    assert elapsed < 0.6
    assert children and all(child.poll() is not None for child in children)
    assert all(child.stdout.closed for child in children)


@pytest.mark.parametrize("failure", [OSError, KeyboardInterrupt])
def test_resolver_ipc_failure_and_cancellation_reap_child(monkeypatch, failure) -> None:
    children = []

    def launch(host, port):
        child = _helper_process("import time; time.sleep(5)")
        children.append(child)

        def broken_communicate(*args, **kwargs):
            raise failure("injected IPC failure")

        monkeypatch.setattr(child, "communicate", broken_communicate)
        return child

    monkeypatch.setattr(session_engine, "_start_resolver", launch)
    started = time.monotonic()
    with pytest.raises(failure):
        session_engine._resolve_addresses_bounded("127.0.0.1", 1, timeout_s=.12)
    assert time.monotonic() - started < .6
    assert all(child.poll() is not None and child.stdout.closed for child in children)


@pytest.mark.parametrize("code,exception", [
    ("import os,time; os.close(1); time.sleep(5)", TimeoutError),
    ("raise SystemExit(7)", ConnectionError),
    ("print('bad JSON')", ConnectionError),
    ("print('[]')", ConnectionError),
    ("print('{\"addresses\": []}')", ConnectionError),
    ("print('{\"addresses\": [[2,1,6,[\"example.test\",1]]]}')", ConnectionError),
])
def test_resolver_eof_exit_and_malformed_output_are_bounded(monkeypatch, code, exception):
    children = []

    def launch(host, port):
        child = _helper_process(code)
        children.append(child)
        return child

    monkeypatch.setattr(session_engine, "_start_resolver", launch)
    started = time.monotonic()
    with pytest.raises(exception):
        session_engine._resolve_addresses_bounded("127.0.0.1", 1, timeout_s=.3)
    assert time.monotonic() - started < .8
    assert all(child.poll() is not None and child.stdout.closed for child in children)


def test_resolver_startup_consumes_existing_budget(monkeypatch):
    clock = _JumpClock()
    children = []

    def launch(host, port):
        child = _helper_process("import time; time.sleep(5)")
        children.append(child)
        clock.now += .4
        return child

    monkeypatch.setattr(session_engine, "_start_resolver", launch)
    with pytest.raises(TimeoutError, match="startup"):
        session_engine._resolve_addresses_bounded("127.0.0.1", 1, timeout_s=.12, clock=clock)
    assert all(child.poll() is not None and child.stdout.closed for child in children)


def test_concurrent_resolvers_have_independent_owned_children():
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(session_engine._resolve_addresses_bounded,
                               "127.0.0.1", 12345, timeout_s=2) for _ in range(4)]
        results = [future.result(timeout=3) for future in futures]
    assert len(results) == 4
    assert all(addresses and elapsed >= 0 for addresses, elapsed in results)


@pytest.mark.parametrize("deadline", [.12, .5])
def test_address_candidates_share_remaining_connect_budget(monkeypatch, deadline):
    """No host-network blackhole: exercise the real factory's budget boundary."""
    clock = _JumpClock()
    connection = session_engine._open_connection(
        SessionTarget(base_url="http://127.0.0.1:1", model_id="mock-model")
    )
    opened = []

    class ControlledSocket:
        def __init__(self, *args):
            self.timeout = None
            self.closed = False
            opened.append(self)

        def settimeout(self, timeout):
            self.timeout = timeout

        def connect(self, address):
            clock.now += .2
            raise ConnectionRefusedError("controlled refusal")

        def close(self):
            self.closed = True

    monkeypatch.setattr(session_engine.socket, "socket", ControlledSocket)
    addresses = [(2, 1, 6, ("127.0.0.1", p)) for p in (1, 2, 3, 4)]
    create = session_engine._bounded_socket_factory(
        connection, addresses, clock=clock, deadline=deadline
    )
    with pytest.raises(OSError):
        create(("ignored", 1))
    assert len(opened) == (1 if deadline == .12 else 3)
    assert all(sock.closed and 0 < sock.timeout <= deadline for sock in opened)
    assert connection.sock is None


def test_tls_hostname_is_preserved_when_connecting_to_a_resolved_address(
    monkeypatch,
) -> None:
    """Resolution substitutes the address for TCP only, never for SNI/verification."""
    captured: dict[str, object] = {}

    class _ReachedTls(Exception):
        pass

    def fake_wrap(context, sock, *, server_hostname=None, **kwargs):
        captured["server_hostname"] = server_hostname
        raise _ReachedTls

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    try:
        monkeypatch.setattr(ssl.SSLContext, "wrap_socket", fake_wrap)
        connection = session_engine._open_connection(
            SessionTarget(base_url=f"https://example.test:{port}", model_id="m"),
            timeout_s=1,
        )
        addresses, _ = session_engine._resolve_addresses_bounded(
            "127.0.0.1", port, timeout_s=1
        )
        started = time.monotonic()
        connection._create_connection = session_engine._bounded_socket_factory(
            connection, addresses, clock=time.monotonic, deadline=started + 1
        )
        with pytest.raises(_ReachedTls):
            connection.connect()
    finally:
        listener.close()

    assert captured["server_hostname"] == "example.test"


def test_queue_time_excludes_deliberate_idle(plan) -> None:
    class FakeClock:
        def __init__(self) -> None:
            self.now = 0.0

        def __call__(self) -> float:
            return self.now

        def sleep(self, seconds: float) -> None:
            self.now += seconds

    clock = FakeClock()
    provider = MockProvider(behavior=MockBehavior.STREAM_SUCCESS)
    url = provider.start()
    try:
        runner = SessionCellRunner(
            cell=_cell(plan, "SD-idle_resume-C1"),
            target=SessionTarget(base_url=url, model_id="mock-model", timeout_s=30),
            clock=clock,
            sleep=clock.sleep,
            idle_seconds=60.0,
        )
        evidence = runner.run()
    finally:
        provider.stop()

    turn_three = evidence.sessions[0].calls[2]
    assert turn_three.idle_before_ms == pytest.approx(60_000.0)
    assert turn_three.queue_ms == pytest.approx(0.0)
    assert evidence.sessions[0].idle_ms == pytest.approx(60_000.0)


def test_independent_and_stable_prefixes_are_isolated_by_sent_bytes(plan) -> None:
    independent, independent_requests = _run(plan, "SD-independent-C1")
    stable, stable_requests = _run(plan, "SD-stable_prefix-C1")
    invalidated, invalidated_requests = _run(plan, "SD-prefix_invalidation-C1")

    independent_prefixes = {
        request.body["messages"][0]["content"] for request in independent_requests
    }
    stable_prefixes = {
        request.body["messages"][0]["content"] for request in stable_requests
    }
    assert len(independent_prefixes) == 20
    assert len(stable_prefixes) == 5
    assert independent_prefixes.isdisjoint(stable_prefixes)
    for evidence in (independent, stable, invalidated):
        assert evidence.cache_prefix_audit["consistent"] is True
        assert evidence.cache_prefix_audit["headers_used_as_proof"] is False
    assert len(invalidated.cache_prefix_audit["checks"]) >= 5
