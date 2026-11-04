# Changelog

## [0.1.3]: 2026-11-04

### Added

- `session same-day`: one foreground workflow from preflight through report, with a 30-cell / 558-call maximum and no scheduled waits.
- Dependent conversation, prefix-change, reconnect, compaction, idle/resume and deterministic tool scenarios at concurrency 1, 2 and 4.
- Context sentinels at three client construction sizes, with provider token counts and exact retrieval checks reported separately.
- [Session/context results](docs/results/2026-11-session-dynamics/README.md): 648 published per-call observations and 18 context checks, with export hashes and measurement limits.
- Bounded latency analysis, duplicate-evidence checks and a synthetic qualification evaluator. Real thresholds remain unset.

### Fixed

- Absolute request/cell deadlines under incomplete headers and byte-dribbling streams; journal-to-send expiry checks preserve partial evidence.
- Session connection reuse and explicit reconnect behavior, checked at the receiving server.
- Resolver isolation and cleanup: a fresh interpreter replaces raw threaded fork; the helper is included in runtime hashes.
- Prompt-prefix isolation, full-request context sizing, interrupted-run preservation and canonical journal locking.
- Counter/cohort validation and ratification scope/expiry checks in the synthetic evaluator.
- Offline tests work from a fresh clone without private run directories.

### Results

The session and context observations returned 648 HTTP 200 responses with no output truncations. All 18 context sentinels passed. The largest request contained 98,410 provider-reported input tokens against a 131,072-token client estimate; size conformance remained outside tolerance. Tool sessions left 30 planned call slots unused after natural termination. These observations support descriptive reporting, with no qualification or maximum-context claim.

Earlier pilots and corrected screening results remain under [the results index](docs/results/README.md). Full local prompts, responses and machine-specific run state are excluded from the public export.

## [0.1.0]: 2026-11-02

Benchmark MVP. The plan's MVP criteria are met: functional AIPerf integration, endpoint performance measurement, streaming and tool-call conformance probes, repeatable benchmark profiles, normalized metrics and evidence, working reports, and reproducible authorized live experiments.

This is not a qualification milestone. No endpoint has been qualified, no threshold is calibrated, and every measurement so far is single-window screening.

See [Unreleased] above for the change list.

## [0.0.1]: Unreleased pre-alpha

### Added

- Python 3.13 package and `endpoint-bench` CLI entry point.
- Offline provider registry validation and network-free dry-run manifest generation.
- Initial methodology and result-schema contracts.
- Contribution, security, development, licensing-decision, and changelog documentation.
- Offline tests, synthetic configuration/example, and GitHub Actions CI.
