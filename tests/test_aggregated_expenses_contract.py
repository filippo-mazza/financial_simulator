import copy
from decimal import Decimal
import json
from pathlib import Path

from jsonschema import Draft202012Validator
import pytest

from app import build_expense_analysis_prompt
from expense import Expense


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "docs" / "aggregated-expenses.schema.json").read_text())
EXAMPLE = json.loads(
    (ROOT / "tests" / "fixtures" / "aggregated-expenses-v1.json").read_text(),
    parse_int=Decimal, parse_float=Decimal,
)
VALIDATOR = Draft202012Validator(SCHEMA)


def test_schema_and_synthetic_example():
    Draft202012Validator.check_schema(SCHEMA)
    VALIDATOR.validate(EXAMPLE)
    assert set(EXAMPLE["monthly_totals"][0]["categories"]) == {
        *Expense("Example", "housing", 0).expense_growth, "uncategorized",
    }
    assert_reconciliation(EXAMPLE)


def assert_reconciliation(document):
    """Check the complete synthetic example's cross-field arithmetic."""
    months = document["monthly_totals"]
    assert [item["month"] for item in months] == ["2025-01", "2025-02"]
    for item in months:
        assert item["expenses_total"] == sum(item["categories"].values())
    reconciliation = document["reconciliation"]
    for field in (
        "expenses_total", "excluded_debits_total", "excluded_income_total",
        "excluded_transfer_credits_total", "refunds_total", "other_excluded_credits_total",
    ):
        assert reconciliation[field] == sum(item[field] for item in months)
    assert reconciliation["debits_difference"] == (
        reconciliation["source_debits_total"] - reconciliation["expenses_total"]
        - reconciliation["excluded_debits_total"]
    )
    assert reconciliation["credits_difference"] == (
        reconciliation["source_credits_total"] - reconciliation["excluded_income_total"]
        - reconciliation["excluded_transfer_credits_total"] - reconciliation["refunds_total"]
        - reconciliation["other_excluded_credits_total"]
    )
    assert reconciliation["status"] == "reconciled"
    assert reconciliation["debits_difference"] == reconciliation["credits_difference"] == 0


@pytest.mark.parametrize("change", [
    lambda doc: doc.update(schema_version=2),
    lambda doc: doc.update(format="financial-simulator-export"),
    lambda doc: doc.update(currency="EUR/USD"),
    lambda doc: doc["period"].update(start="2025-13"),
    lambda doc: doc["monthly_totals"][0]["categories"].update(groceries=-1),
    lambda doc: doc["monthly_totals"][0]["categories"].pop("uncategorized"),
    lambda doc: doc["monthly_totals"][0].update(description="private"),
    lambda doc: doc.update(transactions=[]),
    lambda doc: doc["reconciliation"].update(status="unknown"),
])
def test_schema_rejects_invalid_or_private_fields(change):
    document = copy.deepcopy(EXAMPLE)
    change(document)
    assert not VALIDATOR.is_valid(document)


def test_prompt_template_matches_schema():
    prompt = build_expense_analysis_prompt()
    template = json.loads("{" + prompt.split("\n\n{", 1)[1])
    template["period"] = {"start": "2025-01", "end": "2025-01"}
    template["monthly_totals"][0]["month"] = "2025-01"
    VALIDATOR.validate(template)
    assert "one ISO 4217 currency code per document" in prompt
    assert "user may override" in prompt
    assert "missing month or unknown value requires an unreconciled status" in prompt


def test_schema_allows_unreconciled_missing_month_and_unknown_currency():
    document = copy.deepcopy(EXAMPLE)
    document["currency"] = None
    document["monthly_totals"].pop()
    reconciliation = document["reconciliation"]
    for field in (
        "expenses_total", "excluded_debits_total", "excluded_income_total",
        "excluded_transfer_credits_total", "refunds_total", "other_excluded_credits_total",
        "debits_difference", "credits_difference",
    ):
        reconciliation[field] = None
    reconciliation["status"] = "unreconciled"
    VALIDATOR.validate(document)


@pytest.mark.parametrize("change", [
    lambda doc: doc["monthly_totals"][0]["categories"].update(groceries=201),
    lambda doc: doc["reconciliation"].update(refunds_total=0),
    lambda doc: doc["reconciliation"].update(debits_difference=1),
])
def test_synthetic_reconciliation_checks_detect_mismatches(change):
    document = copy.deepcopy(EXAMPLE)
    change(document)
    assert VALIDATOR.is_valid(document)
    with pytest.raises(AssertionError):
        assert_reconciliation(document)
