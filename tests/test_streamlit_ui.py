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
        "sidebar_jurisdictions",
    }
    assert {expander.key for expander in app.expander} == expected_keys
    assert _expander(app, "sidebar_cash").label.startswith("Cash · $5,000")

    app.number_input(key="scenario_initial_cash").set_value(7500).run(timeout=30)

    assert not app.exception
    assert _expander(app, "sidebar_cash").key == "sidebar_cash"
    assert _expander(app, "sidebar_cash").label.startswith("Cash · $7,500")


def test_streamlit_minimum_supports_persistent_keyed_expanders():
    assert "streamlit>=1.56,<2" in REQUIREMENTS_PATH.read_text()


def test_ab_workspace_keeps_baseline_and_alternative_inputs_isolated(
        monkeypatch, tmp_path):
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "matplotlib"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    app = AppTest.from_file(str(APP_PATH)).run(timeout=30)

    app.button(key="create_scenario_workspace").click().run(timeout=30)
    assert not app.exception
    assert app.text_input(key="scenario_baseline_name").value == "Baseline A"
    assert app.text_input(key="scenario_alternative_name").value == "Alternative B"

    app.radio(key="scenario_variant_selector").set_value("alternative").run(timeout=30)
    app.number_input(key="scenario_initial_cash").set_value(9_000).run(timeout=30)
    app.radio(key="scenario_variant_selector").set_value("baseline").run(timeout=30)

    assert not app.exception
    assert app.number_input(key="scenario_initial_cash").value == 5_000

    app.radio(key="scenario_variant_selector").set_value("alternative").run(timeout=30)

    assert not app.exception
    assert app.number_input(key="scenario_initial_cash").value == 9_000
    assert "simulation_result" not in app.session_state


def test_ab_workspace_restores_each_scenario_rent_selection(monkeypatch, tmp_path):
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "matplotlib"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    app = AppTest.from_file(str(APP_PATH)).run(timeout=30)

    next(
        item for item in app.checkbox
        if item.label == "Enable future property purchase"
    ).set_value(True).run(timeout=30)
    rent_selector = next(
        item for item in app.selectbox
        if item.label == "Rent expense to stop after purchase"
    )
    rent_selector.set_value("Living expenses").run(timeout=30)
    app.button(key="create_scenario_workspace").click().run(timeout=30)

    app.radio(key="scenario_variant_selector").set_value("alternative").run(timeout=30)
    next(
        item for item in app.selectbox
        if item.label == "Rent expense to stop after purchase"
    ).set_value("Do not stop any expense").run(timeout=30)
    app.radio(key="scenario_variant_selector").set_value("baseline").run(timeout=30)

    assert not app.exception
    assert app.session_state["selected_rent_expense"] == "Living expenses"
    assert next(
        item for item in app.selectbox
        if item.label == "Rent expense to stop after purchase"
    ).value == "Living expenses"

    app.radio(key="scenario_variant_selector").set_value("alternative").run(timeout=30)

    assert not app.exception
    assert app.session_state["selected_rent_expense"] is None
    assert next(
        item for item in app.selectbox
        if item.label == "Rent expense to stop after purchase"
    ).value == "Do not stop any expense"


def test_real_estate_panel_exposes_ownership_and_exact_links(monkeypatch, tmp_path):
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "matplotlib"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    app = AppTest.from_file(str(APP_PATH)).run(timeout=30)

    app.selectbox(key="investment_type_to_add").set_value("real_estate").run(timeout=30)
    next(
        item for item in app.button if item.label == "＋ Add investment"
    ).click().run(timeout=30)
    next(
        item for item in app.button if item.label.endswith("Investment 2")
    ).click().run(timeout=30)

    assert not app.exception
    assert any(item.label == "Ownership share (%)" for item in app.number_input)
    assert any(
        item.label == "Mortgage / loan linked to this property"
        for item in app.selectbox
    )
    assert any(
        item.label == "Rental income linked to this property"
        for item in app.selectbox
    )
    assert any(
        item.label == "Property jurisdiction estimate profile"
        for item in app.selectbox
    )
    assert any(
        item.label.startswith("Acquisition costs")
        for item in app.number_input
    )


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
    assert any(
        item.label == "Purchase jurisdiction estimate profile"
        for item in app.selectbox
    )
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


def test_monte_carlo_compare_view_shows_run_identifiers_and_seeds(tmp_path):
    script = f"""
from streamlit.testing.v1 import AppTest
app = AppTest.from_file({str(APP_PATH)!r}).run(timeout=30)
next(item for item in app.number_input if item.label == 'Number of simulations').set_value(4)
next(item for item in app.button if item.label == '▶ Run simulations').click().run(timeout=30)
next(item for item in app.radio if item.label == 'Result view').set_value('Compare').run(timeout=30)
assert not app.exception
assert any('not percentile curves' in item.value for item in app.info)
frame = app.dataframe[0].value
assert list(frame['Role']).count('Lowest') == 1
assert {{'Run', 'Seed', 'Final net worth'}} <= set(frame.columns)
assert len(frame) <= 3
"""
    environment = dict(os.environ)
    environment["MPLCONFIGDIR"] = str(tmp_path / "matplotlib")
    environment["XDG_CACHE_HOME"] = str(tmp_path / "cache")
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=APP_PATH.parent, env=environment,
        text=True, capture_output=True, check=False, timeout=60,
    )
    assert result.returncode == 0, result.stderr


def test_import_notice_is_shown_once_and_uploader_key_resets(monkeypatch, tmp_path):
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "matplotlib"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    app = AppTest.from_file(str(APP_PATH))
    app.session_state["import_uploader_counter"] = 1
    app.session_state["import_success_notice"] = "Scenario imported from a.json."
    app.run(timeout=30)

    assert not app.exception
    assert [s.value for s in app.success] == ["Scenario imported from a.json."]
    assert "import_success_notice" not in app.session_state

    app.run(timeout=30)
    assert not [s for s in app.success if "imported" in s.value]
    assert app.session_state["import_uploader_counter"] == 1
