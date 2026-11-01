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
| CLI entry point | Implemented |
| Tests and CI | Implemented |
| AIPerf integration | Planned |
| Streaming conformance probes | Planned |
| Tool-call round-trip probes | Planned |
| Cache benchmarks | Planned |
| Concurrency and stability benchmarks | Planned |
| Long-context benchmarks | Planned |
| AA metadata import | Planned |
| Live endpoint execution | Planned and intentionally disabled |

There are no benchmark results yet. Do not interpret generated manifests as measured evidence.

## Quickstart

The only verified workflow today is offline:

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

Unknown pricing is allowed for registry validation and dry-run; it is represented as `null`, never as zero. `--execute` fails closed. Live inference has not been implemented.

## Configuration

See [`configs/registry.example.json`](configs/registry.example.json). A provider contains a URL, protocol, region, non-secret credential environment-variable name, and one or more deployments. The registry never stores credentials.

Supply credentials only in the environment when live execution is implemented. Do not put them in files, logs, shell history, or process arguments. Current workflows require no credentials.

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
