#!/usr/bin/env bash
# Full local verification, equivalent to the CI jobs that can run without a push.
# Usage: ./scripts/verify.sh [--with-engine]
set -euo pipefail

cd "$(dirname "$0")/.."
WITH_ENGINE=0
[[ "${1:-}" == "--with-engine" ]] && WITH_ENGINE=1

# Scratch directories are cleared so the script is re-runnable. Run directories
# are immutable by design, so a leftover directory is a hard error otherwise.
rm -rf /tmp/verify-probe-stream /tmp/verify-probe-tool

echo "==> sync (locked)"
uv sync --locked

echo "==> lint"
uv run ruff check .

echo "==> unit and integration tests"
uv run pytest -q

echo "==> registry validation"
uv run endpoint-bench registry validate

echo "==> dry run performs no network requests"
uv run endpoint-bench run --target offline-mock-smoke --profile smoke --dry-run \
  > /tmp/endpoint-bench-dry-run.json
uv run python -c 'import json; m=json.load(open("/tmp/endpoint-bench-dry-run.json")); assert m["inference"]["network_requests"] == 0'

echo "==> offline workflow: mock provider plus both probe scenarios"
uv run endpoint-bench mock --port 18080 > /tmp/endpoint-bench-mock.log 2>&1 &
MOCK_PID=$!
trap 'kill $MOCK_PID 2>/dev/null || true' EXIT
for _ in $(seq 1 40); do
  curl -sf http://127.0.0.1:18080/v1/models > /dev/null && break
  sleep 0.5
done
curl -sf http://127.0.0.1:18080/v1/models > /dev/null || { echo "mock did not start"; cat /tmp/endpoint-bench-mock.log; exit 1; }

uv run endpoint-bench probe \
  --config examples/local-benchmark/registry.json \
  --target local-mock-agent-replay --profile smoke --requests 2 \
  --run-dir /tmp/verify-probe-stream > /tmp/verify-probe-stream.json
uv run python -c 'import json; m=json.load(open("/tmp/verify-probe-stream.json")); c=m["counters"]; assert c["successful_trials"] == 2, c'

uv run endpoint-bench probe \
  --config examples/local-benchmark/registry.json \
  --target local-mock-agent-replay --profile smoke \
  --scenario tool-round-trip --requests 1 \
  --run-dir /tmp/verify-probe-tool > /tmp/verify-probe-tool.json
uv run python -c 'import json; m=json.load(open("/tmp/verify-probe-tool.json")); c=m["counters"]; assert c["successful_trials"] == 1 and c["http_attempts"] == 2, c'

echo "==> committed trace is valid"
uv run python -c '
import json, pathlib
rows = [json.loads(l) for l in pathlib.Path("examples/local-benchmark/agent-trace.jsonl").read_text().splitlines() if l.strip()]
assert len(rows) == 12
assert len({r["session_id"] for r in rows}) == 4
'

if [[ "$WITH_ENGINE" == "1" ]]; then
  echo "==> engine-dependent suites (requires .venv-aiperf with aiperf==0.13.0)"
  uv run pytest tests/test_aiperf_integration.py tests/test_process_lifecycle.py -q
fi

echo
echo "ALL LOCAL CHECKS PASSED"
