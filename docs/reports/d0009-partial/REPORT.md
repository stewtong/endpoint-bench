# D0009 partial historical report

**Status:** PARTIAL HISTORICAL RESULT. Future context is unmeasured.

Generated: 2026-11-03T21:59:27-08:00

- Program: `session-dynamics-v1` / directive D0009
- Target: `tf-deepseek-v4.1-flash-1017` / `deepseek-ai/DeepSeek-V4.1-Flash-1017` / region `us-north1` (operator-declared)
- Evidence range: 2026-11-03T19:41:19.429407Z to 2026-11-04T05:07:08.455090Z (9.43 h)
- Cells completed: 33 of 54 in the installed manifest
- Inference attempts: 630 of 1,038 maximum
- Screening: 27 cells / 510 attempts
- Temporal window 1 (W1): 6 cells / 120 attempts
- All 33 completed cells have status `completed`; zero endpoint failures and zero truncated calls.

## What was cancelled and what remains unmeasured

D0010 revoked D0009 W2, W3, W4 and the scheduled context continuation immediately. Those operations were never started:

- W2/W3/W4: 18 temporal cells / 360 planned calls. Cancelled, not completed, missed or provider failures.
- Context sentinels: 3 cells / 18 planned calls at 32,768, 65,536 and 131,072 requested input tokens. Never started.

**Future context and multi-window behaviour are unmeasured.** The empty context table in `analysis.md` is missing evidence, not a zero result. No day-to-day or multi-window stability claim is supported by W1 alone.

## Session observations (screening and W1 pooled where shown)

| Scenario | C | planned | attempts | natural | truncated | failed | visible p50 (ms) | active natural p50 (ms) | session p50 (ms) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| client_reconnect | 1 | 20 | 20 | 20 | 0 | 0 | 1634.9 | 1855.5 | 7988.5 |
| client_reconnect | 2 | 20 | 20 | 20 | 0 | 0 | 1786.3 | 2070.1 | 9302.6 |
| client_reconnect | 4 | 20 | 20 | 20 | 0 | 0 | 1633.7 | 1951.0 | 8224.9 |
| explicit_compaction | 1 | 20 | 20 | 20 | 0 | 0 | 1420.9 | 1933.4 | 8837.3 |
| explicit_compaction | 2 | 20 | 20 | 20 | 0 | 0 | 1326.9 | 1597.8 | 7357.3 |
| explicit_compaction | 4 | 20 | 20 | 20 | 0 | 0 | 1493.1 | 1813.2 | 7954.7 |
| growing_transcript | 1 | 40 | 40 | 40 | 0 | 0 | 2460.6 | 2832.7 | 13455.6 |
| growing_transcript | 2 | 40 | 40 | 40 | 0 | 0 | 2458.5 | 2949.9 | 14288.8 |
| growing_transcript | 4 | 40 | 40 | 40 | 0 | 0 | 2750.2 | 3066.6 | 16114.1 |
| idle_resume | 1 | 20 | 20 | 20 | 0 | 0 | 1430.1 | 1643.2 | 67859.6 |
| idle_resume | 2 | 20 | 20 | 20 | 0 | 0 | 1364.0 | 1625.0 | 67434.7 |
| idle_resume | 4 | 20 | 20 | 20 | 0 | 0 | 1703.6 | 1850.3 | 68130.3 |
| independent | 1 | 20 | 20 | 20 | 0 | 0 | 2191.8 | 2410.3 | 9542.8 |
| independent | 2 | 20 | 20 | 20 | 0 | 0 | 2070.0 | 2207.3 | 8776.4 |
| independent | 4 | 20 | 20 | 20 | 0 | 0 | 1844.6 | 2028.6 | 7870.1 |
| interleaved_sessions | 1 | 40 | 40 | 40 | 0 | 0 | 2558.4 | 3042.4 | 12748.1 |
| interleaved_sessions | 2 | 40 | 40 | 40 | 0 | 0 | 2323.5 | 2791.3 | 14526.9 |
| interleaved_sessions | 4 | 40 | 40 | 40 | 0 | 0 | 2478.3 | 3189.2 | 12036.9 |
| prefix_invalidation | 1 | 20 | 20 | 20 | 0 | 0 | 2978.6 | 3233.3 | 13476.2 |
| prefix_invalidation | 2 | 20 | 20 | 20 | 0 | 0 | 1693.3 | 1777.3 | 8608.5 |
| prefix_invalidation | 4 | 20 | 20 | 20 | 0 | 0 | 2402.5 | 2550.1 | 11199.8 |
| stable_prefix | 1 | 20 | 20 | 20 | 0 | 0 | 2318.7 | 2445.9 | 10820.1 |
| stable_prefix | 2 | 20 | 20 | 20 | 0 | 0 | 2489.7 | 2722.7 | 11896.4 |
| stable_prefix | 4 | 20 | 20 | 20 | 0 | 0 | 1942.7 | 2120.0 | 9313.1 |
| tool_roundtrip | 1 | 20 | 10 | 10 | 0 | 0 | 887.8 | 1411.5 | 2502.0 |
| tool_roundtrip | 2 | 20 | 10 | 10 | 0 | 0 | 944.3 | 1235.1 | 2267.4 |
| tool_roundtrip | 4 | 20 | 10 | 10 | 0 | 0 | 776.7 | 1353.3 | 2671.4 |

The pooled `growing_transcript` and `interleaved_sessions` rows each include the screening cell plus the W1 repeat (40 attempts). The window table in `analysis.md` separates them; W1 below is the temporal subset.

## Temporal window 1 observations (6 cells / 120 attempts)

| Cell | n visible | visible p50 | visible p95 | active natural p50 | censored |
| --- | ---: | ---: | ---: | ---: | ---: |
| growing_transcript:C1:W1 | 20 | 4190.4 | 8058.6 | 4956.2 | 0 |
| growing_transcript:C2:W1 | 20 | 3450.9 | 8085.9 | 4190.2 | 0 |
| growing_transcript:C4:W1 | 20 | 4704.2 | 13097.9 | 5993.0 | 0 |
| interleaved_sessions:C1:W1 | 20 | 3998.8 | 8096.7 | 4741.3 | 0 |
| interleaved_sessions:C2:W1 | 20 | 4019.2 | 7771.4 | 4479.7 | 0 |
| interleaved_sessions:C4:W1 | 20 | 3716.8 | 7891.1 | 4633.5 | 0 |

W1 is one window. It is not a repeat of W2-W4 and cannot stand in for them. The observed W1 visible-content p50 is higher than the matched screening cells; that is a descriptive single-window difference, not a time-of-day effect estimate.

## Tool round-trip natural termination

The three `tool_roundtrip` cells each planned 20 calls (five sessions x four turns) and made 10:

| Cell | attempts | planned | evidence_complete | session stop |
| --- | ---: | ---: | --- | --- |
| SD-tool_roundtrip-C1 | 10 | 20 | False | tool_call_absent |
| SD-tool_roundtrip-C2 | 10 | 20 | False | tool_call_absent |
| SD-tool_roundtrip-C4 | 10 | 20 | False | tool_call_absent |

The 30 unattempted slots are natural terminal behaviour: the endpoint returned no tool call on the second turn, so each session ended at `tool_call_absent` after 2 of 4 calls. They are a coverage limitation, not failures, and no replacement call is permitted. Their actual denominator is 10/20 per cell.

## Context sentinels

No cell evidence exists. All three sizes are unmeasured in this run. The installed manifest still declares them; the D0009 release was revoked before they started.

## Provenance

- Installed manifest file SHA-256: `509033c4b7364db7de8491973d8f71b77f650f8a88e045f869bd468699920058`
- Manifest content SHA-256: `aaa0e17dd5b1f9d23cf11281400a6cc38f0ab5d3fdb8b0d42c27b3a628685a35`
- Plan content SHA-256: `1363df677687e893acef88f99eaa3369845637608bcf07188388e8978f87342a`
- Execution identity SHA-256: `2cac879f06116a05ca80d16fd9c7153c0e6a432ea3d78c657fdb948d61522624`
- Journal file SHA-256: `7ec7dc3c3230128ddeacc8e1158d5cd34fa21fac1e052eeca50267a0161d1361`
- Journal: 33 completed operations; 630 attempt records; created 2026-11-03T19:41:19.428146Z
- Cell evidence: 33 `cell.json` files under `docs/session-dynamics-v1/cells/`
- Analysis: `docs/reports/d0009-partial/analysis.json` and `analysis.md`

The plan, released manifest, journal, calls, transcripts and raw evidence are unchanged by this report. The analysis was written to a separate directory.

## Claims and limits

- These are finite-sample descriptive observations from a declared synthetic workload. No threshold is ratified, no endpoint is qualified, and no population, capacity, SLO or universal ceiling-tail claim is supported.
- Failed, truncated, skipped and ineligible calls remain in the denominators; the 30 unattempted tool slots are coverage, not failures.
- The evidence is one screening pass plus one temporal window. It does not establish day-to-day stability.
- Future context at 32,768 / 65,536 / 131,072 input tokens is unmeasured. A separate immediate completion proposal covers those three cells; it is not executed evidence.
- Legacy pilot and screening artifacts are not pooled into these results.
