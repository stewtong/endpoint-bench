# Historical run inventory

**Version:** 1.0
**Datestamp:** 2026-11-02
**Source:** `runs/`, `docs/gate3-*`, `docs/gate3-corrections*` artifacts; rebuilt by `scripts/build_historical_inventory.py`.
**Update rule:** Rebuild from original artifacts; never edit a recorded counter or promote a retrospective entry to current authority.

Generated: 2026-11-02T10:46:39.467206-08:00

Retrospective reconstruction from repository artifacts. **No entry below is a new D0003 operation, and no entry carries current live authorization.**

## Dedupe summary

- Directories scanned: 53
- Unique run identities: 21 (19 benchmark, 2 probe)
- Duplicate copies: 19
- Unique HTTP attempts: 831
- Derived correction reports without original trials: 13

## Runs

| Run | Kind | Profile | C | Attempts | Natural | Truncated | Timeout (s) | First attempt metadata (UTC) | Duration (s) | Authority |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | ---: | --- |
| probe-ab63b225145e | probe | - | - | 2 | 0 | - | - | 2026-11-02T03:42:14.683800Z | - | unresolved_retrospective |
| probe-dbf504af6924 | probe | - | - | 1 | 0 | - | - | 2026-11-02T03:42:14.741000Z | - | unresolved_retrospective |
| run-3c188a0e4ce6 | benchmark | pilot-10k-2048 | 1 | 20 | 13 | 7 | 60.0 | 2026-11-02T07:21:20.784340Z | 220.1 | unresolved_retrospective |
| run-6351f7471cea | benchmark | quick | 2 | 20 | 1 | 19 | 60.0 | 2026-11-02T07:29:43.768351Z | 35.0 | unresolved_retrospective |
| run-4443906cc72b | benchmark | standard | 4 | 200 | 40 | 160 | 60.0 | 2026-11-02T07:32:47.893781Z | 181.0 | unresolved_retrospective |
| run-5379290bed52 | benchmark | reliability | 2 | 300 | 0 | 300 | 60.0 | 2026-11-02T07:35:11.308003Z | 141.3 | unresolved_retrospective |
| run-dede1f4a445d | benchmark | long-context | 1 | 8 | 7 | 1 | 60.0 | 2026-11-02T07:35:48.570199Z | 34.7 | unresolved_retrospective |
| run-6cabf9e5ee70 | benchmark | pilot-10k-8192 | 1 | 20 | 20 | 0 | 60.0 | 2026-11-02T07:40:11.584918Z | 58.3 | unresolved_retrospective |
| run-6cabf9e5ee70-w1-c1 | benchmark | qualification-10k-8192 | 1 | 20 | 18 | 2 | 60.0 | 2026-11-02T07:42:25.600508Z | 87.5 | unresolved_retrospective |
| run-9edf4133537c | benchmark | pilot-10k-16384 | 1 | 20 | 19 | 1 | 60.0 | 2026-11-02T07:52:20.286383Z | 94.7 | unresolved_retrospective |
| run-2575dfce27f4-w1-c2 | benchmark | qualification-10k-8192 | 2 | 20 | 20 | 0 | 60.0 | 2026-11-02T07:58:23.157036Z | 69.9 | unresolved_retrospective |
| run-2575dfce27f4-w2-c2 | benchmark | qualification-10k-8192 | 2 | 20 | 20 | 0 | 60.0 | 2026-11-02T08:07:59.889258Z | 46.6 | unresolved_retrospective |
| run-6cabf9e5ee70-w2-c1 | benchmark | qualification-10k-8192 | 1 | 20 | 20 | 0 | 60.0 | 2026-11-02T08:09:40.872786Z | 100.9 | unresolved_retrospective |
| run-6cabf9e5ee70-w3-c1 | benchmark | qualification-10k-8192 | 1 | 20 | 19 | 1 | 60.0 | 2026-11-02T08:18:43.609668Z | 90.3 | unresolved_retrospective |
| run-2575dfce27f4-w3-c2 | benchmark | qualification-10k-8192 | 2 | 20 | 20 | 0 | 60.0 | 2026-11-02T08:19:38.236455Z | 54.5 | unresolved_retrospective |
| run-2575dfce27f4-w4-c2 | benchmark | qualification-10k-8192 | 2 | 20 | 18 | 2 | 60.0 | 2026-11-02T08:28:36.030692Z | 82.7 | unresolved_retrospective |
| run-6cabf9e5ee70-w4-c1 | benchmark | qualification-10k-8192 | 1 | 20 | 19 | 1 | 60.0 | 2026-11-02T08:30:41.195028Z | 125.1 | unresolved_retrospective |
| run-6cabf9e5ee70-w5-c1 | benchmark | qualification-10k-8192 | 1 | 20 | 19 | 1 | 60.0 | 2026-11-02T08:39:28.819397Z | 135.5 | unresolved_retrospective |
| run-2575dfce27f4-w5-c2 | benchmark | qualification-10k-8192 | 2 | 20 | 20 | 0 | 60.0 | 2026-11-02T08:40:13.356030Z | 44.5 | unresolved_retrospective |
| run-2575dfce27f4-w6-c2 | benchmark | qualification-10k-8192 | 2 | 20 | 20 | 0 | 60.0 | 2026-11-02T08:47:59.703086Z | 46.4 | unresolved_retrospective |
| run-6cabf9e5ee70-w6-c1 | benchmark | qualification-10k-8192 | 1 | 20 | 20 | 0 | 60.0 | 2026-11-02T08:49:22.502485Z | 82.7 | unresolved_retrospective |

## Duplicate groups

Documentation copies are the same inference attempts, not new evidence.

- canonical `runs/run-6cabf9e5ee70-w5-c1`; copies: `docs/gate3-multiwindow-8192-bounded/run-6cabf9e5ee70-w5-c1`
- canonical `runs/run-5379290bed52`; copies: `docs/gate3-standard-profiles/run-5379290bed52`
- canonical `runs/run-2575dfce27f4-w3-c2`; copies: `docs/gate3-multiwindow-8192-bounded/run-2575dfce27f4-w3-c2`
- canonical `runs/run-6cabf9e5ee70-w3-c1`; copies: `docs/gate3-multiwindow-8192-bounded/run-6cabf9e5ee70-w3-c1`
- canonical `runs/run-6cabf9e5ee70-w6-c1`; copies: `docs/gate3-multiwindow-8192-bounded/run-6cabf9e5ee70-w6-c1`
- canonical `runs/run-2575dfce27f4-w1-c2`; copies: `docs/gate3-multiwindow-8192-bounded/run-2575dfce27f4-w1-c2`
- canonical `runs/run-6cabf9e5ee70-w2-c1`; copies: `docs/gate3-multiwindow-8192-bounded/run-6cabf9e5ee70-w2-c1`
- canonical `runs/run-9edf4133537c`; copies: `docs/gate3-pilot-16384/run-9edf4133537c`
- canonical `runs/run-dede1f4a445d`; copies: `docs/gate3-standard-profiles/run-dede1f4a445d`
- canonical `runs/run-3c188a0e4ce6`; copies: `docs/gate3-pilot/run-3c188a0e4ce6`
- canonical `runs/run-2575dfce27f4-w6-c2`; copies: `docs/gate3-multiwindow-8192-bounded/run-2575dfce27f4-w6-c2`
- canonical `runs/run-6351f7471cea`; copies: `docs/gate3-standard-profiles/run-6351f7471cea`
- canonical `runs/run-2575dfce27f4-w5-c2`; copies: `docs/gate3-multiwindow-8192-bounded/run-2575dfce27f4-w5-c2`
- canonical `runs/run-2575dfce27f4-w4-c2`; copies: `docs/gate3-multiwindow-8192-bounded/run-2575dfce27f4-w4-c2`
- canonical `runs/run-6cabf9e5ee70-w4-c1`; copies: `docs/gate3-multiwindow-8192-bounded/run-6cabf9e5ee70-w4-c1`
- canonical `runs/run-6cabf9e5ee70-w1-c1`; copies: `docs/gate3-multiwindow-8192/run-6cabf9e5ee70-w1-c1`, `docs/gate3-multiwindow-8192-bounded/run-6cabf9e5ee70-w1-c1`
- canonical `runs/run-4443906cc72b`; copies: `docs/gate3-standard-profiles/run-4443906cc72b`
- canonical `runs/run-2575dfce27f4-w2-c2`; copies: `docs/gate3-multiwindow-8192-bounded/run-2575dfce27f4-w2-c2`

## Interpretation corrections

- The bounded plan declares a 240 s request timeout, but every historical report and generated AIPerf configuration observed here records 60 s. The plan value is a declared intent; the runtime value is the evidence.
- Window 1 C1 (07:41 to 07:42 UTC) is the reused first-window chronology; the C2 pair began 07:57 UTC. Later windows use ten-minute offsets from that C2 start. This is exploratory reused evidence, not six uniformly paired fresh windows under a prospectively frozen plan.
- The 8192 fixed cap did not eliminate censoring. It is not evidence that no higher cap could ever do so; no universal ceiling-tail claim is supported.

Review 0006 found the 12 bounded 8192 cells do not match the coordinating chat's 2048/$0.20 bounded approval. Existing artifacts are preserved and counted, but no current authorization is inferred from them. D0003 releases no live operation.
