# Agent Endpoint Bench

Agent Endpoint Bench is a provider-neutral toolkit for measuring the performance and operational readiness of public LLM inference endpoints under realistic agentic workloads.

It is primarily a measurement and qualification system—not a model-intelligence benchmark. It does not decide which model is smartest and does not provide model recommendation or routing.

## Why it exists

The same model can behave very differently across public serving endpoints. Endpoints can differ in latency, throughput, capacity under concurrency, interrupted streams, prompt-cache behavior, multi-turn and tool-call protocol support, effective context limits, error handling, and API compatibility.

This project focuses on those operational properties. It treats broad model quality as out of scope.

## Relationship to other projects

- **AIPerf 0.13.0** is the pinned performance, load, and trace-replay engine. It is driven as a subprocess and is not a project dependency.
- **Artificial Analysis (AA)** is a separately stored external reference for model capability data. AA import is not implemented.

The implementation covers provider configuration, workload profiles, AIPerf orchestration, trace replay, protocol conformance probes, normalized evidence with per-metric validity, and comparative reporting. Cache measurement, long-context qualification, multi-window reliability, and AA synchronization remain unimplemented.

**Project version:** software `0.1.0` (benchmark MVP); specification/methodology `0.5.1`; result schema `0.1.0`; profile schema `1.0`; pinned engine AIPerf `0.13.0`.

## Current status

| Capability | Status |
|---|---|
| Provider registry validation | Implemented |
| Offline dry-run manifest | Implemented |
| Normalized trial evidence (lifecycle/outcome/attempts) | Implemented |
| Local OpenAI-compatible mock provider | Implemented |
| Streaming conformance probes | Implemented |
| AIPerf 0.13.0 subprocess integration | Implemented, verified offline and live |
| Agent-shaped trace replay | Implemented, verified offline |
| Report and comparison commands | Implemented |
| Metric-specific validity states | Implemented |
| Provider token accounting | Implemented (`--token-accounting server`) |
| CLI entry point | Implemented |
| Tests and CI | Implemented, green on GitHub Actions |
| Tool-call round-trip probes | Implemented, verified offline and live |
| Process deadline and cleanup | Implemented |
| Numeric-loopback execution guard | Implemented |
| Screening profiles (1K/10K/32K) | Implemented and executed |
| Cache benchmarks | Planned |
| Long-context qualification (100K-1M) | Planned |
| Multi-window reliability | Planned |
| AA metadata import | Planned |

**There are screening measurements but no qualification.** Six screening cells against one deployment — 1K/10K/32K at concurrency 1 and 2, 120 trials, zero failures — are recorded in [`docs/GATE3_RESULTS.md`](docs/GATE3_RESULTS.md). That evidence is single-window and 20 samples per cell. It supports none of the following: a qualification verdict, percentile claims, capacity, reliability, cache behaviour, or a comparison between sizing or concurrency cells. One live tool-call continuation also passed.

No endpoint has been qualified or failed. Qualification requires calibrated thresholds and multi-window evidence, which do not exist yet.

## Quickstart

The quickstart is offline and needs no credentials. Live execution exists but is fail-closed and requires explicit authorization; see [Live execution](#live-execution).

```bash
git clone https://github.com/stewtong/endpoint-bench.git
cd endpoint-bench
uv sync --locked
uv run endpoint-bench registry validate
uv run endpoint-bench run \
  --target offline-mock-smoke \
  --profile smoke \
  --dry-run > /tmp/endpoint-bench-dry-run.json
cat /tmp/endpoint-bench-dry-run.json
```

This requires Python 3.13. `uv sync` creates an isolated environment; it does not contact an inference provider. The mock endpoint is intentionally unreachable and exists only to exercise configuration resolution.

The dry-run manifest records the resolved target, profile, configuration hashes, planned request/token exposure, and explicit evidence that no request was sent. Cost estimation is intentionally `null` in the example because its synthetic pricing is unverified.

Unknown pricing is allowed for registry validation and dry-run; it is represented as `null`, never as zero. `--execute` fails closed: it requires `--max-requests` within the profile's plan, and for non-loopback targets it additionally requires `--authorize-live`, verified per-million pricing, and a `--max-usd` ceiling that the computed conservative estimate must fit under. One authorized smoke run has been executed; see [`IMPLEMENTATION_TASKS.md`](IMPLEMENTATION_TASKS.md) for its evidence and limitations.

## Run a full benchmark locally

A complete AIPerf benchmark with agent-shaped trace replay needs no credentials and no network beyond loopback:

```bash
uv venv .venv-aiperf --python 3.13
VIRTUAL_ENV=.venv-aiperf uv pip install aiperf==0.13.0

# terminal 1: the mock provider
uv run endpoint-bench mock --port 18080

# terminal 2: replay a synthetic multi-turn agent trace
uv run endpoint-bench run \
  --config examples/local-benchmark/registry.json \
  --target local-mock-agent-replay \
  --trace-file examples/local-benchmark/agent-trace.jsonl \
  --execute --max-requests 12
uv run endpoint-bench report --run runs/<run_id>
```

The trace is synthetic and deterministic: four agent sessions, three turns each, growing context. The report labels the workload `recorded_multi_turn_replay` and states that replay is not live agent execution. See [`docs/GATE3_PREPARATION.md`](docs/GATE3_PREPARATION.md) for the verified result and for the prepared live experiments.

## Offline conformance evidence

The probe path needs no credentials and no AIPerf installation. Its passing conformance matrix — streaming start, chunks, usage, finish reason, truncation, interruption, timeouts before and after first token, 429/5xx, malformed payloads, and missing usage — runs entirely against a local mock in the test suite:

```bash
uv run pytest tests/test_conformance.py
```

The same command works against any configured target:

```bash
uv run endpoint-bench probe --target <deployment_key> --profile smoke --requests 2
```

It writes a run directory containing `manifest.json`, `trials.jsonl`, and hashes. Against the checked-in example target this records `client_error` trials, because that mock URL is deliberately unreachable; the resulting evidence documents a transport failure rather than a passing probe. Trial records separate lifecycle from outcome and count HTTP attempts separately from trials.

Full AIPerf integration requires the pinned engine environment, which is not part of `uv sync`:

```bash
uv venv .venv-aiperf --python 3.13
VIRTUAL_ENV=.venv-aiperf uv pip install aiperf==0.13.0
uv run endpoint-bench run --target <deployment_key> --profile smoke \
  --execute --max-requests 2
uv run endpoint-bench report --run runs/<run_id>
```

Against the checked-in example target this exits non-zero and records a **benchmark failure**, because that mock URL is deliberately unreachable. That is the intended demonstration of failure handling: the run still writes normalized trials, raw evidence, and a report, and it does not report zeros as measurements. A passing AIPerf run requires a reachable target.

The complete multi-turn tool-call exchange is exercised with:

```bash
uv run endpoint-bench probe --target <deployment_key> --profile smoke \
  --scenario tool-round-trip --requests 1
```

One logical trial, two HTTP attempts: the tool call plus the continuation. The passing matrix — fragmented argument reassembly, multiple calls, missing and inconsistent call ids, invalid JSON, interrupted streams, absent tool calls attributed to the model, large observations, and malformed or timed-out continuations — runs offline in `tests/test_tool_conformance.py`. Against the checked-in example target the probe records a `client_error`, because that URL is deliberately unreachable.

AIPerf 0.13.0 sends `X-Session-Affinity` by default, which can improve cache locality and make an endpoint look faster than it would for an ordinary client. Baseline runs disable it and verify its absence at the receiving server; affinity experiments are separate result identities. See [`docs/decisions/0002-architecture-and-aiperf-verification.md`](docs/decisions/0002-architecture-and-aiperf-verification.md).

## Live execution

Live inference is fail-closed. A run refuses to start unless every condition holds:

- `--execute` with `--max-requests` at or below the profile's plan;
- for a non-loopback target, `--authorize-live`;
- verified per-million pricing in the registry;
- `--max-usd` above the computed conservative estimate;
- the credential environment variable named by the registry is set.

Local unattended execution is restricted to explicit numeric loopback addresses on an explicit port. Hostnames are not trusted, proxy variables are stripped, and redirects are refused.

```bash
uv run endpoint-bench run \
  --config ~/.endpoint-bench/registry.live.json \
  --target <deployment_key> \
  --profile screening-10k --concurrency 2 \
  --token-accounting server \
  --execute --authorize-live --max-requests 20 --max-usd 0.20
```

Conformance probes have their own path, and a non-loopback probe additionally requires `--authorize-live`:

```bash
uv run endpoint-bench probe \
  --config ~/.endpoint-bench/registry.live.json \
  --target <deployment_key> \
  --profile smoke --scenario tool-round-trip --requests 1 --authorize-live
```

A client-side ceiling is a bound, not a provider billing limit. Use `probe --dry-run` or `run --dry-run` to inspect the plan and cost estimate before spending anything.

## Configuration

See [`configs/registry.example.json`](configs/registry.example.json). A provider contains a URL, protocol, region, non-secret credential environment-variable name, and one or more deployments. The registry never stores credentials.

Supply credentials only through the environment variable named in the registry. They are injected into generated AIPerf configuration as a `${VAR}` reference, so no secret enters a file, a command line, or an artifact. Offline workflows require no credentials.

## Benchmark profiles

Five profile families ship in [`configs/profiles/`](configs/profiles). Each declares request count, target input size, output ceiling, concurrency, an explicit timeout, and a token-sizing tolerance:

- **Smoke:** protocol and configuration compatibility, 2 requests.
- **Quick:** low-cost screening, 20 requests.
- **Standard:** repeated performance and concurrency observations, 200 requests.
- **Long context:** accepted-versus-demonstrated context, requiring explicit authorization.
- **Reliability:** multi-window stability and failure accounting, 300 requests.

Thresholds are marked provisional and uncalibrated. Low-cost screening is not endpoint qualification, which requires sufficient samples, temporal coverage, and profile-specific thresholds.

## Interpreting results

Methodology and data contracts are in:

- [`docs/METHODOLOGY.md`](docs/METHODOLOGY.md)
- [`docs/RESULTS_SCHEMA.md`](docs/RESULTS_SCHEMA.md)

Never compare endpoints across undisclosed model revisions, client settings, traffic shapes, regions, or time windows. A dry-run manifest is not a result.

## Limitations

- No endpoint has been qualified. Screening evidence only, from one window.
- Cache measurement, long-context qualification, multi-window reliability, and AA import are not implemented.
- The built-in tokenizer does not match every hosted model; on this endpoint it differed from provider-reported usage by 25-29% at a 128-token workload. Use `--token-accounting server` there, which makes the denominator authoritative but stops verifying delivered target size.
- Traces are replayed, never executed as live agent tool loops. Replay measures traffic shape, not agent behaviour.
- Public shared capacity and provider revisions can change independently of model identity.
- API telemetry, token accounting, cache state, and context behavior vary by provider.
- Large-context behavior cannot be inferred safely from an HTTP 200 response.
- Results are client-observed unless provider-side telemetry proves otherwise.

## Contributing and license

Development setup and repository conventions are in [`CONTRIBUTING.md`](CONTRIBUTING.md). Security expectations and reporting are in [`SECURITY.md`](SECURITY.md).

The project is released under the MIT License. See [`LICENSE`](LICENSE).
