"""The committed synthetic report example stays loadable and honestly labelled."""

from pathlib import Path

from endpoint_bench.report import load_report

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "synthetic" / "report.json"


def test_committed_synthetic_report_loads_at_the_current_schema() -> None:
    report = load_report(EXAMPLE)

    assert report["schema_version"] == "0.2.0"
    assert report["legacy_schema"] is False
    assert report["deployment_key"] == "local-mock-agent-replay"
    assert report["counters"]["successful_trials"] == 2
    assert report["completion"]["natural_completions"] == 2


def test_committed_synthetic_report_makes_no_token_sizing_claim() -> None:
    report = load_report(EXAMPLE)

    assert report["token_accounting"]["claims_supported"] is False
    assert report["metric_validity"]["output_token_throughput"]["state"] == "invalid_token_sizing"
    assert (
        report["metric_validity"]["time_to_first_token"]["state"] == "valid_mean_only"
    )


def test_committed_synthetic_report_contains_no_absolute_scratch_paths() -> None:
    text = EXAMPLE.read_text(encoding="utf-8")

    assert "/Users/" not in text
    assert "/tmp/" not in text
    assert "<run-dir>" in text
