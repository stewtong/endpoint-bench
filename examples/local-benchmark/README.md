# Local benchmark example

A complete AIPerf benchmark against a local mock, with agent-shaped trace replay. No credentials, no network beyond loopback, no provider cost.

Everything here is synthetic. It demonstrates the measurement pipeline, not the performance of any real endpoint.

## Files

- `registry.json` — a loopback target on `127.0.0.1:18080`.
- `agent-trace.jsonl` — deterministic Mooncake-format trace: 4 agent sessions, 3 turns each, input growing 512 → 1024 → 1536 tokens, 150 ms between turns. Validated by `aiperf validate --target mooncake-trace`.

## Run it

```bash
# once
uv sync --locked
uv venv .venv-aiperf --python 3.13
VIRTUAL_ENV=.venv-aiperf uv pip install aiperf==0.13.0

# terminal 1
uv run endpoint-bench mock --port 18080

# terminal 2, from the repository root
uv run endpoint-bench run \
  --config examples/local-benchmark/registry.json \
  --target local-mock-agent-replay \
  --trace-file examples/local-benchmark/agent-trace.jsonl \
  --execute --max-requests 12
uv run endpoint-bench report --run runs/<run_id>
```

Expected: 12 trials, 12 successful, 12 HTTP attempts, workload type `recorded_multi_turn_replay`.

## Reading the report

`metric_validity` classifies each metric independently. Latency metrics will read `valid_mean_only` because 12 samples is below the 30-sample percentile minimum. Token-normalized metrics read `invalid_token_sizing` because the mock's token accounting is an approximation, not a tokenizer. That is the reporting behaving correctly, not a failure.

## Without AIPerf

The conformance probes need no engine:

```bash
uv run endpoint-bench probe \
  --config examples/local-benchmark/registry.json \
  --target local-mock-agent-replay \
  --profile smoke --scenario tool-round-trip --requests 1
```
