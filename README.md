# Agent Endpoint Bench

Agent Endpoint Bench is a provider-neutral toolkit for measuring the performance and operational readiness of public LLM inference endpoints under realistic agentic workloads.

It is primarily a measurement and qualification system—not a model-intelligence benchmark. It does not decide which model is smartest and does not provide model recommendation or routing.

## Why it exists

The same model can behave very differently across public serving endpoints. Endpoints can differ in latency, throughput, capacity under concurrency, interrupted streams, prompt-cache behavior, multi-turn and tool-call protocol support, effective context limits, error handling, and API compatibility.

This project focuses on those operational properties. It treats broad model quality as out of scope.

## Relationship to other projects

- **AIPerf** is planned as the primary performance/load/trace-replay engine.
- **Artificial Analysis (AA)** is a planned, separately stored external reference for model capability and intelligence data.
- Agent Endpoint Bench plans to add provider configuration, workload profiles, protocol conformance checks, operational qualification, reproducible evidence, and comparative reporting.

The current implementation is an early, offline-only foundation. It does not yet perform live benchmarking, AIPerf orchestration, tool-call probing, cache measurement, long-context testing, or AA synchronization.

**Project version:** software `0.0.1` (pre-alpha); specification/methodology `0.5.0`; result schema `0.1.0`; profile schema `1.0`; AIPerf target `0.13.0` (not yet integrated).

## Current status

| Capability | Status |
|---|---|
| Provider registry validation | Implemented |
| Offline dry-run manifest | Implemented |
| Normalized trial evidence (lifecycle/outcome/attempts) | Implemented |
| Local OpenAI-compatible mock provider | Implemented |
| Streaming conformance probes | Implemented |
| AIPerf 0.13.0 subprocess integration | Implemented (offline-verified) |
| Report and comparison commands | Implemented (offline evidence only) |
| CLI entry point | Implemented |
| Tests and CI | Implemented |
| Tool-call round-trip probes | Implemented (offline-verified) |
| Process deadline and cleanup | Implemented |
| Numeric-loopback execution guard | Implemented |
| Cache benchmarks | Planned |
| Concurrency and stability benchmarks | Planned |
| Long-context benchmarks | Planned |
| AA metadata import | Planned |
| Live paid endpoint execution | Implemented but fail-closed and unauthorized |

There are no benchmark results yet. Do not interpret generated manifests, probe evidence, or reports as measured endpoint performance. AIPerf integration has been verified only against a local mock, never a live provider.

## Quickstart

The verified workflows are offline (no provider contact).

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

Unknown pricing is allowed for registry validation and dry-run; it is represented as `null`, never as zero. `--execute` fails closed without request ceilings, and it refuses non-loopback targets unless `--authorize-live` is given with verified pricing and a cost ceiling. No paid inference has been run.

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
uv run endpoint-bench run --target offline-mock-smoke --profile smoke \
  --execute --max-requests 2
uv run endpoint-bench report --run runs/<run_id>
```

The complete multi-turn tool-call exchange is exercised with:

```bash
uv run endpoint-bench probe --target <deployment_key> --profile smoke \
  --scenario tool-round-trip --requests 1
```

AIPerf 0.13.0 sends `X-Session-Affinity` by default, which can improve cache locality and make an endpoint look faster than it would for an ordinary client. Baseline runs disable it and verify its absence at the receiving server; affinity experiments are separate result identities. See [`docs/decisions/0002-architecture-and-aiperf-verification.md`](docs/decisions/0002-architecture-and-aiperf-verification.md).

## Configuration

See [`configs/registry.example.json`](configs/registry.example.json). A provider contains a URL, protocol, region, non-secret credential environment-variable name, and one or more deployments. The registry never stores credentials.

Supply credentials only through the environment variable named in the registry. They are injected into generated AIPerf configuration as a `${VAR}` reference, so no secret enters a file, a command line, or an artifact. Offline workflows require no credentials.

## Benchmark profiles

Planned profile families are described in [`BENCHMARK_PLAN.md`](BENCHMARK_PLAN.md) and [`docs/METHODOLOGY.md`](docs/METHODOLOGY.md):

- **Smoke:** protocol and configuration compatibility.
- **Quick:** low-cost screening.
- **Standard:** repeated performance and concurrency observations.
- **Long context:** accepted-versus-demonstrated context, up to a safe and explicitly authorized maximum.
- **Reliability:** multi-window stability and failure accounting.

Low-cost screening is not endpoint qualification. Qualification requires sufficient samples, temporal coverage, and profile-specific thresholds.

## Interpreting results

Methodology and data contracts are in:

- [`docs/METHODOLOGY.md`](docs/METHODOLOGY.md)
- [`docs/RESULTS_SCHEMA.md`](docs/RESULTS_SCHEMA.md)

Never compare endpoints across undisclosed model revisions, client settings, traffic shapes, regions, or time windows. A dry-run manifest is not a result.

## Limitations

- No public endpoint has been measured by this repository.
- AIPerf, conformance probes, caching, long context, and AA import are not implemented.
- Public shared capacity and provider revisions can change independently of model identity.
- API telemetry, token accounting, cache state, and context behavior vary by provider.
- Large-context behavior cannot be inferred safely from an HTTP 200 response.
- Results are client-observed unless provider-side telemetry proves otherwise.

## Contributing and license

Development setup and repository conventions are in [`CONTRIBUTING.md`](CONTRIBUTING.md). Security expectations and reporting are in [`SECURITY.md`](SECURITY.md).

The project is released under the MIT License. See [`LICENSE`](LICENSE).
