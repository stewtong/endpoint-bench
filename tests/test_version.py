from __future__ import annotations

import subprocess
import sys

from endpoint_bench import __version__


def test_version() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "endpoint_bench.cli", "--version"],
        check=True,
        capture_output=True,
        text=True,
    )

    # Asserted against the package constant rather than a literal, so a version
    # bump is a one-line change and the test cannot merely restate it.
    assert result.stdout == f"endpoint-bench {__version__}\n"
