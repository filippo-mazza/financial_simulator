import copy
import json
from decimal import Decimal, localcontext
from pathlib import Path

import pytest

from aggregated_expenses import parse_aggregated_expenses


FIXTURE = Path(__file__).parent / "fixtures" / "aggregated-expenses-v1.json"


def example():
    return json.loads(FIXTURE.read_text())


def encode(document):
    return json.dumps(document).encode()


def test_synthetic_example_and_decimal_amounts():
    document, warnings = parse_aggregated_expenses(FIXTURE.read_bytes())
    assert warnings == []
    assert isinstance(document["monthly_totals"][0]["categories"]["groceries"], Decimal)
    assert isinstance(document["reconciliation"]["debits_difference"], Decimal)


@pytest.mark.parametrize(("change", "field"), [
    (lambda doc: doc.update(schema_version=True), "schema_version"),
    (lambda doc: doc.update(schema_version=2), "schema_version"),
    (lambda doc: doc.update(format="wrong"), "format"),
    (lambda doc: doc.update(currency="eur"), "currency"),
    (lambda doc: doc.update(currency="ZZZ"), "currency"),
    (lambda doc: doc["period"].update(start="2025-13"), "period.start"),
    (lambda doc: doc["period"].update(start="2025-01\n"), "period.start"),
    (lambda doc: doc["period"].update(end=42), "period.end"),
    (lambda doc: doc["monthly_totals"][0].update(month="2025-01\n"), "monthly_totals.month"),
    (lambda doc: doc["monthly_totals"][0].update(expenses_total="private-value"),
     "monthly_totals.0.expenses_total"),
    (lambda doc: doc["reconciliation"].pop("refunds_total"), "refunds_total"),
])
def test_schema_errors_identify_known_fields_without_values(change, field):
    doc = example()
    change(doc)
    with pytest.raises(ValueError) as error:
        parse_aggregated_expenses(encode(doc))
    assert field in str(error.value)
    assert "private-value" not in str(error.value)


@pytest.mark.parametrize("target", ["root", "monthly", "categories"])
def test_unknown_property_names_and_values_are_not_exposed(target):
    doc = example()
    obj = {"root": doc, "monthly": doc["monthly_totals"][0],
           "categories": doc["monthly_totals"][0]["categories"]}[target]
    obj["secret-account-identifier"] = "secret-value"
    with pytest.raises(ValueError) as error:
        parse_aggregated_expenses(encode(doc))
    assert "Unknown properties" in str(error.value)
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("raw", [
    b'{"sensitive-key":1,"sensitive-key":2}',
    b'{"nested":{"sensitive-key":1,"sensitive-key":2}}',
])
def test_duplicate_keys_are_rejected_without_disclosure(raw):
    with pytest.raises(ValueError, match="Duplicate JSON key") as error:
        parse_aggregated_expenses(raw)
    assert "sensitive-key" not in str(error.value)


@pytest.mark.parametrize("raw", [
    b'{"x":NaN}', b'{"x":Infinity}', b'{"x":-Infinity}',
    b'{"x":1e999999999}', b'{"x":' + b"9" * 257 + b"}",
    b"\xff", b'{"private-value":', b"[" * 1500 + b"]" * 1500,
    b" " * (2 * 1024 * 1024 + 1),
])
def test_malformed_and_oversized_input_rejected_safely(raw):
    with pytest.raises(ValueError) as error:
        parse_aggregated_expenses(raw)
    assert "private-value" not in str(error.value)


@pytest.mark.parametrize("change", [
    lambda doc: doc["period"].update(start="2025-03"),
    lambda doc: doc["monthly_totals"].reverse(),
    lambda doc: doc["monthly_totals"].append(copy.deepcopy(doc["monthly_totals"][-1])),
    lambda doc: doc["monthly_totals"][0].update(month="2024-12"),
    lambda doc: doc["monthly_totals"][0]["categories"].update(groceries=201),
    lambda doc: doc["reconciliation"].update(refunds_total=0),
    lambda doc: doc["reconciliation"].update(debits_difference=1),
    lambda doc: doc["reconciliation"].update(expenses_total=None),
])
def test_invalid_dates_and_computations_rejected(change):
    doc = example()
    change(doc)
    with pytest.raises(ValueError):
        parse_aggregated_expenses(encode(doc))


def missing_month_document():
    doc = example()
    doc["monthly_totals"].pop()
    rec = doc["reconciliation"]
    for field in ("expenses_total", "excluded_debits_total", "excluded_income_total",
                  "excluded_transfer_credits_total", "refunds_total",
                  "other_excluded_credits_total", "debits_difference", "credits_difference"):
        rec[field] = None
    rec["status"] = "unreconciled"
    return doc


def test_missing_month_is_unknown_and_warned():
    doc = missing_month_document()
    _, warnings = parse_aggregated_expenses(encode(doc))
    assert any("missing months" in warning for warning in warnings)
    assert any("unknown" in warning for warning in warnings)
    assert any("unreconciled" in warning for warning in warnings)
    doc["reconciliation"]["refunds_total"] = 50
    with pytest.raises(ValueError, match="refunds_total"):
        parse_aggregated_expenses(encode(doc))


def test_unknown_category_requires_unknown_totals_and_warns():
    doc = example()
    doc["currency"] = None
    doc["monthly_totals"][0]["categories"]["groceries"] = None
    doc["monthly_totals"][0]["expenses_total"] = None
    doc["reconciliation"].update(expenses_total=None, debits_difference=None,
                                 status="unreconciled")
    _, warnings = parse_aggregated_expenses(encode(doc))
    assert any("unknown" in warning for warning in warnings)
    doc["monthly_totals"][0]["expenses_total"] = 1050
    with pytest.raises(ValueError, match="Monthly expenses total"):
        parse_aggregated_expenses(encode(doc))


@pytest.mark.parametrize("mode", ["gap", "currency", "difference"])
def test_false_reconciled_claim_rejected(mode):
    doc = missing_month_document() if mode == "gap" else example()
    if mode == "currency":
        doc["currency"] = None
    if mode == "difference":
        doc["reconciliation"].update(source_debits_total=2501, debits_difference=1)
    doc["reconciliation"]["status"] = "reconciled"
    with pytest.raises(ValueError, match="Reconciled status"):
        parse_aggregated_expenses(encode(doc))
    doc["reconciliation"]["status"] = "unreconciled"
    _, warnings = parse_aggregated_expenses(encode(doc))
    assert any("unreconciled" in warning for warning in warnings)


def test_exact_arithmetic_ignores_decimal_context():
    raw = FIXTURE.read_text().replace(
        '"groceries": 200,', '"groceries": 200.00000000000000000000000000001,'
    ).replace(
        '"expenses_total": 1050,', '"expenses_total": 1050.00000000000000000000000000001,'
    ).replace(
        '"source_debits_total": 2500,', '"source_debits_total": 2500.00000000000000000000000000001,'
    ).replace(
        '"expenses_total": 2200,', '"expenses_total": 2200.00000000000000000000000000001,'
    )
    with localcontext() as context:
        context.prec = 3
        document, warnings = parse_aggregated_expenses(raw.encode())
        assert warnings == []
        assert document["monthly_totals"][0]["categories"]["groceries"] == Decimal(
            "200.00000000000000000000000000001"
        )
        with pytest.raises(ValueError, match="Monthly expenses total"):
            parse_aggregated_expenses(raw.replace(
                "1050.00000000000000000000000000001", "1050"
            ).encode())
