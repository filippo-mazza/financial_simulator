from pathlib import Path

from streamlit.testing.v1 import AppTest


APP_PATH = Path(__file__).parents[1] / "app.py"


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
