import copy
import json
from pathlib import Path

import streamlit as st
from streamlit.testing.v1 import AppTest

from app import (
    SimulationConfig, _aggregate_scope, _new_aggregate_workspace,
    _reset_aggregate_editor,
)
from scenario_workspace import ScenarioWorkspace


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "aggregated-expenses-v1.json"


class Upload:
    def __init__(self, data):
        self.data = data

    def getvalue(self):
        return self.data


def setup_app(monkeypatch, document):
    original = st.file_uploader
    upload = {"generation": 0, "data": json.dumps(document).encode()}

    def uploader(label, *args, **kwargs):
        if label == "Import aggregated expenses JSON":
            if kwargs["key"] == f"aggregated_expense_file_{upload['generation']}":
                return Upload(upload["data"])
            return None
        return original(label, *args, **kwargs)

    monkeypatch.setattr(st, "file_uploader", uploader)
    app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=60)
    return app, upload


def widget(items, label):
    return next(item for item in items if item.label == label)


def select_month(app, month="2025-01"):
    widget(app.selectbox, "Select one covered month to import").set_value(month).run(timeout=60)
    assert not app.exception


def confirm_and_apply(app):
    widget(app.checkbox, "I reviewed the applied whole-dollar amounts and confirm appending them to this scenario").check().run(timeout=60)
    widget(app.button, "Append imported expenses").click().run(timeout=60)


def test_import_preview_confirmation_duplicates_and_new_upload(monkeypatch):
    document = json.loads(FIXTURE.read_text())
    app, upload = setup_app(monkeypatch, document)
    original = copy.deepcopy(app.session_state["expenses"])
    assert any("Expense JSON loaded." in item.value for item in app.toast)
    assert widget(app.selectbox, "Select one covered month to import").value is None
    assert app.session_state["expenses"] == original
    app.run(timeout=60)
    assert not any("Expense JSON loaded." in item.value for item in app.toast)
    select_month(app)
    assert widget(app.selectbox, "uncategorized — map to").value == "Skip"
    assert widget(app.selectbox, "unexpected — map to").value == "Skip"
    assert any("stochastic" in item.value for item in app.warning)
    widget(app.number_input, "groceries — amount override").set_value(201.75).run(timeout=60)
    assert any("groceries: $201/month applied" in item.value for item in app.caption)
    confirm_and_apply(app)
    assert app.session_state["expenses"] == original
    assert any("required confirmations" in item.value for item in app.error)
    widget(app.checkbox, "I acknowledge the currency is EUR; amounts will be treated as dollars without conversion").check().run(timeout=60)
    widget(app.button, "Append imported expenses").click().run(timeout=60)
    assert not app.exception
    assert any("Appended expenses for 2025-01." in item.value for item in app.toast)
    assert app.session_state["expenses"][:len(original)] == original
    assert next(item for item in app.session_state["expenses"] if item["name"] == "Imported 2025-01 groceries")["monthly_amount"] == 201
    count = len(app.session_state["expenses"])
    app.run(timeout=60)
    assert len(app.session_state["expenses"]) == count
    assert app.session_state["aggregate_upload_generation"] == 1

    # A different document covering the same month still requires a duplicate confirmation.
    document["currency"] = "USD"
    upload.update(generation=1, data=json.dumps(document).encode())
    app.run(timeout=60)
    select_month(app)
    assert widget(app.checkbox, "I confirm I want to append this month again").value is False
    assert widget(app.checkbox, "I reviewed the applied whole-dollar amounts and confirm appending them to this scenario").value is False
    confirm_and_apply(app)
    assert len(app.session_state["expenses"]) == count
    widget(app.checkbox, "I confirm I want to append this month again").check().run(timeout=60)
    widget(app.button, "Append imported expenses").click().run(timeout=60)
    assert len(app.session_state["expenses"]) > count


def test_unknown_values_must_be_overridden_and_scenario_keys_are_isolated(monkeypatch):
    document = json.loads(FIXTURE.read_text())
    document["currency"] = None
    month = document["monthly_totals"][0]
    month["categories"]["groceries"] = None
    month["expenses_total"] = None
    reconciliation = document["reconciliation"]
    reconciliation["expenses_total"] = None
    reconciliation["debits_difference"] = None
    reconciliation["status"] = "unreconciled"
    app, _ = setup_app(monkeypatch, document)
    select_month(app)
    assert widget(app.number_input, "groceries — amount override").value is None
    assert any("unreconciled" in item.value.lower() for item in app.warning)
    widget(app.checkbox, "I acknowledge the currency is unknown; amounts will be treated as dollars without conversion").check().run(timeout=60)
    confirm_and_apply(app)
    assert any("Enter amounts or skip unknown" in item.value for item in app.error)
    widget(app.number_input, "groceries — amount override").set_value(23.99).run(timeout=60)
    assert not widget(app.checkbox, "I reviewed the applied whole-dollar amounts and confirm appending them to this scenario").value
    confirm_and_apply(app)
    assert next(item for item in app.session_state["expenses"] if item["name"] == "Imported 2025-01 groceries")["monthly_amount"] == 23

    app.button(key="create_scenario_workspace").click().run(timeout=60)
    assert not app.exception
    app.radio(key="scenario_variant_selector").set_value("alternative").run(timeout=60)
    assert not app.exception
    assert any(item["name"] == "Imported 2025-01 groceries" for item in app.session_state["expenses"])


def test_scope_lineage_and_scenario_replacement():
    state = {"aggregate_imported_months": {"single_initial": {"2025-01"}}}
    source = _aggregate_scope(state)
    state["scenario_workspace"] = ScenarioWorkspace.from_config(SimulationConfig())
    _new_aggregate_workspace(state, source, clone_all=True)
    baseline = _aggregate_scope(state)
    state["scenario_workspace"] = state["scenario_workspace"].activate("alternative")
    alternative = _aggregate_scope(state)
    assert baseline != alternative
    assert state["aggregate_imported_months"][baseline] == {"2025-01"}
    assert state["aggregate_imported_months"][alternative] == {"2025-01"}
    _reset_aggregate_editor(state)
    assert alternative not in state["aggregate_imported_months"]
    assert state["aggregate_imported_months"][baseline] == {"2025-01"}
    assert state["aggregate_editor_revisions"][alternative] == 1
    _new_aggregate_workspace(state)
    assert _aggregate_scope(state) != alternative
    assert not state["aggregate_imported_months"][_aggregate_scope(state)]


def test_switching_variants_and_replacing_upload_resets_controls(monkeypatch):
    document = json.loads(FIXTURE.read_text())
    app, upload = setup_app(monkeypatch, document)
    app.button(key="create_scenario_workspace").click().run(timeout=60)
    select_month(app)
    widget(app.selectbox, "uncategorized — map to").set_value("groceries").run(timeout=60)
    widget(app.checkbox, "I acknowledge the currency is EUR; amounts will be treated as dollars without conversion").check().run(timeout=60)
    widget(app.checkbox, "I reviewed the applied whole-dollar amounts and confirm appending them to this scenario").check().run(timeout=60)
    app.radio(key="scenario_variant_selector").set_value("alternative").run(timeout=60)
    assert not app.exception
    assert widget(app.selectbox, "Select one covered month to import").value is None
    select_month(app)
    assert widget(app.selectbox, "uncategorized — map to").value == "Skip"
    assert not widget(app.checkbox, "I acknowledge the currency is EUR; amounts will be treated as dollars without conversion").value
    assert not widget(app.checkbox, "I reviewed the applied whole-dollar amounts and confirm appending them to this scenario").value

    document["currency"] = "USD"
    upload["data"] = json.dumps(document).encode()
    app.run(timeout=60)
    assert widget(app.selectbox, "Select one covered month to import").value is None
    select_month(app)
    confirm_and_apply(app)
    assert any(item["name"] == "Imported 2025-01 groceries" for item in app.session_state["expenses"])
    app.radio(key="scenario_variant_selector").set_value("baseline").run(timeout=60)
    assert not any(item["name"] == "Imported 2025-01 groceries" for item in app.session_state["expenses"])
    upload["generation"] = 1
    app.run(timeout=60)
    select_month(app)
    assert not any(item.label == "I confirm I want to append this month again" for item in app.checkbox)
    app.radio(key="scenario_variant_selector").set_value("alternative").run(timeout=60)
    select_month(app)
    assert widget(app.checkbox, "I confirm I want to append this month again").value is False


def test_invalid_document_does_not_change_scenario(monkeypatch):
    document = json.loads(FIXTURE.read_text())
    document["transactions"] = [{"description": "private"}]
    app, _ = setup_app(monkeypatch, document)
    assert any("Cannot import aggregated expenses" in item.value for item in app.error)
    assert not any("Expense JSON loaded." in item.value for item in app.toast)
    assert not any("private" in item.value for item in app.error)
    assert app.session_state["expenses"] == [
        {"name": "Living expenses", "expense_type": "housing",
         "monthly_amount": 1500.0, "_id": app.session_state["expenses"][0]["_id"]}
    ]


def test_decimal_rounding_and_zero_unexpected_are_explicit(monkeypatch):
    document = json.loads(FIXTURE.read_text())
    document["currency"] = "USD"
    app, upload = setup_app(monkeypatch, document)
    text = json.dumps(document)
    text = text.replace('"groceries": 200,', '"groceries": 199.9999999999999999,', 1)
    text = text.replace('"expenses_total": 1050,', '"expenses_total": 1049.9999999999999999,', 1)
    text = text.replace('"expenses_total": 2200,', '"expenses_total": 2199.9999999999999999,', 1)
    text = text.replace('"source_debits_total": 2500,', '"source_debits_total": 2499.9999999999999999,', 1)
    upload["data"] = text.encode()
    app.run(timeout=60)
    select_month(app)
    assert widget(app.number_input, "groceries — amount override").value == 199.0
    widget(app.selectbox, "unexpected — map to").set_value("unexpected").run(timeout=60)
    assert any("not a recurring monthly amount" in item.value for item in app.caption)
    confirm_and_apply(app)
    assert next(item for item in app.session_state["expenses"] if item["name"] == "Imported 2025-01 groceries")["monthly_amount"] == 199
    assert next(item for item in app.session_state["expenses"] if item["name"] == "Imported 2025-01 unexpected")["monthly_amount"] == 0
