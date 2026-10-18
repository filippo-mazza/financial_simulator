import os
from pathlib import Path
import subprocess
import sys

from streamlit.testing.v1 import AppTest


APP_PATH = Path(__file__).parents[1] / "app.py"
REQUIREMENTS_PATH = APP_PATH.parent / "requirements.txt"


def _expander(app, key):
    return next(expander for expander in app.expander if expander.key == key)


def test_sidebar_expanders_keep_stable_identity_when_summary_changes(monkeypatch, tmp_path):
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "matplotlib"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    app = AppTest.from_file(str(APP_PATH)).run(timeout=30)

    expected_keys = {
        "sidebar_import", "sidebar_cash", "sidebar_investments", "sidebar_income",
        "sidebar_expenses", "sidebar_loans", "sidebar_assumptions", "sidebar_family",
    }
    assert {expander.key for expander in app.expander} == expected_keys
    assert _expander(app, "sidebar_cash").label.startswith("Cash · $5,000")

    app.number_input(key="scenario_initial_cash").set_value(7500).run(timeout=30)

    assert not app.exception
    assert _expander(app, "sidebar_cash").key == "sidebar_cash"
    assert _expander(app, "sidebar_cash").label.startswith("Cash · $7,500")


def test_streamlit_minimum_supports_persistent_keyed_expanders():
    assert "streamlit>=1.56,<2" in REQUIREMENTS_PATH.read_text()


def test_monte_carlo_views_explain_average_and_individual_paths(tmp_path):
    # Engine-only tests install a process-wide minimal numpy stub at collection
    # time, so render the completed Altair/pandas UI in a clean Python process.
    script = f"""
from streamlit.testing.v1 import AppTest
app = AppTest.from_file({str(APP_PATH)!r}).run(timeout=30)
next(item for item in app.number_input if item.label == 'Number of simulations').set_value(2)
next(item for item in app.button if item.label == '▶ Run simulations').click().run(timeout=30)
assert not app.exception
assert any('not a guaranteed or individually realizable household path' in item.value and 'hide liquidity shortfalls' in item.value for item in app.info)
next(item for item in app.radio if item.label == 'Result view').set_value('Individual run').run(timeout=30)
assert not app.exception
assert any('one simulated trajectory rather than an average' in item.value for item in app.info)
assert not any('Frequencies and other summary values' in item.value for item in app.warning)
"""
    environment = dict(os.environ)
    environment["MPLCONFIGDIR"] = str(tmp_path / "matplotlib")
    environment["XDG_CACHE_HOME"] = str(tmp_path / "cache")
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=APP_PATH.parent, env=environment,
        text=True, capture_output=True, check=False, timeout=60,
    )
    assert result.returncode == 0, result.stderr


def test_monte_carlo_low_sample_warning_is_visible(tmp_path):
    script = f"""
from streamlit.testing.v1 import AppTest
app = AppTest.from_file({str(APP_PATH)!r}).run(timeout=30)
next(item for item in app.number_input if item.label == 'Number of simulations').set_value(2)
next(item for item in app.button if item.label == '▶ Run simulations').click().run(timeout=30)
assert not app.exception
assert any('especially small' in item.value and 'tail probabilities' in item.value for item in app.warning)
"""
    environment = dict(os.environ)
    environment["MPLCONFIGDIR"] = str(tmp_path / "matplotlib")
    environment["XDG_CACHE_HOME"] = str(tmp_path / "cache")
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=APP_PATH.parent, env=environment,
        text=True, capture_output=True, check=False, timeout=60,
    )
    assert result.returncode == 0, result.stderr
