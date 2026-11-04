# Agent Endpoint Bench

Measure latency, throughput, streaming behavior, tool continuations and context handling at an OpenAI-compatible inference endpoint.

The default benchmark runs in one foreground invocation. It writes the plan, request evidence, analysis and report, with no calendar windows or periodic checks. AIPerf 0.13.0 handles load testing and recorded trace replay; the session runner adds dependent turns with real assistant replies.

## Results

The [November 2026 results](docs/results/2026-11-session-dynamics/README.md) cover **648 model calls** against `deepseek-ai/DeepSeek-V4.1-Flash-1017` at the Nebius Token Factory endpoint:

| Measurement | Observed result |
| --- | --- |
| Session screening | 27 cells, 510 calls across nine scenarios at concurrency 1, 2 and 4 |
| Later session observation | 6 cells, 120 calls; kept separate from screening |
| Context checks | 3 cells, 18 calls; all 18 exact sentinel checks passed |
| HTTP errors and output truncations | None in these 648 calls |
| Largest delivered input | 524,438 request bytes; 98,410 provider-reported input tokens |

The client estimated the largest context request at 131,072 tokens. Provider counts were about 74 to 75% of the estimates, so the results establish retrieval at the delivered sizes. They do not establish a 131K provider-token limit or a maximum context window. The report includes the tool sessions' 30 unused call slots and the unexecuted portions of each program.

[Per-call measurements](docs/results/2026-11-session-dynamics/observations.jsonl) and [context-check results](docs/results/2026-11-session-dynamics/context-checks.json) are published with export hashes. Earlier screening and correction records remain in [the results index](docs/results/README.md). All measurements are descriptive; qualification thresholds remain unset.

## Install

Python 3.13 and [uv](https://docs.astral.sh/uv/) are required.

```bash
git clone https://github.com/stewtong/endpoint-bench.git
cd endpoint-bench
uv sync --locked
```

## Run a same-day benchmark

Generate a preflight without sending requests:

```bash
uv run endpoint-bench session same-day --out runs/my-session
```

For live execution, configure the endpoint and credential environment variable, inspect the preflight, then run:

```bash
uv run endpoint-bench session same-day \
  --out runs/my-session --execute --authorize-live --max-usd 17
```

The supplied target metadata names the endpoint used for the published results. To use your own configuration, pass `--config <registry.json> --target <deployment_key>`. Credentials are read from the registry's named environment variable. [Configuration and commands](docs/SESSION_DYNAMICS.md) describe the available options.

The full matrix has **30 cells and at most 558 calls**: nine session scenarios at C=1/2/4, followed by three context sizes. The scenarios cover independent prompts, shared prefixes, prefix changes, growing conversations, interleaved sessions, reconnects, compaction, idle/resume and tool continuations.

Use `--select screening` or `--select context` for a finite subset. Each cell has a 600-second deadline; one invocation has a six-hour deadline. A stopped run records partial results. Completed and interrupted cells are preserved and never replayed. There are no automatic inference retries or replacement calls.

The run directory contains `REPORT.md`, `analysis.json`, per-cell evidence, frozen manifests and the canonical journal. Reuse an inspected preflight with `--manifest <run>/manifest.json --out <run>`.

## Other workflows

| Command | Purpose |
| --- | --- |
| `registry validate` | Validate provider and deployment configuration |
| `run ... --dry-run` | Inspect an AIPerf workload without inference |
| `probe` | Check streaming and tool-call protocol behavior |
| `report` / `compare` | Inspect normalized benchmark evidence |
| `reprocess` | Produce a versioned correction from preserved artifacts |
| `session analyze` | Analyze existing session evidence offline |

AIPerf is a separate pinned environment. See [development setup](docs/DEVELOPMENT.md), [methodology](docs/METHODOLOGY.md) and [result schemas](docs/RESULTS_SCHEMA.md). Synthetic examples are under [examples/](examples/). The earlier multi-day program remains a historical workflow; the default requires no scheduled waits.

## Measurement limits

- Results belong to the recorded endpoint, workload, client settings and time interval. Provider revisions and shared capacity can change.
- Token estimates and provider-reported usage are recorded separately. Size disagreement limits requested-size claims.
- Output-ceiling hits are censored observations. Completion-latency statistics use natural completions or explicitly stated bounds.
- Cache telemetry is recorded; cache-benefit attribution requires a separate experiment.
- Deterministic tools and sentinels check protocol and retrieval behavior. Broad model quality and routing are outside this benchmark's scope.

C8, sustained offered-rate tests, larger contexts, additional deployments and Artificial Analysis import are [optional future work](docs/SESSION_FOLLOWON_SCOPE.md).

## Development

```bash
uv run pytest -q
uv run ruff check .
./scripts/verify.sh
```

The public checkout passes 435 tests; 12 engine-dependent tests skip when the optional pinned AIPerf environment is absent. GitHub Actions runs lint, tests and offline workflows; live inference requires explicit execution flags and client-side request, token, concurrency and estimated-cost guards. These guards are not provider billing guarantees.

Software version: 0.1.3. See [contributing](CONTRIBUTING.md), [security](SECURITY.md) and the [MIT license](LICENSE).
