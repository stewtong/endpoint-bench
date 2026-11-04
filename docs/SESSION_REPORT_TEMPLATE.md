# Session-dynamics report template

**Version:** 1.1
**Datestamp:** 2026-11-03
**Use:** Fill from `analysis.json` only. Do not add a threshold, ranking, qualification verdict or population claim that the analysis does not contain. The same-day command writes its own `REPORT.md` from `run-summary.json` and `analysis.json`; this Markdown template documents that shape and the legacy 54-cell variant.

## Same-day header (D0010 default)

- Program: `session-dynamics-sameday-v1` / directive D0010
- Target (deployment, model, revision, region, client): `<from manifest>`
- Invocation status: `COMPLETED` / `PARTIAL` / `CANCELLED`
- Cells selected / executed / already completed / skipped: `<n>` / `<n>` / `<n>` / `<n>`
- Inference attempts: `<n>` of 558 maximum for the full matrix; `<n>` of 18 for a context-only selection
- Cell deadline / invocation deadline: 600 s / 6 h
- Plan SHA-256 / manifest SHA-256 / released manifest SHA-256: `<hash>` / `<hash>` / `<hash>`

A same-day report must state that it is one finite observation, that no calendar spacing was used, and that it does not establish day-to-day stability.

## Header

- Program: `session-dynamics-v1` / directive D0003
- Target (deployment, model, revision, region, client): `<from manifest>`
- Evidence range: `<first cell UTC>` to `<last cell UTC>`
- Cells completed / planned: `<n>/54`
- Inference attempts: `<n>` of 1,038 maximum
- Manifest SHA-256: `<hash>`
- Plan SHA-256: `<hash>`
- Live release: `<operation IDs or "none">`

## Status statement (required)

> These are finite-sample descriptive observations from a declared synthetic workload. No threshold is ratified, no endpoint is qualified, and no population, capacity, SLO, or universal ceiling-tail claim is supported. Failed, truncated, skipped and ineligible calls remain in the denominators.

## Scenario × concurrency

| Scenario | C | Planned | Attempted | Natural | Truncated | Failed | Skipped/ineligible | Visible p50 (ms) | Visible p95 (ms) | Active natural p50 (ms) | Session p50 (ms) | Achieved calls |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| independent | 1 | | | | | | | | | | | |
| independent | 2 | | | | | | | | | | | |
| independent | 4 | | | | | | | | | | | |
| stable_prefix | 1 | | | | | | | | | | | |
| … | | | | | | | | | | | | |

## Window distributions

Report each window separately. Pooling across windows is a compatible descriptive summary, not a reliability claim.

| Cell | n visible | visible p50 | visible p95 | active natural p50 | censored | failed | skipped |
|---|---:|---:|---:|---:|---:|---:|---:|
| `growing_transcript:C1:W1` | | | | | | | |
| … | | | | | | | |

## Template split

| Scenario | Template | n visible | visible p50 | active natural p50 |
|---|---|---:|---:|---:|
| `growing_transcript` | coding | | | |
| `growing_transcript` | document | | | |
| `growing_transcript` | transcript | | | |

## Context sentinels

| Requested input | Checks | API accepted | Demonstrated effective | Refused | Output truncated | Malformed | Unverified |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 32,768 | | | | | | | |
| 65,536 | | | | | | | |
| 131,072 | | | | | | | |

> API acceptance is not demonstrated effective context. A failed exact sentinel check does not prove server-side truncation.

## Tokens and cache

- Provider input/output/reasoning sums and the number of calls reporting each.
- Cache-read/write sums and reporting counts; `unknown` when absent.
- Timing-inferred cache benefit: **not computed**. A clean-cache paired baseline is required.
- Token-normalized claims are withheld unless the sizing checks support them; do not back-fill a client estimate from provider usage.

## Completion and censoring

- Natural completions: `<n>`
- Ceiling truncations: `<n>` (`<fraction>`), active latency is a lower bound for these.
- Timeouts / rate limits / server errors / protocol failures: `<counts>`
- Sessions ineligible on the input bound: `<n>`
- Tool refusals/unsupported calls: `<n>` (observed outcomes, not substituted)
- No call was retried or replaced.

## What this report does not support

- A qualification verdict or a pass/fail SLO.
- A capacity limit, C8/sustained-rate conclusion, or a claim about inputs above 131,072.
- A cache benefit claim.
- A model-intelligence or answer-quality judgment.
- A universal statement that a fixed output cap can or cannot eliminate censoring.

## Linked evidence

- `manifest.json`, `plan.json`, `program-state.json`
- `cells/<operation_id>/cell.json`, `calls.jsonl`, `sessions.jsonl`
- `analysis.json`, `analysis.md`
- Historical inventory and interpretation corrections where relevant.
