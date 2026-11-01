from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from endpoint_bench.aiperf import (
    BASELINE_CLIENT,
    TerminationReport,
    build_benchmark_config,
    run_aiperf,
    terminate_process_group,
    write_config,
)
from endpoint_bench.mock_provider import MockBehavior, MockProvider

AIPERF_EXECUTABLE = Path(".venv-aiperf/bin/aiperf")
requires_aiperf = pytest.mark.skipif(
    not AIPERF_EXECUTABLE.exists(),
    reason="pinned AIPerf environment not installed; see docs/decisions/0002",
)

SECRET = "sk-process-lifecycle-secret"

#: A process that ignores SIGTERM and spawns a child that also ignores SIGTERM.
#: This replicates AIPerf's controller-plus-workers shape under a hostile signal
#: policy, so escalation and group cleanup are exercised deterministically.
STUBBORN_TREE = (
    "import signal, subprocess, sys, time\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "subprocess.Popen([sys.executable, '-c', "
    "'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)'])\n"
    "print('parent ready', flush=True)\n"
    "time.sleep(60)\n"
)


def _stubborn_tree() -> subprocess.Popen[str]:
    return subprocess.Popen(  # noqa: S603 - fixed argv, test only
        [sys.executable, "-c", STUBBORN_TREE],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        start_new_session=True,
    )


def test_sigterm_escalates_to_sigkill_and_reaps_group() -> None:
    process = _stubborn_tree()
    try:
        assert process.stdout is not None
        assert "parent ready" in (process.stdout.readline() or "")
        pgid = os.getpgid(process.pid)

        sigterm, sigkill, gone, reaped = terminate_process_group(process, grace_s=0.5)

        assert sigterm is True, "SIGTERM should be attempted first"
        assert sigkill is True, "a SIGTERM-ignoring tree must escalate to SIGKILL"
        assert gone is True, "no process in the group may survive cleanup"
        assert reaped is True, "the direct child must be reaped"
        assert process.poll() is not None
        with pytest.raises(ProcessLookupError):
            os.killpg(pgid, 0)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_cooperative_process_exits_on_sigterm_without_sigkill() -> None:
    process = subprocess.Popen(  # noqa: S603 - fixed argv, test only
        [sys.executable, "-c", "import time; print('ready', flush=True); time.sleep(60)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        start_new_session=True,
    )
    try:
        assert process.stdout is not None
        assert "ready" in (process.stdout.readline() or "")

        sigterm, sigkill, gone, reaped = terminate_process_group(process, grace_s=2.0)

        assert sigterm is True
        assert sigkill is False, "a cooperative process must not be SIGKILLed"
        assert gone is True
        assert reaped is True
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_cancellation_is_recorded_separately_from_deadline() -> None:
    cancel = threading.Event()
    cancel.set()

    def _trigger() -> None:
        time.sleep(0.2)
        cancel.set()

    # Already-set event: the run should stop promptly and be labelled cancelled.
    process = subprocess.Popen(  # noqa: S603 - fixed argv, test only
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        from endpoint_bench.aiperf import _wait_for_exit

        reason = _wait_for_exit(process, timeout_s=30, cancel_event=cancel, poll_interval_s=0.01)
        assert reason == "cancelled"
        sigterm, _, gone, _ = terminate_process_group(process, grace_s=2.0)
        assert sigterm and gone
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_deadline_expires_without_cancellation() -> None:
    process = subprocess.Popen(  # noqa: S603 - fixed argv, test only
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        from endpoint_bench.aiperf import _wait_for_exit

        reason = _wait_for_exit(
            process, timeout_s=0.3, cancel_event=None, poll_interval_s=0.02
        )
        assert reason == "deadline"
    finally:
        process.kill()
        process.wait(timeout=5)


def test_termination_report_serializes() -> None:
    report = TerminationReport(
        reason="deadline", sigterm_sent=True, sigkill_sent=False, process_group_gone=True
    )

    payload = report.to_dict()

    assert payload["reason"] == "deadline"
    assert payload["sigterm_sent"] is True
    assert payload["sigkill_sent"] is False


# -- against the real engine -------------------------------------------------


@requires_aiperf
def test_stalled_engine_run_records_termination_evidence(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "stall"
    with MockProvider(behavior=MockBehavior.HANG_BEFORE_FIRST_TOKEN, hang_seconds=120) as mock:
        config = build_benchmark_config(
            model_id="mock-model",
            base_url=mock.base_url,
            client=BASELINE_CLIENT,
            requests=1,
            concurrency=1,
            input_tokens=32,
            output_tokens=8,
            artifact_dir=artifact_dir,
            credential_env_name="ENDPOINT_BENCH_OFFLINE_API_KEY",
        )
        config["benchmark"]["endpoint"]["timeout"] = 300.0
        config_path = artifact_dir / "aiperf-config.yaml"
        write_config(config_path, config)

        run = run_aiperf(
            executable=str(AIPERF_EXECUTABLE.resolve()),
            config_path=config_path,
            artifact_dir=artifact_dir,
            client=BASELINE_CLIENT,
            secrets={"ENDPOINT_BENCH_OFFLINE_API_KEY": SECRET},
            process_timeout_s=6,
            termination_grace_s=3.0,
        )

    assert run.timed_out is True
    assert run.termination.reason == "deadline"
    assert run.termination.sigterm_sent is True
    assert run.termination.process_group_gone is True
    assert run.termination.reaped is True
    # Partial output must survive termination.
    assert run.stdout_path.is_file()
    assert run.stderr_path.is_file()
    # Nothing that looks like a live worker should remain.
    assert not any("orphan workers" in note for note in run.notes)


@requires_aiperf
def test_clean_run_records_completed_termination(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "clean"
    with MockProvider() as mock:
        config = build_benchmark_config(
            model_id="mock-model",
            base_url=mock.base_url,
            client=BASELINE_CLIENT,
            requests=2,
            concurrency=1,
            input_tokens=32,
            output_tokens=8,
            artifact_dir=artifact_dir,
            credential_env_name="ENDPOINT_BENCH_OFFLINE_API_KEY",
        )
        config_path = artifact_dir / "aiperf-config.yaml"
        write_config(config_path, config)

        run = run_aiperf(
            executable=str(AIPERF_EXECUTABLE.resolve()),
            config_path=config_path,
            artifact_dir=artifact_dir,
            client=BASELINE_CLIENT,
            secrets={"ENDPOINT_BENCH_OFFLINE_API_KEY": SECRET},
            process_timeout_s=180,
        )

    assert run.exit_code == 0
    assert run.termination.reason == "completed"
    assert run.termination.sigterm_sent is False
    assert run.cancelled is False
