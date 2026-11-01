# 0002 — Architecture and AIPerf 0.13.0 verification

**Date:** 2026-11-01
**Status:** Accepted. Closes Gate 0.
**Supersedes numbering:** Gate 0 in `IMPLEMENTATION_TASKS.md` asked for `docs/decisions/0001-architecture.md`. That number was already taken by `0001-public-readiness-foundation.md`, so this record uses `0002` and `IMPLEMENTATION_TASKS.md` was updated to point here. The Gate 0 deliverable is unchanged; only the filename moved.
**Update rule:** Re-verify against installed AIPerf source and observed behavior before relying on any fact here. Every claim below was checked against AIPerf 0.13.0 as installed, not against upstream documentation.

## Scope

Gate 0 asked for verified AIPerf behavior before any integration code is written. This record covers distribution identity, CLI surface, configuration, credential handling, client-side effects, retries, timeouts, tokenization, artifacts, and error recording. It records what was **observed**, what was **read from installed source**, and what remains **unknown**.

No paid inference was performed. All experiments ran against a local `127.0.0.1` OpenAI-compatible mock with `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`. The mock and scratch configs lived outside the repository.

## 1. Identity and installation

| Fact | Verified value |
|---|---|
| Distribution | `aiperf-0.13.0-py3-none-any.whl` (pure-Python wheel) |
| GitHub tag | `v0.13.0` → `794f8bb75f8582f22e412d7e650fc71ca2a3d21a` |
| Plan pin | `794f8bb75f8582f22e412d7e650fc71ca2a3d21a` — **matches** |
| Tag commit date | 2026-09-23 |
| `requires_python` | `>=3.11,<3.14` |
| Runtime dependencies | 96 distributions |
| Console script | `aiperf`; `aiperf --version` prints bare `0.13.0` |

Installed into a dedicated, git-ignored virtual environment at `.venv-aiperf` (Python 3.13.13). AIPerf is **not** declared in `pyproject.toml`, so `uv.lock` and CI stay light and CI never installs the AIPerf stack.

The wheel and the pinned commit are the same release, so wheel provenance is sufficient; a source install is not required to claim the pin.

## 2. CLI surface

Top-level commands present: `analyze`, `analyze-trace`, `chat`, `config`, `kube`, `plot`, `plugins`, `profile`, `proxy`, `service`, `speed-bench-report`, `synthesize`, `validate`.

**The benchmark command is `aiperf profile`, not `aiperf run`.** `analyze` is post-run visualization over completed artifacts. Any integration code or documentation that assumes `aiperf run` is wrong for this version.

`aiperf profile` exposes roughly 2,000 lines of help across sections: Endpoint, Dataset, Tokenizer, Load Generator, Load Phase, Warmup, Cache Bust, Artifacts, GPU Telemetry, Server Metrics, and others. The surface is wide enough that a hand-built long argv is both fragile and unreadable; the wrapper should generate a YAML config file instead.

## 3. Configuration

- `aiperf profile --config <path.yaml>` accepts a YAML envelope: top-level `schemaVersion: "2.0"` plus a `benchmark:` body. CLI flags override file values.
- Both snake_case and camelCase keys are accepted.
- Shorthand forms exist (`model:` → `models.items[0]`, singular `dataset:`, flat `phases:`), but the wrapper should emit explicit forms so generated files are unambiguous.
- A JSON Schema ships inside the package at `aiperf/config/schema/aiperf-config.schema.json`. The wrapper can validate generated config against AIPerf's own schema rather than reimplementing it.
- `aiperf config validate <file>` performs strict validation. Unknown keys are rejected (`extra_forbidden`) with a readable message and a non-zero exit. This is stronger than the plan assumed and should be relied on.
- `aiperf config init --template <name>` emits bundled templates; useful for reference, not for production generation.

**Decision:** the wrapper generates YAML and passes it via `--config`. It does not build long argv, and it validates the generated file with `aiperf config validate` before executing.

## 4. Credential handling

This is the finding that most affects the wrapper design.

**AIPerf 0.13.0 exposes `--api-key`.** A secret passed this way appears in process arguments and therefore in process listings and shell history — the same defect already identified in the historical `run_guidellm.sh`. **The wrapper must never pass `--api-key`.**

Three mechanisms were examined:

| Mechanism | Result |
|---|---|
| YAML `api_key: ${ENV_VAR}` substitution | **Works.** Verified end to end. |
| `AIPERF_INJECTED_API_KEY` environment variable | **Does not authenticate a local in-process run.** Verified negative. |
| `--api-key` flag | Works, but leaks the secret into argv. Rejected. |

Observed evidence for the YAML substitution path:

- Config containing `api_key: ${ENDPOINT_BENCH_OFFLINE_API_KEY}` and no literal secret.
- The local mock received `Authorization: Bearer <value>` on every request.
- With the variable unset, `aiperf config validate` failed loudly: `Required environment variable 'ENDPOINT_BENCH_OFFLINE_API_KEY' is not set`. This is a fail-closed credential path.
- `grep` over the entire artifact directory found no occurrence of the secret. AIPerf also applies its own `api_key` redaction on JSON serialization.

`AIPERF_INJECTED_API_KEY`, `AIPERF_INJECTED_HEADERS`, and `AIPERF_INJECTED_ENDPOINT_URLS` are a genuine secret-free transport, but installed source shows they are consumed only by `orchestrator/subprocess_runner.py` and `cli_commands/service.py` — the multi-process and service paths. A plain local `aiperf profile` does not consume them. This is why the direct experiment showed no `Authorization` header.

**Decision:** the wrapper writes `api_key: ${<credential_env_name>}` into generated YAML, where `<credential_env_name>` comes from the registry. The secret value exists only in the subprocess environment. No secret appears in source, config, manifests, reports, argv, or artifacts.

## 5. Session affinity and default headers — the plan's specific concern

The plan asks for particular attention to AIPerf's session-affinity behavior and requires a normal-client baseline separate from any affinity-enabled experiment.

**Observed fact: AIPerf 0.13.0 sends `X-Session-Affinity` on every request by default.**

Two independent confirmations:

1. Installed source: `Environment.HTTP.X_SESSION_AFFINITY_FROM_CORRELATION_ID` defaults to `True`, and `transports/base_transports.py` sets `headers["X-Session-Affinity"] = request_info.x_correlation_id` when it is enabled.
2. Direct observation: the local mock received this header set on 100% of requests.

Header set observed on a successful streaming request:

```
User-Agent:            aiperf/0.13.0
X-Request-ID:          <per-request UUID>
X-Correlation-ID:      <stable per-session UUID>
Accept:                text/event-stream
Content-Type:          application/json
X-Session-Affinity:    <stable per-session UUID>
```

The exported `request_headers` in AIPerf's own artifacts show `X-Session-Affinity` as `<redacted>`, even though a real value is sent. Artifacts alone would hide this effect.

**This means AIPerf's default configuration is not a normal-client baseline.** A session-affinity header can pin a session to one replica and improve KV-cache locality, making an endpoint look faster than it would for an ordinary client.

Disabling it is supported and was verified:

```
AIPERF_HTTP_X_SESSION_AFFINITY_FROM_CORRELATION_ID=false
```

With that set, `X-Session-Affinity` disappears from the wire while `X-Correlation-ID` remains.

Other affinity headers — `X-Session-ID`, `X-SMG-Routing-Key`, `X-Dynamo-Session-ID` — default to `False` and are opt-in additive.

**Decision:** the wrapper always sets `AIPERF_HTTP_X_SESSION_AFFINITY_FROM_CORRELATION_ID` explicitly and records the resulting client profile in the run manifest. It never inherits AIPerf's default silently. Normal-client baseline runs set it to `false`; an affinity experiment sets it to `true` and is labeled as such. `X-Correlation-ID` is always present and must be recorded as part of every client profile, because a provider may treat it as a routing key.

`--session-header` only renames the single correlation header; it does not remove affinity.

## 6. Retries

**AIPerf 0.13.0 performs no automatic retries on the inference request path.** No retry flag exists, and no retry field exists in the endpoint config. The only retry loops in installed source are dataset download, tokenizer download, and internal clock-offset probing.

Consequence for the trial model: under AIPerf, one trial corresponds to one HTTP attempt. The plan's separate HTTP-attempt counter remains necessary for our own probes and for any future client that retries, but AIPerf does not create hidden attempt multiplication.

## 7. Connection reuse, timeouts, batching

| Control | Verified default / options |
|---|---|
| `--connection-reuse-strategy` | `pooled` (default), `never`, `sticky-user-sessions` |
| `--request-timeout-seconds` | **21600 s (6 hours)** — effectively no client timeout |
| Load model | closed-loop `--concurrency`; open-loop `--request-rate` + `--request-rate-mode` |
| `--wait-for-model-mode` | `inference` (default) — sends a real inference request before starting |
| `--wait-for-model-timeout` | `0.0` (default) — readiness probe skipped when zero |
| Warmup | opt-in via `--warmup-request-count` / `--warmup-duration` |

The 6-hour default timeout is unsuitable for qualification runs and must be set explicitly by every profile.

The default `wait-for-model-mode` being `inference` is a hidden-traffic risk: enabling a readiness timeout makes AIPerf send an unmeasured inference request before the benchmark. The wrapper pins `--wait-for-model-timeout 0` unless a profile explicitly opts into readiness probing, and any readiness request is recorded as unmeasured.

## 8. Tokenization and prompt handling

- `--tokenizer builtin` is a **zero-network** tokenizer backed by tiktoken `o200k_base`. This makes fully offline operation possible.
- A placeholder model name (`mock-model`, `test-model`, `fake-model`) auto-substitutes `builtin` with a warning.
- A real model name defaults to the HuggingFace tokenizer of that name, which requires network unless a local path is given. Any real-target run therefore has a tokenizer-provisioning dependency that must be recorded.
- `--use-server-token-count` prefers provider-reported usage; `--per-chunk-usage` requests cumulative usage per chunk (`continuous_usage_stats`), which strict OpenAI reject.
- `--apply-chat-template` is off by default, so reported ISL excludes template overhead.
- AIPerf computes `usage_prompt_tokens_diff_pct` and warns when client tokenization and API usage differ by more than a configurable threshold. This is direct evidence for the plan's "a 100K workload must not silently contain 80K tokens" requirement.
- Synthetic datasets are generated from a Shakespeare corpus by default; default ISL was observed at 550 tokens when unspecified.

**Decision:** offline and fixture-based work always pins `--tokenizer builtin`. Real-target profiles must declare a tokenizer strategy explicitly and record it.

## 9. Telemetry defaults

`gpu_telemetry.enabled` and `server_metrics.enabled` both default to **true**. On a host with no GPU and no Prometheus endpoint they produce warnings and empty telemetry blocks.

**Decision:** the wrapper disables both for offline and standard runs unless a profile opts in, so artifact contents stay deterministic.

## 10. Determinism

`--random-seed` makes synthetic data generation reproducible. `--search-random-seed` is separate and applies to search recipes.

**Decision:** every generated config pins `random_seed` so fixtures and manifests are reproducible.

## 11. Artifact layout and schemas

Generated files in `--output-artifact-dir`:

| File | Present when |
|---|---|
| `profile_export_aiperf.json` | summary export, only when at least one request succeeded |
| `profile_export_aiperf.csv` | same |
| `profile_export.jsonl` | per-request records (`records` export level) |
| `profile_export_raw.jsonl` | raw payloads via `raw: true` |
| `raw_records/*.jsonl` | raw payloads, per record-processor file |
| `inputs.json` | resolved run inputs |
| `logs/aiperf.log` | process log |
| `profile_export_console.txt` | console transcript |

### Summary schema

`profile_export_aiperf.json` carries `schema_version: "1.4"` and `aiperf_version: "0.13.0"`. Every metric is an object with a `unit` plus `avg`, `min`, `max`, `std`, `count`, `sum`, and `p1`–`p99` percentiles. It also carries `benchmark_id`, `run_info.cli_command`, `input_config` (the fully resolved configuration), `is_complete`, `was_cancelled`, `error_summary`, `start_time`, and `end_time`.

`schema_version` and `aiperf_version` are both present, so drift detection and unit preservation are straightforward: the parser keys on these and refuses unknown major versions rather than guessing.

### Per-request record schema

`profile_export.jsonl` records contain `metadata` (`session_num`, `x_request_id`, `x_correlation_id`, `conversation_id`, `turn_index`, phase info, `was_cancelled`, `context_overflow_skip`, `root_correlation_id`), `metrics` (value + unit per metric), optionally `payload` and `responses` when raw is enabled, and on failure an `error` object of shape `{type, message, cause}`.

### All-failure behavior — important

**When every request fails, AIPerf exits 1 and does not write the summary export at all.** Observed on a closed port: only `profile_export.jsonl`, `raw_records/*.jsonl`, `inputs.json`, and logs were produced; `profile_export_aiperf.json` was absent. Console reported `All 2 inference request(s) failed; no successful responses were collected.`

**Decision:** the wrapper treats a non-zero exit plus a missing summary as `all_requests_failed` and parses per-request `error` objects from `profile_export.jsonl`. It never assumes the summary file exists, and it never reports zero throughput from a missing summary.

### Raw evidence

Raw export contains complete prompt text and complete response packets. Raw artifacts are local evidence and must be sanitized before any export or publication.

## 12. Additional findings verified during Gate 2 completion

These were discovered while implementing the wrapper and probes, and each one changed the code.

1. **AIPerf leaves orphan workers when terminated with SIGTERM.** The controller exits on SIGTERM but its worker processes can survive, so a deadline kill that only signals the parent would leave processes still generating traffic against the endpoint. Group liveness must be checked *after* the parent is reaped and SIGKILL applied to the remaining group. Observed directly: `sigterm_sent=true`, parent reaped, group still alive until escalated.

2. **The singular `dataset:` shorthand is rejected once a sweep expands.** Sweep expansion adds `datasets:` alongside the shorthand and AIPerf raises `'dataset' cannot be used with 'datasets'`. Generated configuration therefore always uses the explicit `datasets: [{name: main, ...}]` list form.

3. **Sweep paths address dataset entries by name, not index.** `datasets.main.prompts.osl` is valid; `datasets.0.prompts.osl` raises `no entry named '0' found`.

4. **Config validation is stricter than assumed.** Unknown keys are rejected (`extra_forbidden`), and a missing `${VAR}` raises before any request is made. This is a genuinely fail-closed credential path.

5. **Tool-call argument fragmentation is per-chunk and per-index.** Reassembly must key on the delta's `index`, accumulate `function.arguments` fragments in arrival order, and treat a second distinct `id` on the same call as a protocol violation rather than overwriting the first.

6. **A redirect from the target is a protocol violation, not an instruction to follow.** Following it is the simplest way for an ostensibly local target to move a request somewhere else.

## 13. Integration invariants

Owner-specified acceptance criteria for all AIPerf integration code (2026-11-01). A run that violates any of them is an integration failure, not a benchmark result. Offline regression tests encode each one.

1. Baseline runs explicitly set `AIPERF_HTTP_X_SESSION_AFFINITY_FROM_CORRELATION_ID=false` **and verify absence of the header at the receiving server**. Affinity-enabled experiments use separate configuration and result identities.
2. Invoke the verified `aiperf profile` interface; never pass authentication through command-line arguments.
3. Inject credentials through environment-backed configuration and verify that secrets survive single runs and sweeps without appearing in logs, command arguments, or exported artifacts.
4. Disable readiness inference probes and unintended warmup traffic for baseline runs. Account for every actual HTTP request, including probes and warmups when deliberately enabled.
5. Explicitly configure per-request timeouts and an overall process deadline. Terminate and record stalled processes safely.
6. Treat nonzero AIPerf exits and absent summary exports as supported failure paths. Preserve whatever raw evidence is available and distinguish benchmark failure from integration failure.
7. Validate `schema_version` and `aiperf_version` before parsing results. No silent fallback for incompatible schemas.
8. Use the built-in tokenizer for offline integration tests, but verify workload token sizing against an appropriate model tokenizer or provider-reported usage before making token-normalized performance and long-context claims.
9. Preserve HTTP attempts separately from logical trials. Do not introduce implicit retries into baseline measurements.
10. Before enabling paid endpoints, pass local-mock regression tests for headers, credentials, readiness traffic, timeouts, failure exports, and request accounting.

## 14. What remains unknown

Recorded rather than assumed:

- Whether real providers honor `X-Session-Affinity`, `X-Correlation-ID`, or the other affinity headers. Unknown and provider-specific. The effect must be measured, not assumed.
- Real-tokenizer provisioning requires HuggingFace access or a local path; behavior with neither was not tested against a real model.
- GPU telemetry semantics on this host are unverified; telemetry is disabled by default as a result.
- `aiperf analyze`/`plot` were not exercised; they pull plotting dependencies and are out of scope until reporting needs them.
- Public dataset downloads (`--public-dataset`) reach HuggingFace and must never be enabled in offline tests.
- Long-context behavior, cache-bust behavior, and trace replay (`mooncake-trace`, `bailian-trace`, `baseten-trace`, `tracelab`) are documented in the CLI but were not exercised in this pass.

## 15. Consequences for implementation

1. AIPerf integration runs as a subprocess against the pinned `.venv-aiperf`, driven by generated YAML, never by long argv.
2. Credentials flow only through `${VAR}` substitution from the subprocess environment; `--api-key` is forbidden.
3. The client profile is explicit and recorded: affinity env var, session header, connection reuse, timeout, tokenizer, random seed, telemetry flags.
4. AIPerf's default affinity header means "AIPerf default" is a distinct client profile from "normal client baseline", and the two are never merged.
5. The parser keys on `schema_version` 1.4 and `aiperf_version` 0.13.0, preserves `unit`, and handles the missing-summary all-failure case.
6. Offline integration and CI require no AIPerf installation at all: the wrapper's parser and config builder are tested against recorded fixtures, and the live subprocess path stays opt-in.
