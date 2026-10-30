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


def test_purchase_panel_exposes_optional_mortgage_affordability(monkeypatch, tmp_path):
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "matplotlib"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    app = AppTest.from_file(str(APP_PATH)).run(timeout=30)

    next(
        item for item in app.checkbox
        if item.label == "Enable future property purchase"
    ).set_value(True).run(timeout=30)
    next(
        item for item in app.checkbox
        if item.label == "Also require mortgage affordability"
    ).set_value(True).run(timeout=30)
    next(
        item for item in app.checkbox
        if item.label == "Add a delay between eligibility and closing"
    ).set_value(True).run(timeout=30)

    assert not app.exception
    labels = {item.label for item in app.number_input}
    assert "Maximum total debt service (% of qualifying income)" in labels
    assert "Count freelance / royalty / other income (%)" in labels
    affordability_help = {
        item.label: item.proto.help
        for item in app.number_input
        if item.key in {
            "purchase_affordability_ratio_input",
            "purchase_variable_income_input",
            "purchase_base_buffer_input",
            "purchase_adult_count_input",
            "purchase_adult_buffer_input",
            "purchase_dependent_count_input",
            "purchase_dependent_buffer_input",
        }
    }
    assert len(affordability_help) == 7
    assert all(affordability_help.values())
    assert "living costs" in affordability_help["Base monthly living buffer"]
    assert "Buffer per adult" in labels
    assert "Buffer per dependent" in labels
    assert "Months from eligibility to closing" in labels
    assert any(
        item.label == "If closing checks no longer pass"
        for item in app.selectbox
    )
    assert any(
        "Current estimate" in item.value and "debt-service ratio" in item.value
        for item in app.caption
    )
    assert any(
        "Annual bonuses are excluded" in item.value
        for item in app.caption
    )
    assert any(
        "no property, mortgage, or owner costs" in item.value
        for item in app.caption
    )

    next(
        item for item in app.radio if item.label == "Closing delay"
    ).set_value("Bounded random").run(timeout=30)

    assert not app.exception
    labels = {item.label for item in app.number_input}
    assert "Minimum delay (months)" in labels
    assert "Maximum delay (months)" in labels


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
