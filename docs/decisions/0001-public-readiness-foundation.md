# Public-readiness foundation

**Date:** 2026-11-01 ;  **Status:** Accepted for v0.5.0 specification.

> **Partially superseded 2026-11-01.** Items 4 and 5 of the decision below described `--execute` as failing closed because live execution was unimplemented. Live execution is now implemented and still fail-closed, for the reasons recorded in [`0002-architecture-and-aiperf-verification.md`](0002-architecture-and-aiperf-verification.md). The original text is preserved as the historical record of this decision.

## Decision

Implement the smallest credible public foundation before adding benchmark traffic:

1. a Python 3.13 package with a provider-neutral registry;
2. an offline dry-run manifest that proves configuration resolution without network access;
3. explicit schema/version/provenance fields;
4. fail-closed `--execute` while live execution is unimplemented;
5. tests, CI, and public-facing documentation that distinguish specification from implementation.

## Rationale

The repository previously contained specifications but no code, tests, CI, license, examples, or reproducible installation path. A public quickstart would have been misleading. A narrow offline path demonstrates packaging and evidence handling without pretending the benchmark exists.

## Consequences

- The README must state that no endpoint results exist.
- `--execute` must fail until live orchestration is implemented and authorized.
- Dry-run cost is `null`, with a reason, when pricing is unverified.
- AIPerf remains a planned dependency until its pinned behavior is integrated and tested.
- Historical `agent_setups/tf-benchmark` evidence is external and optional; a clean clone does not depend on it.
