# Session-dynamics-v1 results

Generated: 2026-11-04T05:58:49.976768Z

**Descriptive finite-sample evidence.** No threshold is ratified, no endpoint is qualified, and no population or universal ceiling-tail claim is made.

## Scenario x concurrency

| Scenario | C | planned | attempts | natural | truncated | failed | skipped | visible p50 (ms) | active natural p50 (ms) | session p50 (ms) | achieved calls |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| client_reconnect | 1 | 20 | 20 | 20 | 0 | 0 | 0 | 1634.9 | 1855.5 | 7988.5 | 1 |
| client_reconnect | 2 | 20 | 20 | 20 | 0 | 0 | 0 | 1786.3 | 2070.1 | 9302.6 | 2 |
| client_reconnect | 4 | 20 | 20 | 20 | 0 | 0 | 0 | 1633.7 | 1951.0 | 8224.9 | 4 |
| explicit_compaction | 1 | 20 | 20 | 20 | 0 | 0 | 0 | 1420.9 | 1933.4 | 8837.3 | 1 |
| explicit_compaction | 2 | 20 | 20 | 20 | 0 | 0 | 0 | 1326.9 | 1597.8 | 7357.3 | 2 |
| explicit_compaction | 4 | 20 | 20 | 20 | 0 | 0 | 0 | 1493.1 | 1813.2 | 7954.7 | 4 |
| growing_transcript | 1 | 40 | 40 | 40 | 0 | 0 | 0 | 2460.6 | 2832.7 | 13455.6 | 1 |
| growing_transcript | 2 | 40 | 40 | 40 | 0 | 0 | 0 | 2458.5 | 2949.9 | 14288.8 | 2 |
| growing_transcript | 4 | 40 | 40 | 40 | 0 | 0 | 0 | 2750.2 | 3066.6 | 16114.1 | 4 |
| idle_resume | 1 | 20 | 20 | 20 | 0 | 0 | 0 | 1430.1 | 1643.2 | 67859.6 | 1 |
| idle_resume | 2 | 20 | 20 | 20 | 0 | 0 | 0 | 1364.0 | 1625.0 | 67434.7 | 2 |
| idle_resume | 4 | 20 | 20 | 20 | 0 | 0 | 0 | 1703.6 | 1850.3 | 68130.3 | 4 |
| independent | 1 | 20 | 20 | 20 | 0 | 0 | 0 | 2191.8 | 2410.3 | 9542.8 | 1 |
| independent | 2 | 20 | 20 | 20 | 0 | 0 | 0 | 2070.0 | 2207.3 | 8776.4 | 2 |
| independent | 4 | 20 | 20 | 20 | 0 | 0 | 0 | 1844.6 | 2028.6 | 7870.1 | 4 |
| interleaved_sessions | 1 | 40 | 40 | 40 | 0 | 0 | 0 | 2558.4 | 3042.4 | 12748.1 | 1 |
| interleaved_sessions | 2 | 40 | 40 | 40 | 0 | 0 | 0 | 2323.5 | 2791.3 | 14526.9 | 2 |
| interleaved_sessions | 4 | 40 | 40 | 40 | 0 | 0 | 0 | 2478.3 | 3189.2 | 12036.9 | 4 |
| prefix_invalidation | 1 | 20 | 20 | 20 | 0 | 0 | 0 | 2978.6 | 3233.3 | 13476.2 | 1 |
| prefix_invalidation | 2 | 20 | 20 | 20 | 0 | 0 | 0 | 1693.3 | 1777.3 | 8608.5 | 2 |
| prefix_invalidation | 4 | 20 | 20 | 20 | 0 | 0 | 0 | 2402.5 | 2550.1 | 11199.8 | 4 |
| stable_prefix | 1 | 20 | 20 | 20 | 0 | 0 | 0 | 2318.7 | 2445.9 | 10820.1 | 1 |
| stable_prefix | 2 | 20 | 20 | 20 | 0 | 0 | 0 | 2489.7 | 2722.7 | 11896.4 | 2 |
| stable_prefix | 4 | 20 | 20 | 20 | 0 | 0 | 0 | 1942.7 | 2120.0 | 9313.1 | 4 |
| tool_roundtrip | 1 | 20 | 10 | 10 | 0 | 0 | 0 | 887.8 | 1411.5 | 2502.0 | 1 |
| tool_roundtrip | 2 | 20 | 10 | 10 | 0 | 0 | 0 | 944.3 | 1235.1 | 2267.4 | 2 |
| tool_roundtrip | 4 | 20 | 10 | 10 | 0 | 0 | 0 | 776.7 | 1353.3 | 2671.4 | 4 |

## Window distributions

Each window is reported separately; pooling across windows is only a compatible descriptive summary, not a reliability claim.

| Cell | n visible | visible p50 | visible p95 | active natural p50 | censored |
| --- | ---: | ---: | ---: | ---: | ---: |
| client_reconnect:C1:WNone | 20 | 1634.9 | 2615.6 | 1855.5 | 0 |
| client_reconnect:C2:WNone | 20 | 1786.3 | 3900.0 | 2070.1 | 0 |
| client_reconnect:C4:WNone | 20 | 1633.7 | 3527.6 | 1951.0 | 0 |
| explicit_compaction:C1:WNone | 20 | 1420.9 | 3416.2 | 1933.4 | 0 |
| explicit_compaction:C2:WNone | 20 | 1326.9 | 2529.1 | 1597.8 | 0 |
| explicit_compaction:C4:WNone | 20 | 1493.1 | 2610.3 | 1813.2 | 0 |
| growing_transcript:C1:W1 | 20 | 4190.4 | 8058.6 | 4956.2 | 0 |
| growing_transcript:C1:WNone | 20 | 1727.2 | 3523.1 | 1992.2 | 0 |
| growing_transcript:C2:W1 | 20 | 3450.9 | 8085.9 | 4190.2 | 0 |
| growing_transcript:C2:WNone | 20 | 1334.6 | 3615.1 | 1568.3 | 0 |
| growing_transcript:C4:W1 | 20 | 4704.2 | 13097.9 | 5993.0 | 0 |
| growing_transcript:C4:WNone | 20 | 1737.3 | 3332.4 | 1896.7 | 0 |
| idle_resume:C1:WNone | 20 | 1430.1 | 2947.9 | 1643.2 | 0 |
| idle_resume:C2:WNone | 20 | 1364.0 | 3274.7 | 1625.0 | 0 |
| idle_resume:C4:WNone | 20 | 1703.6 | 3027.6 | 1850.3 | 0 |
| independent:C1:WNone | 20 | 2191.8 | 3311.9 | 2410.3 | 0 |
| independent:C2:WNone | 20 | 2070.0 | 2669.4 | 2207.3 | 0 |
| independent:C4:WNone | 20 | 1844.6 | 4072.9 | 2028.6 | 0 |
| interleaved_sessions:C1:W1 | 20 | 3998.8 | 8096.7 | 4741.3 | 0 |
| interleaved_sessions:C1:WNone | 20 | 1508.1 | 3350.8 | 1912.7 | 0 |
| interleaved_sessions:C2:W1 | 20 | 4019.2 | 7771.4 | 4479.7 | 0 |
| interleaved_sessions:C2:WNone | 20 | 1411.5 | 2668.8 | 1820.8 | 0 |
| interleaved_sessions:C4:W1 | 20 | 3716.8 | 7891.1 | 4633.5 | 0 |
| interleaved_sessions:C4:WNone | 20 | 1407.7 | 3357.7 | 1783.0 | 0 |
| prefix_invalidation:C1:WNone | 20 | 2978.6 | 5801.4 | 3233.3 | 0 |
| prefix_invalidation:C2:WNone | 20 | 1693.3 | 3947.5 | 1777.3 | 0 |
| prefix_invalidation:C4:WNone | 20 | 2402.5 | 3582.1 | 2550.1 | 0 |
| stable_prefix:C1:WNone | 20 | 2318.7 | 3799.3 | 2445.9 | 0 |
| stable_prefix:C2:WNone | 20 | 2489.7 | 4838.7 | 2722.7 | 0 |
| stable_prefix:C4:WNone | 20 | 1942.7 | 3685.3 | 2120.0 | 0 |
| tool_roundtrip:C1:WNone | 7 | 887.8 | 1991.7 | 1411.5 | 0 |
| tool_roundtrip:C2:WNone | 6 | 944.3 | 1287.3 | 1235.1 | 0 |
| tool_roundtrip:C4:WNone | 5 | 776.7 | 1063.1 | 1353.3 | 0 |

## Context sentinels

| Requested input | checks | API accepted | demonstrated effective | refused | output truncated | malformed | unverified |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |

## Denominators and exclusions

- Failed, truncated, skipped and ineligible calls stay in the table; none is silently omitted or replaced.
- A truncated call's active time is a lower bound, not a completion time.
- Cache telemetry is provider-reported only; timing-inferred benefit is not computed and unknown state is not zero.
- API-accepted context is not demonstrated effective context.

## Limits

Twenty calls per cell and five sessions per scenario are small samples. Window-level and pooled percentiles are descriptive, not population tail estimates.
