# Implementation tasks: Agent Endpoint Bench v0.5

**Version:** 0.5.5
**Datestamp:** 2026-11-03
**Source:** `AGENTS.md` and canonical `BENCHMARK_PLAN.md` v0.5.1, plus the D0010 same-day correction.
**Status:** Approved execution checklist; not evidence of completed benchmark runs. The default done bar is the same-day session tooling plus accepted designated results/report/handoff. Gates 3 and 4 multi-day qualification are optional future scope, not default completion requirements.
**Update rule:** Keep gate deliverables, prerequisites, exits, and safety rules aligned with the canonical plan. Preserve historical evidence separately.

The same-day implementation and designated results are complete. The accepted implementation passed 445 offline tests. Extended load, larger-context and metadata-import work remains optional; historical milestones below retain their original dates.

## Gate 0: Repository survey and verified architecture

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

## Gate 1: Offline foundation

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

## Gate 2: AIPerf and protocol integration

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
- Live execution requires explicit authorization and is not part of this gate's offline exit.

**Status (2026-11-01): Gate 2 exit met for offline evidence.** Delivered and verified against the pinned engine and a local mock:

- AIPerf 0.13.0 subprocess wrapper: deterministic YAML generation, environment-only credential injection, explicit client profiles, per-request timeout plus process deadline, SIGTERM-then-SIGKILL process-group termination with orphan-worker detection, sanitized stdout/stderr, raw artifact preservation, and version-checked parsing that rejects unsupported schema.
- Explicit controls and metadata for session affinity, default headers, connection reuse, timeout, tokenization, prompt normalization, and cache controls where AIPerf exposes them. Baseline and affinity-enabled runs are separate result identities.
- Live-turn tool-call round-trip probe: fixed schema, fragmented streamed arguments, ordered reassembly, identifier and JSON and argument-type validation, synthetic tool result keyed to the correct call id, full-history resubmission, and required terminated continuation. Variants covered offline: multiple calls, missing and inconsistent call ids, invalid JSON, interrupted tool streams, absent tool calls attributed to the model, large tool observations, streaming and non-streaming, malformed and timed-out continuations.
- Attribution separating provider/protocol failure from model semantic behavior.
- Cost preflight guards with unknown-pricing fail-closed behavior, numeric-loopback-only local execution, proxy stripping, and redirect refusal.
- Normalized evidence with four distinct token measures, per-trial attribution, workload-type labelling, and planned-versus-actual request reconciliation.
- Offline regression tests encoding all ten integration invariants from `docs/decisions/0002-architecture-and-aiperf-verification.md`.

Remaining gaps, stated rather than implied:

- Trace-replay provenance labelling exists as a workload enum and report label, and the synthetic Mooncake trace has been executed through AIPerf against the local mock (12 trials, see [`docs/GATE3_PREPARATION.md`](docs/GATE3_PREPARATION.md)). No replay has been executed against a live endpoint.
- The raw-to-normalized demonstration runs through the AIPerf wrapper and through the independent probe client as separate evidence streams; there is no single AIPerf-produced tool-call run.
- Live qualification is not established. One authorized smoke run was executed against a single deployment (see below); it is screening evidence, not qualification.

**Live smoke (2026-11-01, owner-authorized).** `deepseek-ai/DeepSeek-V4.1-Flash-1017` on `api.tokenfactory.nebius.com`, 2 requests, smoke profile, baseline client, $0.01 ceiling against a $0.00023 estimate. Both trials returned HTTP 200 with no automatic retries, but **both stopped at the 32-token output ceiling** (`finish_reason: length`) and spent the entire completion budget on reasoning tokens, emitting no visible answer. Under the corrected classification they are ceiling truncations attributed to `harness`, not successes: `successful_trials: 0`, `truncated_trials: 2`. Raw evidence confirms no `X-Session-Affinity` header was sent and that the credential stayed out of every artifact.

Of the published figures, **TTFT 575.5 ms stands**: the first token arrives long before any ceiling applies. **Request latency 699.0 ms is time-to-cap, not completion latency**, and output throughput describes reasoning-token emission under a 32-token limit. Corrected evidence: [`docs/gate3-corrections-2/run-34efa7f73ac1/`](docs/gate3-corrections-2/run-34efa7f73ac1/). A second smoke run directory (`runs/run-fd51a4f8d1ad`) exists from before raw capture was enabled; it has no raw export, so under current rules no trial in it can be asserted successful and it supports no claim.

The run also produced the first real token-sizing finding: the provider reported 160 and 165 prompt tokens against AIPerf's built-in tokenizer count of 128, a 25.0% and 28.9% discrepancy. This is a genuine tokenizer mismatch, not a harness defect, and it is why the built-in tokenizer must not be treated as authoritative for a hosted model.

Under the corrected two-check model **both** comparisons fail on this run, which is more informative than the single verdict it originally produced:

- `client_vs_provider` fails: the two tokenizers disagree by 25%.
- `provider_vs_target` **also** fails. Asking for "128 tokens" asked for 128 *built-in-tokenizer* tokens; the provider counted 160. The delivered workload was therefore about 25% larger than requested in the provider's own units. Requested-size precision is limited by tokenizer identity, not just by the harness.

Because this run used client tokenization, token-normalized throughput is withheld (`invalid_token_sizing`) rather than retained as a delivered-workload observation ;  the provider's `usage` was available but was not the accounting source. `claims_supported` is false and no token-normalized claim is made.

What this does **not** establish: capacity, concurrency behaviour, cache behaviour, long context, tool support on the live endpoint, reliability over time, or any qualification verdict.

## Gate 3: Standard performance profiles

**Status (2026-11-02): screening executed, then corrected 2026-11-01; Gate 3 is optional future scope.** See [`docs/GATE3_PREPARATION.md`](docs/GATE3_PREPARATION.md). Delivered toward this gate: metric-specific validity states so token-sizing failures no longer erase latency evidence; a reproducible local-mock benchmark with agent-shaped Mooncake trace replay and a normalized report; three screening profiles at 1K/10K/32K with a concurrency override; exact dry-run manifests and conservative cost estimates for six screening runs and one live tool-call continuation test. Six screening cells (1K/10K/32K at concurrency 1 and 2) have been executed against one deployment: 120 planned trials, 120 HTTP attempts, zero endpoint failures. Results are in `docs/GATE3_RESULTS.md`. Versioned qualification thresholds calibrated against multi-window evidence, randomized run ordering, and cache scenarios remain unbuilt; D0010 removed them from the default done bar and keeps them as optional future scope.

**Measurement-integrity correction (2026-11-01).** Reprocessing all six cells from their original raw artifacts found three harness defects that changed published numbers. See [`docs/GATE3_RESULTS_CORRECTION_1.md`](docs/GATE3_RESULTS_CORRECTION_1.md).

1. Token sizing was circular under provider accounting: AIPerf derives its input-length metric from provider usage in that mode, so the client-versus-provider comparison compared a number with itself. Sizing is now two separately reported verdicts and the client measure is absent, not back-filled, under provider accounting.
2. Every protocol-completed response was scored `success`. `finish_reason` was never read and was `null` in all 120 stored records. In fact **117 of 120 responses stopped at the 256-token output ceiling**; 3 completed naturally. Ceiling truncation is now `output_truncated` attributed to `harness`, counted in `truncated_trials`, and excluded from `successful_trials`. `success` now requires a witnessed 2xx status and a natural finish reason.
3. Cache-read, cache-write, and reasoning token counts were discarded. All three are now retained per trial with provenance; cache-write exists only in the raw stream because AIPerf 0.13.0 has no metric for it.

Corrected totals: 3 natural completions, 117 ceiling-truncated, 0 endpoint failures. Latency is now marked right-censored for truncated trials. TTFT, latency means, and provider-versus-target conformance (1.16 to 3.99%) are unchanged. The client-versus-provider check is permanently unrecoverable for these runs because no client tokenizer ever ran.

Also delivered: `endpoint-bench reprocess` for re-deriving published evidence without re-execution, a 10K multi-window experiment plan whose 1024-token ceiling is an untested declared choice, and an SLO contract that separates objective data-quality gates from business performance thresholds. The contract currently returns `inconclusive`, which is the correct answer while no threshold is ratified.

**Direction after review (2026-11-01).** The current posture is **characterization**: publish measured behavior and leave the verdict `inconclusive` until an operator ratifies a requirement. No threshold may be derived from the run it judges. Two further changes are in place:

- The contract now evaluates performance on a **pooled** per-trial sample and treats a window as an evidence-validity unit only. Version 1.0 asked a 20-request window to satisfy a p50 objective under its own 30-sample floor, which was unsatisfiable at any endpoint quality.
- A separate exploratory pilot is prepared at [`configs/profiles/pilot-10k-2048.json`](configs/profiles/pilot-10k-2048.json) and [`docs/gate3-plans/pilot-10k-2048.json`](docs/gate3-plans/pilot-10k-2048.json): 40 requests (20 at C=1, 20 at C=2), 10,240 requested input tokens, 2048-token output ceiling, $0.165888 per run against a $0.20 guard. It is a distinct workload identity from the 1024-token plan and is not authorized. A clean pilot does not certify its ceiling: zero censoring in 40 trials gives a one-sided 95% exact binomial upper bound of about 7.2% on the censoring probability.

Reasoning and the withdrawn claims from earlier analysis are recorded in [`docs/decisions/0003-qualification-threshold-and-censoring-policy.md`](docs/decisions/0003-qualification-threshold-and-censoring-policy.md). A contract evaluator exists but is exercised only against synthetic evidence; it is not a live qualification pipeline and no endpoint has been qualified.

**Pilot execution (2026-11-01).** The C=1 pilot cell ran against the live deployment under the plan's $0.20 ceiling: 20 planned, 20 HTTP attempts, 13 natural completions, 7 ceiling truncations at 2048 tokens, 0 endpoint failures. All seven truncated responses were reasoning-only and emitted no visible answer. The pilot stop rule requires zero truncations, so C=2 was not executed and no further paid cell was started. Provider-versus-target conformance passed 20/20 (0.03 to 3.00%). TTFT is observed (median 1,253 ms); request latency is right-censored and supports no completion claim. Full counters, telemetry, cost estimates and limitations are in [`docs/GATE3_PILOT_RESULTS.md`](docs/GATE3_PILOT_RESULTS.md).

**Standard profile execution (2026-11-01).** After the pilot stopped, the four standard profiles ran once each at their declared ceilings: quick (20 requests, 2,048/256, C=2), standard (200, 4,096/512, C=4), reliability (300, 1,024/128, C=2), and long-context (8, 131,072/512, C=1). Total 528 requests, 528 HTTP attempts, 48 natural completions, 480 ceiling truncations, 0 endpoint failures. The endpoint accepted 131,072-token inputs with provider conformance within 1.65%. Completion latency is censored for 90.9% of the suite, so these runs measure TTFT, capped throughput, conformance and telemetry, not completion performance. Results and the completion audit are in [`docs/GATE3_STANDARD_PROFILE_RESULTS.md`](docs/GATE3_STANDARD_PROFILE_RESULTS.md).

**8192 ceiling attempt (2026-11-01).** A new 8192-token pilot completed 20/20 naturally (282 to 3,563 tokens), so the first multiwindow cell at the same ceiling was run. That cell censored 2 of 20 responses, both reasoning-only at exactly 8192 tokens, and the multiwindow stop rule fired after cell 1 of 12. C=2 and windows 2 to 6 were not executed. Combined, 2 of 40 requests were censored (5.0%; one-sided 95% exact upper bound 14.9%), so a 240-request zero-censoring gate is not a feasible design at this ceiling. The attempt also exposed a harness defect: identical cells produced the same run ID and the second cell was refused. `run --run-label` now makes repeated run identities distinct without changing the AIPerf config. Results are in [`docs/GATE3_MULTIWINDOW_8192_RESULTS.md`](docs/GATE3_MULTIWINDOW_8192_RESULTS.md).

**16384 ceiling attempt (2026-11-01).** A 16384-token pilot censored 1 of 20 responses, again reasoning-only and exactly at the cap; 19 completed naturally at 260 to 10,851 tokens. The observed censoring rate stayed at 5% when the ceiling doubled, so a higher fixed cap shifts which requests are censored rather than eliminating the tail. The escalation ladder is exhausted for this workload: no fixed-cap 240-request zero-censoring multiwindow is feasible, and none was run at 16384. TTFT, capped throughput, provider conformance and telemetry remain measurable. Results are in [`docs/GATE3_CEILING_ESCALATION_RESULTS.md`](docs/GATE3_CEILING_ESCALATION_RESULTS.md).

**Bounded multiwindow (2026-11-01).** The censoring-tolerant extension was implemented in [`src/endpoint_bench/bounds.py`](src/endpoint_bench/bounds.py) with [`scripts/analyze_bounded.py`](scripts/analyze_bounded.py) and 15 regression tests. A cap-hit time is a per-trial lower bound; a lower bound above an upper-bound threshold proves failure, a lower bound below it stays inconclusive, and censored trials are never dropped. The 12-cell 8192 multiwindow then completed: 240 requests, 233 natural completions, 7 ceiling truncations, 0 endpoint failures. Pooled p50 completion-latency lower bounds are 2,767 ms at C=1 and 2,826 ms at C=2; TTFT p50 is 760 to 788 ms. No threshold is ratified, so every bound verdict is `inconclusive` and no endpoint is qualified. Results are in [`docs/GATE3_MULTIWINDOW_8192_BOUNDED_RESULTS.md`](docs/GATE3_MULTIWINDOW_8192_BOUNDED_RESULTS.md).

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

## Gate 4: Long-context and reliability qualification (optional future scope)

**Prerequisites:** explicit authorization for large-context, sustained-load, and high-concurrency work. This gate is not part of the same-day default done bar and does not block acceptance of the D0010 tooling or the designated results.

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

## Gate 5: AA synchronization and reference dataset

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

## Gate 6: Public-readiness and engineering quality

**Status (2026-11-01):** Pre-alpha source foundation delivered. Gate 6 items below are re-verified against current behavior; release and benchmark MVP remain distinct.

- [x] Add a Python 3.13 package, CLI entry point, registry validation, and network-free dry-run manifest.
- [x] Fail closed on a missing run mode. `--execute` is now implemented and still fails closed: request ceilings are mandatory, and non-loopback targets require explicit authorization, verified pricing, and a cost ceiling the conservative estimate must fit under.
- [x] Add accurate README, methodology, result contract, development guide, contribution guide, security policy, changelog, and synthetic example.
- [x] Add offline tests, GitHub Actions CI, pinned actions, locked dependencies, and least-privilege workflow permissions.
- [x] Obtain owner license approval and add the MIT `LICENSE`.
- [x] Verify the offline workflow in an isolated local checkout; macOS compatibility is verified only for that local environment.
- [x] Complete Linux clean-clone verification and observe a passing GitHub Actions run. Verified 2026-11-02: all four CI jobs passed on ubuntu-latest, including the opt-in engine-integration job that installs AIPerf 0.13.0.
- [x] Validate normalized trial artifacts: lifecycle/outcome invariants, counter nesting, and per-record schema versioning are enforced before any trial is counted.
- [x] Commit a synthetic report example alongside the existing synthetic dry-run manifest. `examples/synthetic/report.json` is a loopback-mock run at schema `0.2.0`, with scratch paths genericized and a test that keeps it loadable.
- [x] Repository published publicly with Dependabot alerts, Dependabot security updates, secret scanning, and push protection enabled.

The pre-alpha source milestone is satisfied: license approved, CI verified on Linux, public repository with scanning enabled. The benchmark MVP criteria below are also met; neither constitutes a qualification milestone.

The benchmark MVP's criteria are met, online as well as offline: AIPerf integration, streaming and tool-call conformance probes, normalized evidence with per-metric validity, reports, and reproducible authorized live experiments all exist and have run. What remains is not implementation but evidence at scale: multi-window repeats, interleaved ordering, and calibrated thresholds before any qualification verdict. Cache, long-context, reliability, and AA work remains a later milestone.

**Exit:** A fresh outsider can install, run the offline example, inspect the manifest, and understand that no endpoint has been qualified. Screening measurements exist and are labelled as such. Licensing and CI verification are satisfied; the benchmark MVP criteria are met.

## D0010 same-day session default (2026-11-03)

**Status:** `COMPLETE`. This is the current default. It replaces the multi-day D0009 calendar program as the repository's done bar. D0010 implementation/preflight was accepted; D0011 executed the released context cells (see below). No new paid request was made during implementation.

**Delivered**

- Versioned program definition [`configs/session-programs/session-dynamics-sameday-v1.json`](configs/session-programs/session-dynamics-sameday-v1.json): `session-dynamics-sameday-v1`, program schema `2.0`, plan schema `session-plan/2.0.0`, manifest schema `session-manifest/2.0.0`, workload `live_turn_session_dynamics_same_day`. The matrix is 30 cells / 558 maximum calls: 27 screening cells (nine scenarios at C=1/2/4, 20 calls each) plus three context sentinels (32K/64K/128K, 6 calls each). No temporal-repeat cell is in the default.
- One foreground command: `endpoint-bench session same-day`. Dry-run/preflight is the default; `--execute` sends inference and a non-loopback target additionally requires `--authorize-live` and `--max-usd`. The command generates the plan and manifest, executes the enumerated cells in order, analyzes, and writes `REPORT.md` in the same invocation. `--select context` is a finite execution-scoped selection for the 3-cell / 18-call completion proposal.
- Bounds unchanged: no retries, replacements or hidden probes; C<=4; output 8192; input 32768/session and 131072/context; request 240s. Default cell deadline 600s and total foreground invocation deadline 6h; a guard stop records skipped cells and keeps them resumable. Completed and interrupted cells are never replayed. The 24h journal-lifetime check is a legacy `session execute` guard; it is not enforced by the `session same-day` foreground path.
- Legacy preservation: `configs/session-programs/session-dynamics-v1.json`, `docs/session-dynamics-v1/` (plan, released manifest, journal, 33 cells, W1 evidence) are unchanged. W2/W3/W4 and the three unstarted context cells are cancelled, not completed, missed or provider failures.
- New regressions in [`tests/test_session_same_day.py`](tests/test_session_same_day.py): same-day planning, no anchor waits, finite invocation deadline, end-to-end loopback run -> analysis/report, cancellation/resume/no-replay, per-cell argv without an anchor, and legacy evidence hashes.

**D0011 context completion (2026-11-04):** the exact released `--select context` invocation completed 3 cells / 18 calls: 18 HTTP 200, 18 natural stops, 0 refusals / output truncations / malformed / unverified, and 18/18 exact sentinel checks at early/middle/late positions. Provider input tokens were 24.2K / 48.8K / 98.4K at the 32K / 64K / 128K construction sizes (about 74 to 75% of the client estimate), so delivered-size conformance is `outside_tolerance`; the late sentinel passed at every size, which supports a tokenizer/estimate difference rather than truncation. Cumulative new-program attempts 630 (D0009) + 18 = 648. The same-day 27 screening cells remain unmeasured. Combined report: [`docs/reports/sameday-context-final/REPORT.md`](docs/reports/sameday-context-final/REPORT.md).

## D0003/D0009 legacy temporal session-dynamics (historical record)

**Status:** `AWAITING_REVIEW`. D0008 offline closeout is complete: the fresh-interpreter resolver, its runtime hash and the target binding are in place. No live operation is released and no paid traffic has been sent.

**Delivered**

- Versioned program definition [`configs/session-programs/session-dynamics-v1.json`](configs/session-programs/session-dynamics-v1.json): 54 cells, 1,038 maximum attempts, nine scenarios, C=1/2/4, four temporal windows, three context sizes.
- `session_program.py`: deterministic coding/document/transcript templates, matched prefix/suffix lengths, explicit cache group identity, frozen session/turn order, schedule anchors, source/workload/config/plan hashes, manifest and conservative operational envelope.
- `session_engine.py`: live-turn adapter with inherited probe transport/SSE/tool validation, dependent transcripts retaining real replies, client reconnect, deterministic local compaction, 60 s idle, fixed in-process tools, first-any/reasoning/visible timing, input-bound enforcement, no retries, one outstanding call per session.
- `session_evidence.py`: immutable cell/call/session evidence, restart journal, fail-closed validation, denominators, scenario/window/template/context aggregation, Markdown report.
- CLI `endpoint-bench session generate|dry-run|execute|analyze`; `execute` is guarded by release, target, exact request cap, dollar envelope, credential presence and attempt cap.
- Repaired analysis chain: `bounds.py` rejects non-finite/boolean values and inconsistent completion metadata; `analyze_bounded.py` rejects duplicate paths, run/request identities, concurrency-label mismatch and incompatible pooling, and publishes all denominators.
- Historical reconciliation: [`docs/HISTORICAL_RUN_INVENTORY.md`](docs/HISTORICAL_RUN_INVENTORY.md) deduplicates 21 run identities (831 unique attempts) and marks every entry retrospective with unresolved authority. Interpretation corrections cover the 60 s runtime versus 240 s plan timeout, reused window-1 chronology, and finite-sample ceiling-tail scope.
- Target-bound preflight packet: [`docs/session-dynamics-v1/`](docs/session-dynamics-v1/) with plan, 54-cell manifest, exact argv and hashes.

**Verification:** The D0008 offline gate records 434 tests passing in 106.47 s, lint clean, and both plan/reprocess checkers exit 0; the preflight was rebuilt with zero endpoint calls. Dry-run/generate make zero endpoint calls and need no credentials. Mock coverage asserts transcript isolation, reconnect state, round-robin scheduling, idle/active separation, tool continuation budgets, cache treatment construction, visible-token timing, truncation/errors, duplicate evidence, restart/no-repeat, bounded resolver cleanup and connection reuse.

**Remaining (superseded by D0010 for the default):** D0009 executed the 27 screening cells and the 6 W1 temporal cells against one deployment (33 cells / 630 attempts). D0010 revoked the unstarted W2/W3/W4 and context cells and removed the multi-day schedule from the default. The target binding's origin and exact model id were confirmed by one read-only model listing; region, revision, feature and context behavior remain unverified. The original plan, journal, cells, transcripts and raw evidence remain preserved unchanged. C8, offered-rate/sustained capacity, >131,072-input and ~1M context remain separate optional designs in `docs/SESSION_FOLLOWON_SCOPE.md`. No qualification verdict is possible from this program.

## Useful references

- AIPerf 0.13.0: <https://github.com/ai-dynamo/aiperf>
- AIPerf benchmark datasets: <https://github.com/ai-dynamo/aiperf/blob/main/docs/benchmark-datasets.md>
- AIPerf agentic-code generator: <https://github.com/ai-dynamo/aiperf/blob/main/docs/tutorials/agentic-code-generator.md>
- AA Data API: <https://artificialanalysis.ai/data-api/docs>
- AA API reference: <https://artificialanalysis.ai/data-api/docs/reference>
- AA methodology: <https://artificialanalysis.ai/methodology/intelligence-benchmarking>

## Measurement-integrity follow-up (software 0.1.2)

[Correction 2](docs/GATE3_RESULTS_CORRECTION_2.md) tightens malformed-stream and natural-finish validation, preserves provider-token observations independently of target conformance, and withholds natural-completion summaries for censored latency. Original source evidence and Correction 1 remain preserved. Endpoint error rate is a performance objective in the draft contract. The repeat plan is exploratory, has ten-minute start spacing and a first-window pilot stop rule, and remains unauthorized. The contract checker validates selected document properties; it is not a qualification evaluator. These changes do not exit Gate 3.
