# Same-day session example

This example runs the default same-day session workload. Published measurements are in [the results directory](../../docs/results/README.md).

The default program is `session-dynamics-sameday-v1`: **30 cells / at most 558 model calls**, one foreground invocation, no calendar anchor, not-before window, background monitor or scheduled wait.

```bash
# 1. Offline preflight: generates plan.json, manifest.json and PREFLIGHT.md.
#    Zero requests; no credentials; safe in a fresh clone.
endpoint-bench session same-day --out /tmp/sameday

# 2. Inspect the frozen matrix and the finite guards.
python - <<'PY'
import json
plan = json.load(open("/tmp/sameday/plan.json"))
print(plan["totals"])
print(plan["run_mode"])
print(plan["limits"]["cell_process_deadline_s"], plan["limits"]["max_foreground_invocation_seconds"])
PY

# 3. One live foreground run against the approved target binding.
#    Non-loopback execution needs an explicit authorization flag and a dollar guard.
NEBIUS_API_KEY=... endpoint-bench session same-day \
  --execute --authorize-live --max-usd 17 --out /tmp/sameday

# 4. Context-only finite selection: 3 cells / at most 18 calls.
endpoint-bench session same-day --select context \
  --execute --authorize-live --max-usd 1 --out /tmp/sameday-context
```

The run directory contains `plan.json`, `manifest.json`, `PREFLIGHT.md`, `program-state.json` (the canonical no-replay journal), `cells/`, `manifest.released.json`, `release-record.json`, `analysis.json`, `analysis.md`, `run-summary.json` and `REPORT.md`.

A local end-to-end check against the mock provider is available in the regression suite:

```bash
uv run pytest tests/test_session_same_day.py -q
```

A same-day run is one finite observation. It does not establish day-to-day stability, an SLO, capacity, endpoint qualification or a population tail. C8, sustained/offered-rate, inputs above 131,072, higher output caps, other deployments and AA remain optional future scopes.
