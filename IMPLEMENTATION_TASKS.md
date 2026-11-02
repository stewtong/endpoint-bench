# Implementation tasks — Agent Endpoint Bench v0.5

**Version:** 0.5.1
**Datestamp:** 2026-11-01  
**Source:** `AGENTS.md` and canonical `BENCHMARK_PLAN.md` v0.5.1.
**Status:** Approved execution checklist; not evidence of completed benchmark runs.  
**Update rule:** Keep gate deliverables, prerequisites, exits, and safety rules aligned with the canonical plan. Preserve historical evidence separately.

This is the execution checklist for coding agents working in this repository. The Markdown artifacts describe work to perform; they do not imply that an AIPerf harness, conformance layer, or AA importer has been implemented.

## Gate 0 — Repository survey and verified architecture

**Prerequisites:** Approved plan; access to repository and local tooling; no paid API calls.

**Deliverables**

- Inventory current files, scripts, fixtures, dependency managers, historical results, and credential environment-variable names without exposing or overwriting anything.
- Preserve optional external Token Factory evidence as historical observations; do not pool it with new trials or make a clean clone depend on it.
- Verify AIPerf 0.13.0, commit `794f8bb75f8582f22e412d7e650fc71ca2a3d21a`, Python requirements, CLI behavior, metric exports, trace replay, session affinity, configuration, credential handling, and limitations.
- Inspect AIPerf session-affinity behavior, default headers, retries, connection reuse, timeouts, request batching, prompt normalization, tokenization, conversation serialization, and cache-control effects.
- Review provider registry assumptions and current six Token Factory candidates as unverified configuration.
- Verify AA documentation, endpoint definitions, pagination, Free/Pro tiers, quotas, and applicable data-rights constraints.
- Record architecture decisions, verified assumptions, unknowns, limitations, and first milestone in `docs/decisions/0002-architecture-and-aiperf-verification.md`.

**Exit criteria**

- Architecture and dependency assumptions are verified from installed/source evidence.
- CLI choices are validated or explicitly deferred with limitations.
- No paid calls are made.
- Offline testing is possible.

**Status (2026-11-01): Gate 0 exit met.** AIPerf 0.13.0 was installed from the PyPI wheel (tag `v0.13.0` equals the pinned commit `794f8bb`), driven entirely offline against a local OpenAI-compatible mock, and recorded in `docs/decisions/0002-architecture-and-aiperf-verification.md`. Key verified facts: the benchmark command is `aiperf profile` (not `run`); credentials work through YAML `${VAR}` substitution with no secret in argv or artifacts; `X-Session-Affinity` is sent by default and must be disabled for a normal-client baseline; there are no automatic request retries; a total-failure run exits non-zero without writing the summary export.

## Gate 1 — Offline foundation

**Prerequisites:** Gate 0 exit.

**Deliverables**

- Typed provider/model registry schema, including endpoint, protocol, revision, region, capabilities, context, output limits, tokenizer, pricing metadata, auth env name, cache controls, and last-verified timestamp.
- Typed benchmark profile schema for smoke, quick, standard, long-context, reliability, and feature profiles.
- Trial lifecycle/outcome schema separating planned/started/transport/protocol/skipped/cancelled states from success/failure outcomes.
- Separate counters for planned trials, started trials, successfully completed trials, and underlying HTTP attempts.
- Immutable run manifests with UTC timestamps, versions, target fingerprints, fixture/config hashes, budgets, and client metadata.
- Safe `--dry-run` that creates zero inference traffic, validates schemas, resolves target/profile configuration, estimates exposure, and emits an inspectable manifest.
- CLI skeleton for `endpoint-bench registry validate`, `endpoint-bench probe`, `endpoint-bench run`, `endpoint-bench report`, and `endpoint-bench compare`.
- Local OpenAI-compatible mock provider and deterministic fixture generator.
- Normalized trial JSONL/Parquet serialization with sanitized logs and missing fields represented as unknown/null.
- Deterministic report generation and reproducible hash comparison.
- Initial AA parser, mapper, and snapshot-validator fixtures using mocked Free/Pro responses only.
- Unit/integration tests and CI pipeline that do not require credentials or network access.

**Exit criteria**

- A valid offline manifest and normalized trial evidence can be produced end-to-end from fixtures.
- Malformed configs, unsupported protocols, invalid targets, and invalid budgets fail loudly. Unknown pricing remains explicit in offline dry-run and blocks unattended live execution.
- No Token Factory/Nebius behavior is hard-coded in core orchestration.
- Dry-run makes no inference requests.

**Status (2026-11-01): Gate 1 exit met, with AA deferred by owner decision.** Delivered: extended registry schema (capability states, output limit, tokenizer, cache controls, last-verified); five profile families with explicit timeouts and provisional qualification metadata; lifecycle/outcome trial model with separate planned/started/transport/protocol/success/failure/skip/cancel counters and HTTP attempts; in-process OpenAI-compatible mock provider plus deterministic hashable fixture generator; normalized trial JSONL with sanitized logs; `probe`, `report`, and `compare` workflows. Two deliberate exceptions: the initial offline AA parser was deferred to Gate 5 by owner decision, and `report`/`compare` operate on produced evidence rather than a committed synthetic example (still open under Gate 6).

## Gate 2 — AIPerf and protocol integration

**Prerequisites:** Gate 1 exit; pinned AIPerf environment.

**Deliverables**

- Pinned-version AIPerf subprocess wrapper with CLI builder, config generation, environment-only credential handling, process/timeout capture, stdout/stderr sanitization, raw artifact preservation, and result parsing.
- Verified handling of AIPerf schema/version drift and metric units/denominators.
- Explicit controls and metadata for session affinity, default headers, retries, connection reuse, timeouts, prompt normalization, tokenization, conversation serialization, and cache controls.
- Separate normal-client baseline and affinity-enabled experiments when supported; never mix client configurations silently.
- Trace-replay provenance labeling that clearly distinguishes replay from live agent execution.
- Deterministic streaming conformance probes for stream start, chunks, usage, finish reason, truncation, interruption, timeout before/after first token, and 429/5xx.
- Deterministic live-turn tool-call round-trip probe:
  1. fixed request with declared tool schema;
  2. valid tool call, including streamed arguments when supported;
  3. fragmented argument assembly;
  4. call-ID/argument/schema validation;
  5. synthetic tool result associated with the correct call ID;
  6. complete conversation resubmission;
  7. complete, correctly terminated continuation.
- Supported variants for multiple tool calls, long tool observations, invalid tool arguments, interrupted tool-call streams, streaming/non-streaming, JSON/schema output, reasoning-enabled responses, and near-context requests.
- Deterministic tool-choice controls where supported.
- Explicit classification of provider/protocol failure versus model semantic behavior.
- Cost preflight with dated pricing, conservative upper-bound estimate, request/token/concurrency/duration ceilings, unknown-pricing handling, and fail-closed execution guards.
- Offline mocked integration tests covering all supported conformance and error paths.

**Exit criteria**

- Complete offline raw-to-normalized demonstration works on mocked responses.
- AIPerf metrics and custom probe results preserve provenance and unit metadata.
- Live-turn conformance contract is implemented and tested offline.
- Live smoke remains blocked pending explicit authorization.

**Status (2026-11-01): Gate 2 exit met for offline evidence.** Delivered and verified against the pinned engine and a local mock:

- AIPerf 0.13.0 subprocess wrapper: deterministic YAML generation, environment-only credential injection, explicit client profiles, per-request timeout plus process deadline, SIGTERM-then-SIGKILL process-group termination with orphan-worker detection, sanitized stdout/stderr, raw artifact preservation, and version-checked parsing that rejects unsupported schema.
- Explicit controls and metadata for session affinity, default headers, connection reuse, timeout, tokenization, prompt normalization, and cache controls where AIPerf exposes them. Baseline and affinity-enabled runs are separate result identities.
- Live-turn tool-call round-trip probe: fixed schema, fragmented streamed arguments, ordered reassembly, identifier and JSON and argument-type validation, synthetic tool result keyed to the correct call id, full-history resubmission, and required terminated continuation. Variants covered offline: multiple calls, missing and inconsistent call ids, invalid JSON, interrupted tool streams, absent tool calls attributed to the model, large tool observations, streaming and non-streaming, malformed and timed-out continuations.
- Attribution separating provider/protocol failure from model semantic behavior.
- Cost preflight guards with unknown-pricing fail-closed behavior, numeric-loopback-only local execution, proxy stripping, and redirect refusal.
- Normalized evidence with four distinct token measures, per-trial attribution, workload-type labelling, and planned-versus-actual request reconciliation.
- Offline regression tests encoding all ten integration invariants from `docs/decisions/0002-architecture-and-aiperf-verification.md`.

Remaining gaps, stated rather than implied:

- Trace-replay provenance labelling exists as a workload enum and report label, but no replay dataset has been executed through the harness.
- The raw-to-normalized demonstration runs through the AIPerf wrapper and through the independent probe client as separate evidence streams; there is no single AIPerf-produced tool-call run.
- Live qualification is not established. One authorized smoke run completed against a single deployment (see below); it is screening evidence, not qualification.

**Live smoke (2026-11-01, owner-authorized).** `deepseek-ai/DeepSeek-V4.1-Flash-1017` on `api.tokenfactory.nebius.com`, 2 requests, smoke profile, baseline client, $0.01 ceiling against a $0.00023 estimate. Both trials succeeded with HTTP 200 and no automatic retries. Observed (n=2, single window, screening only): TTFT 575.5 ms, request latency 699.0 ms, output throughput 44.2 tokens/sec. Raw evidence confirms no `X-Session-Affinity` header was sent and that the credential stayed out of every artifact.

The run also produced the first real token-sizing finding: the provider reported 160 and 165 prompt tokens against AIPerf's built-in tokenizer count of 128, a 25.0% and 28.9% discrepancy. This is a genuine tokenizer mismatch, not a harness defect, and it is why the built-in tokenizer must not be treated as authoritative for a hosted model. `claims_supported` is therefore false for this run and no token-normalized claim is made.

What this does **not** establish: capacity, concurrency behaviour, cache behaviour, long context, tool support on the live endpoint, reliability over time, or any qualification verdict.

## Gate 3 — Standard performance profiles

**Status (2026-11-01): preparation only; Gate 3 is not entered.** See [`docs/GATE3_PREPARATION.md`](docs/GATE3_PREPARATION.md). Delivered toward this gate: metric-specific validity states so token-sizing failures no longer erase latency evidence; a reproducible local-mock benchmark with agent-shaped Mooncake trace replay and a normalized report; three screening profiles at 1K/10K/32K with a concurrency override; exact dry-run manifests and conservative cost estimates for six screening runs and one live tool-call continuation test. No screening profile has been executed against a live endpoint.


**Prerequisites:** Gate 2 offline exit; explicit user authorization for any live execution.

**Deliverables**

- Immutable smoke, quick, and standard profiles with reusable agent-shaped traffic fixtures for coding, logs, research, documents, structured files, and tools.
- License-clean synthetic data or explicitly approved traces, with fixture hashes and provenance.
- 1K/10K/32K/100K workload profiles with verified serialized/tokenized sizes.
- Bounded concurrency sweeps at C=1/2/4/8 and controlled offered-rate behavior where supported.
- Stable-prefix, growing-transcript, early-prefix invalidation, interleaved-session, and controlled warm/cold cache scenarios.
- Provider-reported cache accounting separated from timing-inferred benefit, latency improvement, and cost improvement.
- Randomized/interleaved run order and repeated observations across time windows.
- Profile-specific qualification thresholds stored in versioned, format-neutral configuration, with provisional status until calibrated.
- Statistical summaries with sample size, uncertainty, temporal coverage, and conservative percentile handling.
- Reports separating direct API, AIPerf replay, custom probes, and framework evidence; no intelligence scoring.

**Exit criteria**

- A repeatable standard profile can qualify, fail, or mark inconclusive a target based on evidence.
- A new endpoint can be added primarily through configuration.
- Historical comparisons preserve methodology/config provenance.

## Gate 4 — Long-context and reliability qualification

**Prerequisites:** Gate 3 exit; explicit authorization for large-context, sustained-load, and high-concurrency work.

**Deliverables**

- Context/tokenizer/output-headroom preflight with explicit token counts, budget estimates, payload limits, and safety margin.
- Separate advertised context, API-accepted context, demonstrated effective context, active per-request context, cumulative session consumption, client compaction, and unknown provider transformations.
- Guarded escalation from 128K→256K→512K→768K→safe ~1M only where supported.
- Sentinel checks at the beginning, middle, and end of large contexts; distinguish model failure from server-side truncation.
- Explicit states for rejected, truncated, budget-skipped, accepted-but-unverified, and demonstrated context.
- Cache-retention experiments at controlled intervals, such as 5m/30m/2h.
- Multi-window reliability measurements, bounded sustained load, and concurrent capacity qualification.
- Conservative capacity findings scoped to tested account, region, endpoint, and time window.

**Exit criteria**

- Evidence-backed findings distinguish acceptance from effectiveness.
- All large-load operations were explicitly authorized.
- Qualification, failure, and inconclusive results are supported by objective thresholds and sample evidence.

## Gate 5 — AA synchronization and reference dataset

**Prerequisites:** Gate 1 offline AA fixtures; explicit confirmation of AA access tier and data-use rights for live synchronization.

**Deliverables**

- Authenticated AA importer using `AA_API_KEY` from the environment only.
- Free and Pro endpoint support, pagination, bounded backoff, and explicit 401/403/429 handling.
- Free-mode ingestion of available indices while preserving absent coding/agentic scores as null.
- Pro-mode ingestion of published per-benchmark evaluations as-is, without filling absent scores.
- Immutable raw pages, normalized `models.jsonl`, snapshot manifests, AA index/version, access tier, source URLs, page counts, hashes, attribution, and rights metadata.
- Explicit model-ID→AA-ID mapping with revision, reasoning tier, confidence, provenance, and states for unmatched, ambiguous, unavailable, not entitled, and not measured.
- Schema-evolution handling that preserves unknown fields and adds fixture-based regression tests.
- Historical snapshots retained without overwrite; latest pointer updated only after successful complete validation.
- `docs/AA_DATA.md` documenting attribution, licensing constraints, and redistribution boundaries.
- Offline unit tests for pagination, Free/Pro fixtures, missing/null scores, auth/rate-limit responses, ambiguous aliases, revision mismatch, unknown fields, methodology/index changes, and incomplete snapshots.

**Exit criteria**

- Authorized users can refresh AA reference snapshots independently of endpoint measurements.
- No restricted AA records are committed or redistributed without rights.
- No joint leaderboard, ranking, recommendation, or routing logic is introduced.

## Explicit deferral

Do not implement now:

- joining AA scores and endpoint performance;
- weighted ranking;
- workload matching;
- auto-routing;
- cloud selection;
- intelligence conclusions;
- public leaderboards from restricted AA data.

Future work may consume AA snapshots, mapping files, and qualified endpoint datasets after evidence standards and data rights are defined.

## Developer interface targets

```bash
endpoint-bench registry validate
endpoint-bench aa sync --tier auto --scope tracked
endpoint-bench aa validate --snapshot external/aa/snapshots/<stamp> --offline
endpoint-bench probe --target <deployment_key> --profile smoke --dry-run
endpoint-bench run --target <deployment_key> --profile quick --dry-run
endpoint-bench run --target <deployment_key> --profile quick --execute --max-usd <cap> --max-requests <cap>
endpoint-bench report --run <run_id>
endpoint-bench compare --baseline <run_id> --candidate <run_id>
```

Dollar/request values are syntax examples, not spending authorization. Reject `--execute` without a selected target, validated manifest, cost/request ceilings, and safe concurrency/rate limits. Run AA sync separately from paid inference.

## Gate 6 — Public-readiness and engineering quality

**Status (2026-11-01):** Pre-alpha source foundation delivered. Gate 6 items below are re-verified against current behavior; release and benchmark MVP remain distinct.

- [x] Add a Python 3.13 package, CLI entry point, registry validation, and network-free dry-run manifest.
- [x] Fail closed on a missing run mode. `--execute` is now implemented and still fails closed: request ceilings are mandatory, and non-loopback targets require explicit authorization, verified pricing, and a cost ceiling the conservative estimate must fit under.
- [x] Add accurate README, methodology, result contract, development guide, contribution guide, security policy, changelog, and synthetic example.
- [x] Add offline tests, GitHub Actions CI, pinned actions, locked dependencies, and least-privilege workflow permissions.
- [x] Obtain owner license approval and add the MIT `LICENSE`.
- [x] Verify the offline workflow in an isolated local checkout; macOS compatibility is verified only for that local environment.
- [x] Complete Linux clean-clone verification and observe a passing GitHub Actions run. Verified 2026-11-02: all four CI jobs passed on ubuntu-latest, including the opt-in engine-integration job that installs AIPerf 0.13.0.
- [x] Validate normalized trial artifacts: lifecycle/outcome invariants, counter nesting, and per-record schema versioning are enforced before any trial is counted.
- [ ] Commit a synthetic report example alongside the existing synthetic dry-run manifest.
- [x] Repository published publicly with Dependabot alerts, Dependabot security updates, secret scanning, and push protection enabled.

The pre-alpha source milestone is ready for release review after license approval and CI verification; live inference is not required for it.

The benchmark MVP's implementation criteria are now met offline: AIPerf integration, streaming and tool-call conformance probes, normalized evidence, reports, and one authorized live experiment all exist. What remains is not implementation but evidence at scale: repeatable profiles exercised with enough samples and temporal coverage to support a qualification verdict. Expanded cache, long-context, reliability, and AA work remains a later milestone.

**Exit:** A fresh outsider can install, run the offline example, inspect the manifest, and understand that no endpoint has been qualified. One authorized 2-request smoke run is labelled as screening evidence only. A pre-alpha release additionally requires approved licensing and verified CI. A benchmark MVP additionally requires the Gate 2/3 live evidence listed above.

## Useful references

- AIPerf 0.13.0: <https://github.com/ai-dynamo/aiperf>
- AIPerf benchmark datasets: <https://github.com/ai-dynamo/aiperf/blob/main/docs/benchmark-datasets.md>
- AIPerf agentic-code generator: <https://github.com/ai-dynamo/aiperf/blob/main/docs/tutorials/agentic-code-generator.md>
- AA Data API: <https://artificialanalysis.ai/data-api/docs>
- AA API reference: <https://artificialanalysis.ai/data-api/docs/reference>
- AA methodology: <https://artificialanalysis.ai/methodology/intelligence-benchmarking>
