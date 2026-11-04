# Session-dynamics follow-on scope (design only)

**Version:** 1.1
**Datestamp:** 2026-11-03
**Status:** Separate later design. **Not part of the same-day default (30 cells / 558 calls), not part of the legacy 54-cell program's 1,038-attempt matrix, and not released.**
**Source:** D0003 §3 and §4; the C=1/2/4 implementation in this repository; D0010 optional-scope note.
**Update rule:** Revise as a new version after a finite plan is approved; never fold these cells into either finite program or call the subset complete. These scopes are optional and are not a default done bar.

## Why this is separate

The D0003 program measures dependent sessions at C=1/2/4, with one active cell and at most four in-flight calls. It does not measure C=8, offered-rate/sustained capacity, or inputs above 131,072. Those change the workload identity and the operational risk, so they require their own finite preflight and authorization.

## Proposed later stages (not authorized)

| Stage | Purpose | Finite sketch | Main evidence |
|---|---|---|---|
| A. C8 screening | Does an eight-session cell complete without protocol/tool breakage? | 2 scenarios (`growing_transcript`, `tool_roundtrip`) × C=8 × 5 sessions × 4 calls = 40 attempts | achieved C8 concurrency, per-turn timing, session/tool status, error and censoring denominators |
| B. Offered-rate | Does the endpoint sustain a requested arrival rate without unbounded queueing? | 4 rates (0.25, 0.5, 1, 2 req/s) × C=8 × 20 calls = 80 attempts | requested vs achieved rate, queue time, active latency, deadline completions, error/censoring counts |
| C. Sustained | Does behavior drift over a bounded window? | 1 scenario × C=4 × 0.2 req/s × 10 min ≈ 120 attempts per cell, 2 cells = 240 attempts | windowed distributions, drift indicators, endpoint errors, censoring |
| D. Larger context (optional) | Separate API acceptance from demonstrated effective context above 131,072 | 2 sizes (262,144; 524,288) × 3 positions × 2 replicates = 12 attempts, C=1 | accepted/unverified/refused/truncated classification, exact sentinel checks |

The sketches sum to roughly 372 attempts before replication choices, but they are **illustrative**, not an approved matrix. Any actual plan must freeze exact cells, attempt caps, C, rates, timeouts, output caps, context sizes, seeds, order and exclusions.

## Guards to carry forward

- One active cell; concurrency and in-flight caps explicit; one outstanding call per session.
- No retries, replacement trials, hidden warmups or automatic size/rate escalation.
- Input bounds enforced before send; session marked ineligible rather than trimmed.
- Censoring is a measured outcome; lower bounds and every denominator are preserved.
- C=8 and sustained load require explicit authorization beyond the current program.
- Inputs above 131,072 require explicit authorization; ~1M is not approved and must not be attempted automatically.
- No endpoint qualification, SLO, capacity guarantee or universal ceiling-tail conclusion.

## Remaining scope statement

Completing session-dynamics-v1 does not complete the endpoint benchmark. The canonical remaining scope includes C8/offered-rate/sustained capacity, optional larger context, cache-benefit attribution with a clean-cache baseline, qualification thresholds ratified by an operator, AA synchronization, and reviewed public source release after evidence acceptance. None is authorized by D0003.
