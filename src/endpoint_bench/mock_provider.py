from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from enum import StrEnum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class MockBehavior(StrEnum):
    """Deterministic response shapes the mock can produce.

    Every failure mode the plan requires a probe for has a behavior here, so
    conformance tests assert outcomes rather than merely executing code.
    """

    STREAM_SUCCESS = "stream_success"
    NONSTREAM_SUCCESS = "nonstream_success"
    STREAM_REASONING_THEN_CONTENT = "stream_reasoning_then_content"
    STREAM_FRAGMENTED_SSE = "stream_fragmented_sse"
    STREAM_VISIBLE_TRUNCATED = "stream_visible_truncated"
    SENTINEL_ECHO = "sentinel_echo"
    RATE_LIMITED = "rate_limited"
    SERVER_ERROR = "server_error"
    HANG_BEFORE_FIRST_TOKEN = "hang_before_first_token"
    HANG_AFTER_FIRST_TOKEN = "hang_after_first_token"
    TRUNCATE_NO_FINISH = "truncate_no_finish"
    INTERRUPT_MID_STREAM = "interrupt_mid_stream"
    MISSING_USAGE = "missing_usage"
    INCONSISTENT_USAGE = "inconsistent_usage"
    MALFORMED_JSON = "malformed_json"
    REDIRECT = "redirect"

    TOOL_CALL_STREAM = "tool_call_stream"
    TOOL_CALL_NONSTREAM = "tool_call_nonstream"
    TOOL_CALL_MULTI_STREAM = "tool_call_multi_stream"
    TOOL_CALL_MISSING_ID = "tool_call_missing_id"
    TOOL_CALL_INCONSISTENT_ID = "tool_call_inconsistent_id"
    TOOL_CALL_INVALID_JSON = "tool_call_invalid_json"
    TOOL_CALL_INTERRUPTED = "tool_call_interrupted"
    TOOL_CALL_ABSENT = "tool_call_absent"
    TOOL_CALL_TRUNCATED = "tool_call_truncated"
    TOOL_CALL_UNKNOWN = "tool_call_unknown"

    CONTINUATION_MALFORMED = "continuation_malformed"
    CONTINUATION_HANG = "continuation_hang"


DEFAULT_TOKENS = ("Mock", " stream", " response", " for", " conformance", ".")

#: Characters per token used to derive coherent synthetic usage from the payload.
#: Only the mock uses this; real token counts always come from a tokenizer or the
#: provider's own `usage` field.
_APPROX_CHARS_PER_TOKEN = 4


@dataclass(frozen=True)
class ToolCallSpec:
    call_id: str
    name: str
    arguments: str


@dataclass
class MockRequest:
    """One request the mock actually received.

    This is the server-side accounting used to verify client behavior: header
    presence or absence, request counts including probes and warmups, and the
    exact payload that arrived.
    """

    method: str
    path: str
    headers: dict[str, str]
    body: dict[str, Any] | None
    received_at: float

    @property
    def header_names_lower(self) -> set[str]:
        return {name.lower() for name in self.headers}

    def has_header(self, name: str) -> bool:
        return name.lower() in self.header_names_lower

    def header(self, name: str) -> str | None:
        lowered = name.lower()
        for key, value in self.headers.items():
            if key.lower() == lowered:
                return value
        return None


@dataclass
class MockProvider:
    """Local OpenAI-compatible provider for offline evidence generation.

    Runs in a background thread on an ephemeral loopback port. Records every
    request so tests can verify headers, readiness traffic, warmups, and request
    counts from the receiving side rather than trusting client self-reporting.
    """

    behavior: MockBehavior = MockBehavior.STREAM_SUCCESS
    tokens: tuple[str, ...] = DEFAULT_TOKENS
    chunk_delay_s: float = 0.0
    first_token_delay_s: float = 0.0
    hang_seconds: float = 30.0
    model_id: str = "mock-model"
    #: Override the provider-reported prompt token count. Used only to build a
    #: deliberate token-sizing mismatch fixture.
    prompt_tokens_override: int | None = None
    #: Stream with chunked transfer encoding instead of `Connection: close`.
    #: Offline session cells need this to observe a genuinely reusable TCP
    #: transport; the default keeps the historical single-response framing.
    keep_alive: bool = False
    _server: ThreadingHTTPServer | None = field(default=None, init=False, repr=False)
    _thread: threading.Thread | None = field(default=None, init=False, repr=False)
    requests: list[MockRequest] = field(default_factory=list, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    # -- lifecycle ---------------------------------------------------------

    def start(self, port: int = 0) -> str:
        """Start serving and return the base URL (without a trailing slash).

        `port=0` binds an ephemeral port, which is what tests want. The CLI binds
        an explicit port so a contributor can point the benchmark at it.
        """
        provider = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:  # silence test output
                return

            def do_GET(self) -> None:  # noqa: N802 - stdlib naming
                provider._record("GET", self.path, self.headers, None)
                if self.path.rstrip("/").endswith("/models"):
                    self._send_json(
                        {
                            "object": "list",
                            "data": [
                                {
                                    "id": provider.model_id,
                                    "object": "model",
                                    "created": 0,
                                    "owned_by": "endpoint-bench-mock",
                                }
                            ],
                        }
                    )
                    return
                self._send_json({"error": {"message": "not found"}}, 404)

            def do_POST(self) -> None:  # noqa: N802 - stdlib naming
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body: dict[str, Any] | None = json.loads(raw)
                except json.JSONDecodeError:
                    body = None
                provider._record("POST", self.path, self.headers, body)
                provider._respond(self, body or {})

            def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        server.daemon_threads = True
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, daemon=True)
        self._thread.start()
        host, port = server.server_address[:2]
        return f"http://{host}:{port}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> MockProvider:
        self.base_url = self.start()
        return self

    def serve_forever(self) -> None:
        """Block, serving until interrupted. Used by the `mock` CLI command."""
        if self._server is None:
            raise RuntimeError("call start() before serve_forever()")
        try:
            while True:
                time.sleep(0.2)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # -- accounting --------------------------------------------------------

    def _record(
        self,
        method: str,
        path: str,
        headers: Any,
        body: dict[str, Any] | None,
    ) -> None:
        with self._lock:
            self.requests.append(
                MockRequest(
                    method=method,
                    path=path,
                    headers={key: value for key, value in headers.items()},
                    body=body,
                    received_at=time.time(),
                )
            )

    def reset(self) -> None:
        with self._lock:
            self.requests.clear()

    @property
    def inference_requests(self) -> list[MockRequest]:
        """Requests the mock classified as inference traffic, not discovery."""
        return [
            request
            for request in self.requests
            if request.method == "POST" and request.path.rstrip("/").endswith("/chat/completions")
        ]

    def sent_header(self, name: str) -> list[str | None]:
        """Every value observed for a header across all requests."""
        return [request.header(name) for request in self.requests]

    # -- response construction --------------------------------------------

    def _respond(self, handler: BaseHTTPRequestHandler, body: dict[str, Any]) -> None:  # noqa: C901
        behavior = self.behavior
        stream = bool(body.get("stream"))
        continuation = _is_continuation(body)

        if behavior is MockBehavior.RATE_LIMITED:
            self._send_error(handler, 429, "rate_limit_exceeded", "Too many requests")
            return
        if behavior is MockBehavior.SERVER_ERROR:
            self._send_error(handler, 503, "server_error", "Upstream unavailable")
            return
        if behavior is MockBehavior.MALFORMED_JSON:
            self._write_broken_json(handler)
            return
        if behavior is MockBehavior.SENTINEL_ECHO:
            code = _sentinel_from_body(body)
            self._write_completion(
                handler,
                body,
                content=code or "",
                stream=stream,
                tokens=(code,) if code else ("",),
            )
            return
        if behavior is MockBehavior.REDIRECT:
            self._write_redirect(handler)
            return
        if behavior is MockBehavior.HANG_BEFORE_FIRST_TOKEN:
            time.sleep(self.hang_seconds)
            self._close_quietly(handler)
            return
        if behavior is MockBehavior.CONTINUATION_MALFORMED and continuation:
            self._write_broken_json(handler)
            return
        if behavior is MockBehavior.CONTINUATION_HANG and continuation:
            time.sleep(self.hang_seconds)
            self._close_quietly(handler)
            return
        if behavior is MockBehavior.TOOL_CALL_TRUNCATED:
            # Output ceiling hit before a usable call was emitted: no tool_calls,
            # finish_reason "length".
            self._write_truncated_stream(handler, body)
            return
        if behavior is MockBehavior.TOOL_CALL_ABSENT:
            # The model answers in prose instead of calling a tool. This is a
            # semantic choice, not an infrastructure fault.
            self._write_completion(handler, body, content="".join(self.tokens), stream=stream)
            return
        if continuation:
            self._write_completion(handler, body, content="".join(self.tokens), stream=stream)
            return

        tool_behaviors = {
            MockBehavior.TOOL_CALL_STREAM,
            MockBehavior.TOOL_CALL_NONSTREAM,
            MockBehavior.TOOL_CALL_MULTI_STREAM,
            MockBehavior.TOOL_CALL_MISSING_ID,
            MockBehavior.TOOL_CALL_INCONSISTENT_ID,
            MockBehavior.TOOL_CALL_INVALID_JSON,
            MockBehavior.TOOL_CALL_INTERRUPTED,
            MockBehavior.TOOL_CALL_UNKNOWN,
        }
        if body.get("tools") or behavior in tool_behaviors:
            self._respond_tool_call(handler, body, stream)
            return

        self._write_completion(handler, body, content="".join(self.tokens), stream=stream)

    def _write_truncated_stream(
        self, handler: BaseHTTPRequestHandler, body: dict[str, Any]
    ) -> None:
        model = body.get("model", self.model_id)
        self._open_stream(handler)
        self._emit(handler, self._chunk(model, delta={"role": "assistant"}))
        final = self._chunk(model, delta={})
        final["choices"][0]["finish_reason"] = "length"
        final["usage"] = self._usage(body)
        self._emit(handler, final)
        self._send_stream_bytes(handler, b"data: [DONE]\n\n")
        self._end_stream(handler)

    def _write_redirect(self, handler: BaseHTTPRequestHandler) -> None:
        handler.send_response(302)
        handler.send_header("Location", "http://198.51.100.7/v1/chat/completions")
        handler.send_header("Content-Length", "0")
        handler.end_headers()

    def _write_broken_json(self, handler: BaseHTTPRequestHandler) -> None:
        data = b'{"id": "broken", "choices": ['
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)

    def _send_error(
        self,
        handler: BaseHTTPRequestHandler,
        status: int,
        code: str,
        message: str,
    ) -> None:
        data = json.dumps({"error": {"code": code, "message": message}}).encode()
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)

    def _write_completion(
        self,
        handler: BaseHTTPRequestHandler,
        body: dict[str, Any],
        *,
        content: str,
        stream: bool = False,
        tokens: tuple[str, ...] | None = None,
    ) -> None:
        if stream:
            self._stream_text(handler, body, tokens=tokens)
            return
        payload = {
            "id": "chatcmpl-mock",
            "object": "chat.completion",
            "created": 0,
            "model": body.get("model", self.model_id),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": self._usage(body, completion_tokens=len(tokens or (content,))),
        }
        if self.behavior is MockBehavior.MISSING_USAGE:
            payload.pop("usage")
        data = json.dumps(payload).encode()
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)

    def _stream_text(
        self,
        handler: BaseHTTPRequestHandler,
        body: dict[str, Any],
        *,
        tokens: tuple[str, ...] | None = None,
    ) -> None:
        model = body.get("model", self.model_id)
        behavior = self.behavior
        output_tokens = tokens if tokens is not None else self.tokens
        self._open_stream(handler)
        if not self._emit(handler, self._chunk(model, delta={"role": "assistant"})):
            return
        if self.first_token_delay_s:
            time.sleep(self.first_token_delay_s)

        if behavior is MockBehavior.STREAM_REASONING_THEN_CONTENT:
            for token in ("Reasoning", " about", " the", " fixture", "."):
                if not self._emit(
                    handler,
                    self._chunk(model, delta={"reasoning_content": token}),
                ):
                    return
                if self.chunk_delay_s:
                    time.sleep(self.chunk_delay_s)
            for token in output_tokens:
                if not self._emit(handler, self._chunk(model, delta={"content": token})):
                    return
                if self.chunk_delay_s:
                    time.sleep(self.chunk_delay_s)
            final = self._chunk(model, delta={})
            final["choices"][0]["finish_reason"] = "stop"
            final["usage"] = self._usage(body, completion_tokens=len(output_tokens))
            self._emit(handler, final)
            self._send_stream_bytes(handler, b"data: [DONE]\n\n")
            self._end_stream(handler)
            return

        for index, token in enumerate(output_tokens):
            if behavior is MockBehavior.HANG_AFTER_FIRST_TOKEN and index == 1:
                time.sleep(self.hang_seconds)
                self._close_quietly(handler)
                return
            if behavior is MockBehavior.INTERRUPT_MID_STREAM and index == 2:
                self._close_quietly(handler)
                return
            payload = self._chunk(model, delta={"content": token})
            if behavior is MockBehavior.STREAM_FRAGMENTED_SSE:
                if not self._emit_fragmented(handler, payload):
                    return
            elif not self._emit(handler, payload):
                return
            if self.chunk_delay_s:
                time.sleep(self.chunk_delay_s)

        if behavior is MockBehavior.TRUNCATE_NO_FINISH:
            self._close_quietly(handler)
            return

        final = self._chunk(model, delta={})
        final["choices"][0]["finish_reason"] = (
            "length" if behavior is MockBehavior.STREAM_VISIBLE_TRUNCATED else "stop"
        )
        if behavior is not MockBehavior.MISSING_USAGE:
            final["usage"] = self._usage(body, completion_tokens=len(output_tokens))
        self._emit(handler, final)
        self._send_stream_bytes(handler, b"data: [DONE]\n\n")
        self._end_stream(handler)

    # -- tool calls --------------------------------------------------------

    def _tool_specs(self) -> list[ToolCallSpec]:
        behavior = self.behavior
        primary = ToolCallSpec(
            call_id="call_mock_1",
            name="read_file",
            arguments='{"path": "/tmp/example.txt", "max_bytes": 4096}',
        )
        if behavior is MockBehavior.TOOL_CALL_UNKNOWN:
            return [
                ToolCallSpec(
                    call_id="call_mock_1",
                    name="read_secret",
                    arguments='{"path": "/etc/passwd", "max_bytes": 4096}',
                )
            ]
        if behavior is MockBehavior.TOOL_CALL_INVALID_JSON:
            return [
                ToolCallSpec(
                    call_id="call_mock_1",
                    name="read_file",
                    arguments='{"path": "/tmp/example.txt", "max_bytes":',
                )
            ]
        if behavior is MockBehavior.TOOL_CALL_MULTI_STREAM:
            return [
                primary,
                ToolCallSpec(
                    call_id="call_mock_2",
                    name="list_dir",
                    arguments='{"path": "/tmp", "recursive": false}',
                ),
            ]
        return [primary]

    def _respond_tool_call(
        self,
        handler: BaseHTTPRequestHandler,
        body: dict[str, Any],
        stream: bool,
    ) -> None:
        model = body.get("model", self.model_id)
        specs = self._tool_specs()

        if not stream:
            self._write_tool_completion(handler, body, specs)
            return

        self._open_stream(handler)
        if not self._emit(handler, self._chunk(model, delta={"role": "assistant"})):
            return

        fragments_by_index = [
            [spec.arguments[i : i + 12] for i in range(0, len(spec.arguments), 12)]
            for spec in specs
        ]
        longest = max(len(fragments) for fragments in fragments_by_index)
        interrupted = self.behavior is MockBehavior.TOOL_CALL_INTERRUPTED

        for step in range(longest):
            for index, spec in enumerate(specs):
                fragments = fragments_by_index[index]
                if step >= len(fragments):
                    continue
                call: dict[str, Any] = {
                    "index": index,
                    "function": {"arguments": fragments[step]},
                }
                if step == 0 and self.behavior is not MockBehavior.TOOL_CALL_MISSING_ID:
                    call["id"] = spec.call_id
                    call["type"] = "function"
                    call["function"]["name"] = spec.name
                if (
                    step == 1
                    and self.behavior is MockBehavior.TOOL_CALL_INCONSISTENT_ID
                ):
                    # A conflicting identifier on a later fragment. A correct
                    # client must notice rather than picking whichever it saw first.
                    call["id"] = "call_mock_conflict"
                if interrupted and step == 1 and index == 0:
                    self._close_quietly(handler)
                    return
                self._emit(handler, self._chunk(model, delta={"tool_calls": [call]}))

        final = self._chunk(model, delta={})
        final["choices"][0]["finish_reason"] = "tool_calls"
        final["usage"] = self._usage(body)
        self._emit(handler, final)
        self._send_stream_bytes(handler, b"data: [DONE]\n\n")
        self._end_stream(handler)

    def _write_tool_completion(
        self,
        handler: BaseHTTPRequestHandler,
        body: dict[str, Any],
        specs: list[ToolCallSpec],
    ) -> None:
        payload = {
            "id": "chatcmpl-mock",
            "object": "chat.completion",
            "created": 0,
            "model": body.get("model", self.model_id),
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": spec.call_id,
                                "type": "function",
                                "function": {"name": spec.name, "arguments": spec.arguments},
                            }
                            for spec in specs
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": self._usage(body),
        }
        data = json.dumps(payload).encode()
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)

    # -- helpers -----------------------------------------------------------

    def _open_stream(self, handler: BaseHTTPRequestHandler) -> None:
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Cache-Control", "no-cache")
        if self.keep_alive:
            handler.send_header("Transfer-Encoding", "chunked")
        else:
            handler.send_header("Connection", "close")
        handler.end_headers()

    def _send_stream_bytes(self, handler: BaseHTTPRequestHandler, data: bytes) -> None:
        """Write one SSE unit, chunk-framed when the mock keeps the socket open."""
        if self.keep_alive:
            handler.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
        else:
            handler.wfile.write(data)
        handler.wfile.flush()

    def _end_stream(self, handler: BaseHTTPRequestHandler) -> None:
        if self.keep_alive:
            handler.wfile.write(b"0\r\n\r\n")
            handler.wfile.flush()

    def _usage(
        self,
        body: dict[str, Any] | None = None,
        *,
        completion_tokens: int | None = None,
    ) -> dict[str, int]:
        """Report usage derived from the actual request payload.

        Deriving rather than hardcoding keeps the happy path internally
        coherent, so a token-count mismatch means something real. Use
        `prompt_tokens_override` to build a deliberate mismatch fixture.
        """
        prompt = self.prompt_tokens_override
        if prompt is None:
            prompt = _estimate_prompt_tokens(body or {})
        completion = len(self.tokens) if completion_tokens is None else completion_tokens
        usage = {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        }
        if self.behavior is MockBehavior.STREAM_REASONING_THEN_CONTENT:
            reasoning = 5
            usage["completion_tokens_details"] = {"reasoning_tokens": reasoning}
            usage["completion_tokens"] = completion + reasoning
            usage["total_tokens"] = prompt + completion + reasoning
        return usage

    def _chunk(self, model: str, delta: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": "chatcmpl-mock",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
        }

    def _emit(self, handler: BaseHTTPRequestHandler, payload: dict[str, Any]) -> bool:
        try:
            self._send_stream_bytes(
                handler, b"data: " + json.dumps(payload).encode() + b"\n\n"
            )
            return True
        except (BrokenPipeError, ConnectionResetError):
            return False

    def _emit_fragmented(
        self, handler: BaseHTTPRequestHandler, payload: dict[str, Any]
    ) -> bool:
        """Write one SSE event across several TCP writes.

        This is what makes the client's line buffering observable: a reader that
        assumes one `readline()` equals one complete event fails here.
        """
        data = b"data: " + json.dumps(payload).encode() + b"\n\n"
        try:
            for start in range(0, len(data), 17):
                self._send_stream_bytes(handler, data[start : start + 17])
                time.sleep(0.001)
            return True
        except (BrokenPipeError, ConnectionResetError):
            return False

    def _close_quietly(self, handler: BaseHTTPRequestHandler) -> None:
        try:
            handler.close_connection = True
            handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


def _is_continuation(body: dict[str, Any]) -> bool:
    """True when the payload carries a tool result, i.e. it is a later turn."""
    messages = body.get("messages")
    if not isinstance(messages, list):
        return False
    return any(
        isinstance(message, dict) and message.get("role") == "tool" for message in messages
    )


def _estimate_prompt_tokens(body: dict[str, Any]) -> int:
    """Approximate prompt size from the serialized messages.

    Deterministic and payload-derived. It is not a tokenizer and is never
    reported as an authoritative count outside the mock.
    """
    characters = 0
    messages = body.get("messages")
    if isinstance(messages, list):
        for message in messages:
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if isinstance(content, str):
                characters += len(content)
            elif isinstance(content, list):
                characters += len(json.dumps(content))
            tool_calls = message.get("tool_calls")
            if isinstance(tool_calls, list):
                characters += len(json.dumps(tool_calls))
    tools = body.get("tools")
    if isinstance(tools, list):
        characters += len(json.dumps(tools))
    return max(1, characters // _APPROX_CHARS_PER_TOKEN)


def _sentinel_from_body(body: dict[str, Any]) -> str | None:
    """Return the first SENTINEL- code found in the request, if any."""
    import re

    messages = body.get("messages")
    if not isinstance(messages, list):
        return None
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str):
            match = re.search(r"SENTINEL-[A-F0-9]{12}", content)
            if match:
                return match.group(0)
    return None
