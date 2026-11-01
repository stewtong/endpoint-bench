# Implementation tasks — Agent Endpoint Bench v0.3

**Version:** 0.3.0  
**Datestamp:** 2026-11-01  
**Source:** `AGENTS.md`, canonical `BENCHMARK_PLAN.md` v0.3.0, repository inspection, AIPerf 0.13.0, and Artificial Analysis documentation.  
**Status:** Approved execution checklist; not evidence of completed benchmark runs.  
**Update rule:** Keep gates aligned with the canonical plan and preserve historical evidence separately.

This is the execution checklist for a coding agent working in the actual project repository. **The markdown artifacts describe work to perform; they do not imply that an AIPerf harness or AA importer has been implemented.** Read `AGENTS.md` for execution/safety rules and `BENCHMARK_PLAN.md` for methodology.

## Gate 0 — Local survey and integration contract

- [ ] Inventory current files, `tf_bench.py` and OpenClaw runner (if present), dependency managers, existing results, credentials by **env-var name only**; do not overwrite anything.
- [ ] Use the existing `agent_setups/tf-benchmark` directory as the dedicated project root; do not create a separate clone/worktree unless the user explicitly requests one. Isolate dependencies with `uv`/venv and do not touch launchd services.
- [ ] Pin AIPerf 0.13.0, commit `794f8bb75f8582f22e412d7e650fc71ca2a3d21a`, in a Python 3.13 environment. Check installed help/API for multi-turn traces, Mooncake/agentic-code replay, metrics export, caching, TTFO, load generation, config validation, and authentication/URL configuration. Document missing support and supported workarounds.
- [ ] Confirm current AA `/language/models/free` and `/language/models` schema/tier and applicable data rights from official docs; treat live AA sync as separately authorized and never ask for the actual key in source/chat.
- [ ] Preserve OpenClaw as separate prior/optional framework evidence; defer framework integration until the direct-API v0.3 core is complete.
- [ ] Record architectural decision and first milestone in `docs/decisions/0001-architecture.md`.

**Exit:** Version and limitations documented, CLI choices validated, no paid requests, tests can run offline.

## Gate 1 — Safe, offline MVP

- [ ] Define typed YAML schemas: deployment registry, model identity, feature flags, profile workload, budgets, AA mapping.
- [ ] Seed original six Token Factory rows as unverified configs; add a second provider sample via config only.
- [ ] Implement immutable run manifest, sanitized trial schema and error taxonomy; add JSONL serialization and deterministic fixture hashing.
- [ ] Implement CLI `registry validate`, `run --dry-run`, `probe --dry-run`, `report --run` for offline fixture data. Dry-run must generate *zero* inference traffic.
- [ ] Write offline conformance-response fixtures and parser/metric unit tests.
- [ ] Add CLI smoke in CI/test pipeline without secrets or network.

**Exit:** Run manifest can be produced and validated offline, malformed configs fail loudly, no hard-coded Token Factory behavior.

## Gate 2 — AIPerf integration and minimal API probes

- [ ] Implement a pinned-version AIPerf wrapper in the Python 3.13 environment, including CLI builder, process exit/timeout capture, stdout/stderr sanitization, and results parser. Preserve raw AIPerf artifacts. Keep this runner separate from the Python 3.14 legacy venv.
- [ ] Treat the existing `run_guidellm.sh` as unsafe reference material because it exposes the API key in argv. GuideLLM may remain optional only after environment-only credential handling is verified.
- [ ] Verify *actual* AIPerf trace behavior (recorded request replay vs live follow-up) and document in report metadata.
- [ ] Implement standalone OpenAI-compatible direct API conformance probes: streaming, valid termination, usage, tool-call message + streamed argument assembly, JSON/schema output, long observation, controlled reasoning configurations; add alternate provider adapters only when necessary.
- [ ] Support no-credentials/missing permissions, 429/5xx/timeouts, errors before/after first token, interruption and cost-unknown states.
- [ ] Run offline mocked integration tests. For **live one-target smoke**, first show manifest/cost cap and request explicit authorization (do not silently proceed).

**Exit:** Demonstrable end-to-end raw→normalized result on an offline fixture; live smoke only after authorization.

## Gate 3 — Performance profiles and analysis

- [ ] Define immutable smoke/quick/standard profiles and realistic traffic fixtures (coding repository, test logs, research documents, structured file workflows, tools), with synthetic/approved trace provenance.
- [ ] Implement randomized/interleaved run order; record client host, region, time window, offered load and concurrency.
- [ ] Implement 1K/10K/32K/100K profiles, C=1/2/4/8 bounded sweeps, warm-prefix/growing-history/interleaved-session cache scenarios.
- [ ] Distinguish latency to first streamed byte/token, first answer token, and end-to-end; preserve denominators and actual token counts. Provide confidence/sample counts; do not publish unjustified P99.
- [ ] Generate report: measured endpoints, workload shapes, supported/unknown features, error distributions, limit onset, cache reported vs inferred benefit, provider-independent versus cross-model comparisons.

**Exit:** Repeatable standard profile; new endpoint added without modifying benchmark core; no intelligence scoring.

## Gate 4 — Long-context and reliability qualification (OPTIONAL; separately authorized)

- [ ] Implement context/tokenizer/output-headroom preflight with explicit token count and budget estimates; prove request compaction state is known.
- [ ] Step 128K→256K→512K→768K→safe ~1M only if supported; mark rejected, truncated, budget-skipped, accepted-but-not-verified separately.
- [ ] Test multi-turn growth, long synthetic tool logs, stable prefix/cache retention, idle-first differences (avoid claiming genuine server cold-start).
- [ ] Run bounded multi-window reliability and capacity sweeps; report observed limits from the tested account/region/time, not universal provider SLA.

**Exit:** Conservative effective context/capacity findings, explicit failure taxonomy and cost evidence, all large-load operations pre-authorized.

## Gate 5 — AA model scores: fetch and preserve locally

- [ ] Implement parser, validator, mapping, and storage tests against mocked Free/Pro fixtures first. Do not perform live AA sync until the user confirms the access tier and applicable data-use rights.
- [ ] Implement `aa sync --tier auto` with key supplied via `AA_API_KEY`, Free/Pro support, pagination, timeouts/backoff, 401/403/429 distinction, quota header preservation and no retry storms.
- [ ] In Free mode, ingest available indices; preserve `artificial_analysis_coding_index` and `artificial_analysis_agentic_index` as `null` if provided. Do not imply individual benchmark scores are available on Free.
- [ ] In Pro mode, store **published per-benchmark evaluations** as-is, notably `aa_briefcase_elo`, `automationbench_aa`, `frontiercode_v2_0`, `terminalbench_v4_0`, `gdp_pdf` when present; never fill absent scores.
- [ ] Save immutable raw pages, normalized `models.jsonl`, manifest with AA index version and access tier, attribution/source/version and cryptographic hashes. Put `latest.json` pointer only after successful full validation.
- [ ] Add tracked-model mapping file with explicit AA ID/slug/revision/reasoning-tier confidence; do not silently match alias or model family. Include `unmatched/not_covered/not_entitled/not_measured` states.
- [ ] Write unit tests against mocked paginated Free and Pro API responses, variant conflict, null scores, drift and index-version changes; never require network in CI.
- [ ] Ensure AA raw records are gitignored by default and not included in public exports without the relevant rights. Add `docs/AA_DATA.md` with attribution and license constraints.

**Exit:** Authorized users can refresh local AA reference snapshots without touching benchmark trial data. Results remain independent; no combined leaderboard or workload-mapping logic.

## Explicit deferral — later project, NOT v0.3

- [ ] **Do not implement now:** joining AA scores and performance into weighted ranking, workload matching, auto-routing, cloud selection, intelligence conclusions, or public leaderboards based on restricted AA data.
- [ ] Future design may separately consume AA snapshots, model identity mappings, and qualified endpoint result datasets after evidence standards and data rights are defined.

## Commands to make available (proposed user-facing interface)

```bash
bench registry validate
bench aa sync --tier auto --scope tracked
bench aa validate --snapshot external/aa/snapshots/<stamp> --offline
bench run --target <deployment_key> --profile quick --dry-run
bench run --target <deployment_key> --profile quick --execute --max-usd 5 --max-requests 100
bench report --run <run_id>
bench compare --baseline <run_id> --candidate <run_id>
```

The `$5` / `100` values above are **syntax examples, not permission to spend**. Reject `--execute` without an explicitly selected target, validated forecast, cost/request ceilings, and safe default concurrency. Run AA sync separately from paid inference.

## Useful source references

- AIPerf: <https://github.com/ai-dynamo/aiperf>
- AIPerf benchmark datasets: <https://github.com/ai-dynamo/aiperf/blob/main/docs/benchmark-datasets.md>
- AIPerf agentic-code generator: <https://github.com/ai-dynamo/aiperf/blob/main/docs/tutorials/agentic-code-generator.md>
- AA data API: <https://artificialanalysis.ai/data-api/docs>
- AA endpoint definitions: <https://artificialanalysis.ai/data-api/docs/reference>
- AA intelligence methodology: <https://artificialanalysis.ai/methodology/intelligence-benchmarking>
