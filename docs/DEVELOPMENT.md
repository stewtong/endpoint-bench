# Development guide

## Requirements

- Python 3.13 (`>=3.13,<3.14`)
- [`uv`](https://docs.astral.sh/uv/)
- No API credentials for ordinary development

## Setup

```bash
uv sync --locked
uv run pytest
uv run ruff check .
```

## Offline workflows

```bash
uv run endpoint-bench registry validate
uv run endpoint-bench run --target offline-mock-smoke --profile smoke --dry-run
uv run pytest tests/test_conformance.py
uv run pytest tests/test_tool_conformance.py
uv run pytest tests/test_process_lifecycle.py
uv run pytest tests/test_network_guard.py
```

Tool-call round trip against a configured local target:

```bash
uv run endpoint-bench probe --target <deployment_key> --profile smoke \
  --scenario tool-round-trip --requests 1
```

The mock endpoint in `configs/registry.example.json` is intentionally unusable and is never contacted for the dry-run path. Conformance and AIPerf integration tests use the in-process mock in `src/endpoint_bench/mock_provider.py`, which binds an ephemeral loopback port.

## Pinned AIPerf environment

AIPerf 0.13.0 is **not** a project dependency: it pulls ~96 distributions and CI must stay offline and light. Install it into a dedicated, git-ignored environment when you need real orchestration:

```bash
uv venv .venv-aiperf --python 3.13
VIRTUAL_ENV=.venv-aiperf uv pip install aiperf==0.13.0
.venv-aiperf/bin/aiperf --version   # must print 0.13.0
```

`endpoint-bench run` looks for `.venv-aiperf/bin/aiperf` by default; override with `--aiperf` or `ENDPOINT_BENCH_AIPERF`. Tests that need it skip automatically when it is absent.

Before changing integration code, read [`docs/decisions/0002-architecture-and-aiperf-verification.md`](decisions/0002-architecture-and-aiperf-verification.md). It records verified behavior and the ten integration invariants that tests enforce.

## Local benchmark and scripts

```bash
uv run endpoint-bench mock --port 18080          # serve the mock provider
./scripts/verify.sh                              # full local verification
./scripts/verify.sh --with-engine                # include engine-dependent suites
./scripts/push-and-open-pr.sh                    # push branch and open a PR
```

Agent-shaped trace replay against the mock:

```bash
uv run endpoint-bench run \
  --config examples/local-benchmark/registry.json \
  --target local-mock-agent-replay \
  --trace-file examples/local-benchmark/agent-trace.jsonl \
  --execute --max-requests 12
```

Token accounting can come from the built-in tokenizer (default) or from provider
usage. Use provider usage on any endpoint whose tokenizer does not match `builtin`:

```bash
uv run endpoint-bench run ... --token-accounting server
```

## Repository map

- `src/endpoint_bench`: provider-neutral implementation.
  - `registry.py`, `profiles.py`: declarative configuration and validation.
  - `trials.py`: lifecycle/outcome model, counters, JSONL serialization.
  - `probes.py`: deterministic conformance client.
  - `mock_provider.py`, `fixtures.py`: offline evidence generation.
  - `aiperf.py`: pinned engine wrapper, config generation, artifact parsing.
  - `report.py`, `sanitize.py`: reporting, comparison, redaction.
- `configs/`: checked-in non-secret example configuration and profile families.
- `docs/`: methodology, schema, decisions, and development documentation.
- `examples/synthetic/`: clearly synthetic dry-run artifacts.
- `tests/`: offline tests.

## Change discipline

Do not add a provider branch to core code. Add or extend configuration first. Do not implement cache, long-context, or AA features without updating the methodology/schema contracts and fail-closed tests. Do not represent dry-run output, probe evidence, or mock-verified integration as benchmark results.

Never pass a credential on a command line. Never merge a baseline result with an affinity-enabled result. Never let an AIPerf version or summary schema version change silently. Never apply a correction factor to a reported token count. Local execution stays on explicit numeric loopback addresses.

New offline tests should add a `MockBehavior` rather than monkeypatching transport internals, so the failure mode is reproducible and server-observed.
