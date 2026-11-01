from __future__ import annotations

import subprocess
import sys


def test_version() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "endpoint_bench.cli", "--version"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout == "endpoint-bench 0.0.1\n"
