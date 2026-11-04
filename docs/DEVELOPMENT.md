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
uv run pytest tests/test_session_program.py tests/test_session_engine.py
uv run pytest tests/test_session_evidence.py tests/test_session_cli.py
uv run pytest tests/test_session_same_day.py
```

Same-day default (no credentials, no inference):

```bash
uv run endpoint-bench session same-day --out /tmp/sameday
```

The loopback end-to-end regression is `tests/test_session_same_day.py::test_same_day_full_loopback_run_writes_analysis_and_report`. It exercises the mock provider, all 30 cells, analysis and the final report.

Legacy temporal planning commands (preserved, not the default):

```bash
uv run endpoint-bench session generate --out /tmp/session-plan
uv run endpoint-bench session dry-run \
  --config configs/registry.example.json --target offline-mock-smoke \
  --out /tmp/session-preflight --max-usd 31
uv run endpoint-bench session analyze --run-dir /tmp/session-run --allow-partial
```

The legacy target-bound review packet is rebuilt with:

```bash
uv run python scripts/build_session_preflight.py \
  --target configs/session-programs/session-dynamics-v1.target.json \
  --out docs/session-dynamics-v1
```

`session same-day --execute` is the default paid path; `session execute` is the legacy per-cell path. Both refuse a non-loopback target without explicit authorization, and the legacy path also requires an exact release plus the exact cell request budget and dollar envelope. Never run either while awaiting review.

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
  - `session_program.py`: session-dynamics matrix, templates, plan and manifest for both the same-day default (schema 2.0) and the legacy temporal program (schema 1.0).
  - `session_engine.py`: live-turn session runner, timing, tools, restart-safe calls.
  - `session_evidence.py`: evidence validation, journal, aggregation and report.
  - `aiperf.py`: pinned engine wrapper, config generation, artifact parsing.
  - `report.py`, `sanitize.py`: reporting, comparison, redaction.
- `configs/`: checked-in non-secret example configuration and profile families.
- `docs/`: methodology, schema, decisions, and development documentation.
- `examples/synthetic/`: clearly synthetic dry-run and normalized-report artifacts.
- `tests/`: offline tests.

## Change discipline

Do not add a provider branch to core code. Add or extend configuration first. Do not implement cache, long-context, or AA features without updating the methodology/schema contracts and fail-closed tests. Do not represent dry-run output, probe evidence, or mock-verified integration as benchmark results.

Never pass a credential on a command line. Never merge a baseline result with an affinity-enabled result. Never let an AIPerf version or summary schema version change silently. Never apply a correction factor to a reported token count. Local execution stays on explicit numeric loopback addresses.

New offline tests should add a `MockBehavior` rather than monkeypatching transport internals, so the failure mode is reproducible and server-observed.
