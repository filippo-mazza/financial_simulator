import subprocess
import sys
import os
from pathlib import Path


def test_documented_example_runs_as_a_real_program():
    """Exercise the complete application path in a fresh Python process."""
    repository_root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["MPLCONFIGDIR"] = str(repository_root / ".pytest-mplconfig")
    environment["XDG_CACHE_HOME"] = str(repository_root / ".pytest-cache-home")
    result = subprocess.run(
        [sys.executable, "example.py"],
        cwd=repository_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Simulation complete: 60 months" in result.stdout
    assert "Final net worth:" in result.stdout
