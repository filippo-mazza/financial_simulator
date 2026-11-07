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
        "sidebar_jurisdictions", "sidebar_curveballs",
    }
    assert {expander.key for expander in app.expander} == expected_keys
    assert _expander(app, "sidebar_cash").label.startswith("Cash · $5,000")

    app.number_input(key="scenario_initial_cash").set_value(7500).run(timeout=30)

    assert not app.exception
    assert _expander(app, "sidebar_cash").key == "sidebar_cash"
    assert _expander(app, "sidebar_cash").label.startswith("Cash · $7,500")


def test_curveballs_panel_exposes_predefined_event_checkboxes(monkeypatch, tmp_path):
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "matplotlib"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    app = AppTest.from_file(str(APP_PATH)).run(timeout=30)

    curveballs = _expander(app, "sidebar_curveballs")
    temporary_job_loss = next(
        item for item in curveballs.checkbox if item.label == "Temporary job loss"
    )
    temporary_job_loss.set_value(True).run(timeout=30)

    assert not app.exception
    assert any(
        item.label == "Reduced working hours"
        for item in _expander(app, "sidebar_curveballs").checkbox
    )
    assert app.session_state["curveballs"][0]["name"] == "Temporary job loss"
    assert app.session_state["curveballs"][0]["enabled"] is True


def test_selected_curveball_can_be_customized_in_collapsed_expander(monkeypatch, tmp_path):
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "matplotlib"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    app = AppTest.from_file(str(APP_PATH)).run(timeout=30)

    _expander(app, "sidebar_curveballs").checkbox(
        key="curveball_preset_temporary_job_loss"
    ).set_value(True).run(timeout=30)
    customize = next(
        item for item in app.expander
        if item.key == "curveball_customize_temporary_job_loss"
    )
    customize.number_input(
        key="curveball_probability_temporary_job_loss"
    ).set_value(50.0).run(timeout=30)

    assert not app.exception
    assert app.session_state["curveballs"][0]["probability"] == 50.0


def test_curveball_preset_state_is_isolated_between_ab_variants(monkeypatch, tmp_path):
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "matplotlib"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    app = AppTest.from_file(str(APP_PATH)).run(timeout=30)

    app.button(key="create_scenario_workspace").click().run(timeout=30)
    app.radio(key="scenario_variant_selector").set_value("alternative").run(timeout=30)
    _expander(app, "sidebar_curveballs").checkbox(
        key="curveball_preset_temporary_job_loss"
    ).set_value(True).run(timeout=30)
    app.radio(key="scenario_variant_selector").set_value("baseline").run(timeout=30)

    assert not app.exception
    assert app.session_state["curveballs"] == []
    assert app.checkbox(key="curveball_preset_temporary_job_loss").value is False


def test_curveball_preset_range_tracks_shorter_horizon(monkeypatch, tmp_path):
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "matplotlib"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    app = AppTest.from_file(str(APP_PATH)).run(timeout=30)

    _expander(app, "sidebar_curveballs").checkbox(
        key="curveball_preset_temporary_job_loss"
    ).set_value(True).run(timeout=30)
    app.slider(key="scenario_years").set_value(1).run(timeout=30)

    assert not app.exception
    assert app.session_state["curveballs"][0]["start_month_max"] == 12


def test_curveball_custom_start_range_survives_variant_switch(monkeypatch, tmp_path):
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "matplotlib"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    app = AppTest.from_file(str(APP_PATH)).run(timeout=30)

    curveballs = _expander(app, "sidebar_curveballs")
    curveballs.checkbox(
        key="curveball_preset_temporary_job_loss"
    ).set_value(True).run(timeout=30)
    customize = next(
        item for item in app.expander
        if item.key == "curveball_customize_temporary_job_loss"
    )
    customize.number_input(key="curveball_start_min_temporary_job_loss").set_value(24).run(
        timeout=30
    )
    customize = next(
        item for item in app.expander
        if item.key == "curveball_customize_temporary_job_loss"
    )
    customize.number_input(key="curveball_start_max_temporary_job_loss").set_value(36).run(
        timeout=30
    )
    app.button(key="create_scenario_workspace").click().run(timeout=30)
    app.radio(key="scenario_variant_selector").set_value("alternative").run(timeout=30)
    app.radio(key="scenario_variant_selector").set_value("baseline").run(timeout=30)

    assert not app.exception
    assert app.session_state["curveballs"][0]["start_month_min"] == 24
    assert app.session_state["curveballs"][0]["start_month_max"] == 36


def test_imported_custom_curveball_can_be_removed(monkeypatch, tmp_path):
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "matplotlib"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    app = AppTest.from_file(str(APP_PATH)).run(timeout=30)
    app.session_state["curveballs"] = [{
        "name": "Old custom event", "enabled": True, "probability": 25.0,
        "start_month_min": 1, "start_month_max": 120, "duration_months": 3,
        "affected_income_name": None, "income_reduction_pct": 50.0,
        "recovery_months": 3, "_id": "old-custom",
    }]
    app.run(timeout=30)

    curveballs = _expander(app, "sidebar_curveballs")
    next(item for item in curveballs.button if item.label == "Remove").click().run(
        timeout=30
    )

    assert not app.exception
    assert app.session_state["curveballs"] == []


def test_new_expense_is_inserted_at_top_of_expense_list(monkeypatch, tmp_path):
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "matplotlib"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    app = AppTest.from_file(str(APP_PATH)).run(timeout=30)

    expenses = _expander(app, "sidebar_expenses")
    next(item for item in expenses.button if item.label == "＋ Add expense").click().run(
        timeout=30
    )

    assert not app.exception
    expense_buttons = [
        item.label for item in _expander(app, "sidebar_expenses").button
        if item.label.startswith(("▸ ", "▾ "))
    ]
    assert expense_buttons[0].endswith("Expense 2")
    assert expense_buttons[1].endswith("Living expenses")


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


def test_ab_workspace_restores_each_scenario_maintenance_percentage(monkeypatch, tmp_path):
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "matplotlib"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    app = AppTest.from_file(str(APP_PATH)).run(timeout=30)

    next(
        item for item in app.checkbox
        if item.label == "Enable future property purchase"
    ).set_value(True).run(timeout=30)
    app.number_input(key="purchase_maintenance_percentage").set_value(1.0).run(timeout=30)
    app.button(key="create_scenario_workspace").click().run(timeout=30)

    app.radio(key="scenario_variant_selector").set_value("alternative").run(timeout=30)
    app.number_input(key="purchase_maintenance_percentage").set_value(3.0).run(timeout=30)
    app.radio(key="scenario_variant_selector").set_value("baseline").run(timeout=30)

    assert not app.exception
    assert app.number_input(key="purchase_maintenance_percentage").value == 1.0


def test_ab_workspace_runs_paired_comparison_dashboard(tmp_path):
    # Engine-only tests install a minimal numpy stub process-wide. Exercise the
    # completed dataframe/Altair dashboard in a clean interpreter.
    script = f"""
from streamlit.testing.v1 import AppTest
app = AppTest.from_file({str(APP_PATH)!r}).run(timeout=30)
app.button(key='create_scenario_workspace').click().run(timeout=30)
next(item for item in app.number_input if item.label == 'Number of simulations').set_value(2).run(timeout=30)
next(item for item in app.button if item.label == '⇄ Compare A/B').click().run(timeout=30)
assert not app.exception
assert any(item.value == 'Paired A/B comparison' for item in app.subheader)
assert any('B−A is calculated within each seed pair' in item.value for item in app.info)
assert any('scheduled investment contributions' in item.value.lower() for item in app.caption)
assert len(app.dataframe) >= 2
"""
    environment = dict(os.environ)
    environment["MPLCONFIGDIR"] = str(tmp_path / "matplotlib")
    environment["XDG_CACHE_HOME"] = str(tmp_path / "cache")
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=APP_PATH.parent, env=environment,
        text=True, capture_output=True, check=False, timeout=60,
    )
    assert result.returncode == 0, result.stderr


def test_ab_comparison_marks_results_for_mobile_focus(tmp_path):
    script = f"""
from streamlit.testing.v1 import AppTest
app = AppTest.from_file({str(APP_PATH)!r}).run(timeout=30)
app.button(key='create_scenario_workspace').click().run(timeout=30)
next(item for item in app.button if item.label == '⇄ Compare A/B').click().run(timeout=30)
assert not app.exception
assert any('comparison-results' in item.value for item in app.markdown)
"""
    environment = dict(os.environ)
    environment["MPLCONFIGDIR"] = str(tmp_path / "matplotlib")
    environment["XDG_CACHE_HOME"] = str(tmp_path / "cache")
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=APP_PATH.parent, env=environment,
        text=True, capture_output=True, check=False, timeout=60,
    )
    assert result.returncode == 0, result.stderr


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
    assert any(
        item.label.endswith(": Ownership share (%)")
        and item.label.startswith("Investment 2")
        for item in app.number_input
    )
    assert any(
        item.label.endswith(": Mortgage / loan linked to this property")
        for item in app.selectbox
    )
    assert any(
        item.label.endswith(": Rental income linked to this property")
        for item in app.selectbox
    )
    assert any(
        item.label.endswith(": Property jurisdiction estimate profile")
        for item in app.selectbox
    )
    assert any(
        "Acquisition costs" in item.label
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


def test_completed_run_announces_deterministic_ab_suggestions(tmp_path):
    script = f"""
from streamlit.testing.v1 import AppTest
app = AppTest.from_file({str(APP_PATH)!r}).run(timeout=30)
next(item for item in app.button if item.label == '▶ Run simulations').click().run(timeout=60)
assert not app.exception
assert any('Deterministic A/B alternatives' in item.value for item in app.toast)
assert sum(item.label == 'Apply and compare' for item in app.button) == 3
"""
    environment = dict(os.environ)
    environment["MPLCONFIGDIR"] = str(tmp_path / "matplotlib")
    environment["XDG_CACHE_HOME"] = str(tmp_path / "cache")
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=APP_PATH.parent, env=environment,
        text=True, capture_output=True, check=False, timeout=90,
    )
    assert result.returncode == 0, result.stderr


def test_applying_suggestion_resets_existing_workspace_widget_state(tmp_path):
    script = f"""
from streamlit.testing.v1 import AppTest
app = AppTest.from_file({str(APP_PATH)!r}).run(timeout=30)
app.button(key='create_scenario_workspace').click().run(timeout=30)
next(item for item in app.number_input if item.label == 'Number of simulations').set_value(1).run(timeout=30)
next(item for item in app.button if item.label == '▶ Run simulations').click().run(timeout=60)
app.button(key='apply_ab_suggestion_larger_cash_buffer').click().run(timeout=60)
assert not app.exception
assert any(item.value == 'Paired A/B comparison' for item in app.subheader)
assert not any('A/B inputs, names, or run count changed' in item.value for item in app.warning)
"""
    environment = dict(os.environ)
    environment["MPLCONFIGDIR"] = str(tmp_path / "matplotlib")
    environment["XDG_CACHE_HOME"] = str(tmp_path / "cache")
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=APP_PATH.parent, env=environment,
        text=True, capture_output=True, check=False, timeout=120,
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
    assert not app.success
    assert "import_success_notice" not in app.session_state

    app.run(timeout=30)
    assert not [s for s in app.success if "imported" in s.value]
    assert app.session_state["import_uploader_counter"] == 1


def _fresh_app(monkeypatch, tmp_path):
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "matplotlib"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return AppTest.from_file(str(APP_PATH)).run(timeout=30)


def test_scenario_json_actions_are_grouped_in_sidebar(monkeypatch, tmp_path):
    script = f"""
from streamlit.testing.v1 import AppTest
app = AppTest.from_file({str(APP_PATH)!r}).run(timeout=30)
panel = next(item for item in app.sidebar.expander if item.key == 'sidebar_import')
assert panel.label == 'Scenario import/export'
assert {{item.label for item in panel.file_uploader}} == {{
        "Import single-scenario JSON",
        "Import A/B workspace JSON",
    }}
assert {{item.label for item in panel.checkbox}} == {{
    "Import multiple scenarios",
    }}
assert "Single-scenario export filename" in {{
    item.label for item in panel.text_input
}}
assert {{item.label for item in panel.button}} >= {{
    "Import A/B workspace",
    "Create Baseline A and Alternative B",
}}
assert "Import two scenarios" not in {{
    item.label for item in panel.button
}}
app.sidebar.button(key='create_scenario_workspace').click().run(timeout=30)
next(item for item in app.number_input if item.label == 'Number of simulations').set_value(1).run(timeout=30)
next(item for item in app.button if item.label == '▶ Run simulations').click().run(timeout=60)
assert not app.exception
panel = next(item for item in app.sidebar.expander if item.key == 'sidebar_import')
assert {{item.label for item in panel.download_button}} == {{
        "Export single-scenario JSON",
        "Export A/B workspace JSON",
    }}
assert {{item.label for item in panel.text_input}} >= {{
        "Single-scenario export filename",
        "A/B workspace export filename",
    }}
"""
    environment = dict(os.environ)
    environment["MPLCONFIGDIR"] = str(tmp_path / "matplotlib")
    environment["XDG_CACHE_HOME"] = str(tmp_path / "cache")
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=APP_PATH.parent, env=environment,
        text=True, capture_output=True, check=False, timeout=90,
    )
    assert result.returncode == 0, result.stderr


def test_scenario_json_import_errors_keep_uploads_and_explain_failure(
        monkeypatch, tmp_path):
    app = _fresh_app(monkeypatch, tmp_path)
    panel = _expander(app.sidebar, "sidebar_import")
    panel.file_uploader(key="scenario_import_file_0").upload(
        "invalid-scenario.json", b"{", "application/json",
    ).run(timeout=30)

    assert not app.exception
    assert app.session_state.get("import_uploader_counter", 0) == 0
    assert app.sidebar.file_uploader(key="scenario_import_file_0").value
    assert any(
        "The selected file is not valid UTF-8 JSON." in item.value
        for item in _expander(app.sidebar, "sidebar_import").error
    )

    panel = _expander(app.sidebar, "sidebar_import")
    panel.file_uploader(key="scenario_workspace_file_0").upload(
        "invalid-workspace.json", b"{", "application/json",
    ).run(timeout=30)
    _expander(app.sidebar, "sidebar_import").button(
        key="import_scenario_workspace",
    ).click().run(timeout=30)

    assert not app.exception
    assert app.session_state["workspace_import_counter"] == 0
    assert app.sidebar.file_uploader(key="scenario_workspace_file_0").value


def test_older_single_scenario_import_updates_widget_state_before_render(tmp_path):
    script = f"""
import json
from app import SimulationConfig, create_simulation_export, run_simulation
from streamlit.testing.v1 import AppTest

config = SimulationConfig(years=1, seed=7, investments=[], incomes=[], expenses=[], loans=[])
net_worth, repayments, details = run_simulation(config)
document = json.loads(create_simulation_export(config, net_worth, repayments, details))
document['parameters'].pop('curveballs', None)
payload = json.dumps(document).encode()

app = AppTest.from_file({str(APP_PATH)!r}).run(timeout=30)
app.sidebar.file_uploader(key='scenario_import_file_0').upload(
    'older-scenario.json', payload, 'application/json',
).run(timeout=30)
assert not app.exception
assert app.session_state['scenario_years'] == 1
assert app.session_state['import_uploader_counter'] == 1
assert app.session_state.get('sidebar_import') is False
assert any('older-scenario.json' in item.value for item in app.toast)
assert not any('cannot be modified after the widget' in item.value for item in app.exception)
"""
    environment = dict(os.environ)
    environment["MPLCONFIGDIR"] = str(tmp_path / "matplotlib")
    environment["XDG_CACHE_HOME"] = str(tmp_path / "cache")
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=APP_PATH.parent, env=environment,
        text=True, capture_output=True, check=False, timeout=120,
    )
    assert result.returncode == 0, result.stderr


def test_multiple_scenario_import_creates_ab_workspace(tmp_path):
    script = f"""
from app import SimulationConfig, create_simulation_export, run_simulation
from streamlit.testing.v1 import AppTest

def export(years, initial_cash):
    config = SimulationConfig(
        years=years, initial_cash=initial_cash, seed=7,
        investments=[], incomes=[], expenses=[], loans=[],
    )
    results = run_simulation(config)
    return create_simulation_export(config, *results)

app = AppTest.from_file({str(APP_PATH)!r}).run(timeout=30)
app.sidebar.checkbox(key='import_multiple_scenarios').set_value(True).run(timeout=30)
uploader = app.sidebar.file_uploader(key='scenario_import_file_0')
assert uploader.accept_multiple_files
uploader.set_value([
    ('baseline.json', export(1, 1000).encode(), 'application/json'),
    ('alternative.json', export(2, 2000).encode(), 'application/json'),
]).run(timeout=30)
assert not app.exception
assert app.session_state['scenario_workspace'] is None
app.button(key='import_multiple_scenarios_button').click().run(timeout=30)
assert not app.exception
workspace = app.session_state['scenario_workspace']
assert workspace.variant('baseline').name == 'baseline'
assert workspace.variant('alternative').name == 'alternative'
assert workspace.config_for('baseline').years == 1
assert workspace.config_for('alternative').years == 2
assert app.session_state['scenario_initial_cash'] == 1000
assert app.session_state['sidebar_import'] is False
assert any('Imported two scenarios' in item.value for item in app.toast)
"""
    environment = dict(os.environ)
    environment["MPLCONFIGDIR"] = str(tmp_path / "matplotlib")
    environment["XDG_CACHE_HOME"] = str(tmp_path / "cache")
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=APP_PATH.parent, env=environment,
        text=True, capture_output=True, check=False, timeout=120,
    )
    assert result.returncode == 0, result.stderr


def test_jurisdiction_help_popup_lists_assumptions_sources_and_limitations(
        monkeypatch, tmp_path):
    app = _fresh_app(monkeypatch, tmp_path)

    app.button(key="jurisdiction_help_button").click().run(timeout=30)

    assert not app.exception
    text = " ".join(item.value for item in app.markdown)
    assert "not legal or tax advice" in text
    assert "Zero does **not** mean tax-free" in text
    assert "Not covered" in text
    assert "https://atc.gencat.cat/" in text


def test_jurisdiction_cards_label_bases_and_flag_unsupported_cases(
        monkeypatch, tmp_path):
    app = _fresh_app(monkeypatch, tmp_path)

    assert not app.exception
    captions = " ".join(item.value for item in app.caption)
    assert "assessed/cadastral" in captions or "assessed" in captions
    assert any("Not covered" in item.value for item in app.warning)
    assert any("· indicative allowance" in item.label for item in app.number_input)
    assert any("**Grunderwerbsteuer" in item.value and "statutory rate" in item.value
               for item in app.markdown)


def test_lazio_purchase_profile_prompts_for_cadastral_income_and_warns(
        monkeypatch, tmp_path):
    app = _fresh_app(monkeypatch, tmp_path)
    next(
        item for item in app.checkbox
        if item.label == "Enable future property purchase"
    ).set_value(True).run(timeout=30)
    app.selectbox(key="purchase_jurisdiction_profile_input").set_value(
        "Lazio (Italy) — private resale, prima casa").run(timeout=30)

    assert not app.exception
    assert app.number_input(key="purchase_cadastral_income_input").value == 0
    assert any("proxy" in item.value for item in app.warning)

    app.number_input(key="purchase_cadastral_income_input").set_value(
        1_500).run(timeout=30)
    assert not app.exception
    assert not any("proxy" in item.value for item in app.warning)
