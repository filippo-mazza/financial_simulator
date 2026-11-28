"""Run Streamlit aggregate-import cases without other tests' NumPy stubs."""

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_aggregated_expenses_ui_cases():
    result = subprocess.run(
        [
            sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
            "tests/aggregated_expenses_ui_cases.py",
        ],
        cwd=ROOT, capture_output=True, text=True, timeout=180, check=False,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
