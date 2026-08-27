"""Opt-in network test: vendored ossie files match upstream. Run with `uv run pytest -m network`."""

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "check_ossie_drift.py"


@pytest.mark.network
def test_vendored_ossie_matches_upstream() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, f"\n{result.stdout}\n{result.stderr}"
