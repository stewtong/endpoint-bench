"""Public benchmark exports preserve measured counts, size limits and hashes."""
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'docs/results/2026-11-session-dynamics'


def test_public_measurements_and_context_limits():
    rows = [json.loads(line) for line in (ROOT / 'observations.jsonl').read_text().splitlines()]
    assert len(rows) == len({r['request_id'] for r in rows}) == 648
    assert Counter(r['program'] for r in rows) == {'historical-session': 630, 'same-day-context': 18}
    assert Counter(r['finish_reason'] for r in rows) == {'stop': 633, 'tool_calls': 15}
    assert all(r['http_status'] == 200 and r['outcome'] == 'success' for r in rows)
    context = [r for r in rows if r['program'] == 'same-day-context']
    assert max(r['input']['provider_input_tokens'] for r in context) == 98410
    assert all(r['input']['delivered_size_state'] == 'outside_tolerance' for r in context)
    checks = json.loads((ROOT / 'context-checks.json').read_text())
    assert len(checks) == 18
    assert all(c['demonstrated_effective'] for c in checks)
    assert Counter(c['position'] for c in checks) == {'early': 6, 'middle': 6, 'late': 6}


def test_public_export_hashes():
    record = json.loads((ROOT / 'provenance.json').read_text())
    for name, expected in record['files'].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected
