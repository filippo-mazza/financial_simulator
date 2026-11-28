"""Validate the version 1 aggregated-expenses interchange format."""

import json
import re
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path

import pycountry
from jsonschema import Draft202012Validator


_MAX_BYTES = 2 * 1024 * 1024
_MAX_DIGITS = 256
_MAX_EXPONENT = 1000
_MONTH = re.compile(r"(?!0000)[0-9]{4}-(0[1-9]|1[0-2])")
_MONTHLY_FIELDS = (
    "expenses_total", "excluded_debits_total", "excluded_income_total",
    "excluded_transfer_credits_total", "refunds_total", "other_excluded_credits_total",
)
_SCHEMA = json.loads(
    (Path(__file__).resolve().parent / "docs" / "aggregated-expenses.schema.json")
    .read_text(encoding="utf-8")
)
_VALIDATOR = Draft202012Validator(_SCHEMA)
_FIELD_NAMES = {
    *_SCHEMA["properties"], *_SCHEMA["properties"]["period"]["properties"],
    *_SCHEMA["properties"]["reconciliation"]["properties"],
    *_SCHEMA["$defs"]["monthly"]["properties"],
    *_SCHEMA["$defs"]["monthly"]["properties"]["categories"]["properties"],
}


def _number(token):
    try:
        value = Decimal(token)
    except InvalidOperation:
        raise ValueError("Invalid JSON number.") from None
    _, digits, exponent = value.as_tuple()
    if len(digits) > _MAX_DIGITS or abs(exponent) > _MAX_EXPONENT:
        raise ValueError("JSON number exceeds supported precision or exponent limits.")
    return value


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key; remove duplicate properties.")
        result[key] = value
    return result


def _constant(_value):
    raise ValueError("Non-finite JSON numbers are not allowed.")


def _month_index(month):
    return int(month[:4]) * 12 + int(month[5:]) - 1


def _check_computed(actual, operands, label):
    """Require null for unknown inputs and an exact value otherwise."""
    expected = None if any(value is None for value in operands) else sum(
        (Fraction(value) for value in operands), Fraction(0)
    )
    if (expected is None and actual is not None) or (
        expected is not None and (actual is None or Fraction(actual) != expected)
    ):
        raise ValueError(f"{label} must equal its computed value, or be null when unknown.")


def parse_aggregated_expenses(raw: bytes) -> tuple[dict, list[str]]:
    """Parse and validate an aggregate; return its document and safe review warnings.

    All JSON numbers are parsed as Decimal. Unknown amounts remain None. Errors
    and warnings do not include input values or unrecognized property names.
    """
    if not isinstance(raw, bytes):
        raise ValueError("Aggregate input must be UTF-8 encoded bytes.")
    if len(raw) > _MAX_BYTES:
        raise ValueError("Aggregate input exceeds the 2 MiB size limit.")
    try:
        document = json.loads(
            raw.decode("utf-8"), parse_int=_number, parse_float=_number,
            parse_constant=_constant, object_pairs_hook=_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("Invalid UTF-8 JSON document; check its encoding and syntax.") from None

    error = next(_VALIDATOR.iter_errors(document), None)
    if error is not None:
        # Schema messages and unfiltered paths can contain private input.
        location = ".".join(
            str(part) for part in error.absolute_path
            if isinstance(part, int) or part in _FIELD_NAMES
        ) or "document"
        if error.validator == "additionalProperties":
            raise ValueError(f"Unknown properties in {location}; use only schema-defined fields.")
        if error.validator == "required":
            missing = next(name for name in error.validator_value if name not in error.instance)
            raise ValueError(f"Missing required property {missing} in {location}.")
        raise ValueError(f"Invalid value in {location}; check the aggregate schema.")

    currency = document["currency"]
    if currency is not None and pycountry.currencies.get(alpha_3=currency) is None:
        raise ValueError("currency must be an ISO 4217 alphabetic code or null.")

    period = document["period"]
    for field in ("start", "end"):
        if not _MONTH.fullmatch(period[field]):
            raise ValueError(f"period.{field} must be a valid YYYY-MM month.")
    start, end = _month_index(period["start"]), _month_index(period["end"])
    if start > end:
        raise ValueError("period.start must not be later than period.end.")

    months = document["monthly_totals"]
    previous = None
    for monthly in months:
        if not _MONTH.fullmatch(monthly["month"]):
            raise ValueError("monthly_totals.month must be a valid YYYY-MM month.")
        month = _month_index(monthly["month"])
        if not start <= month <= end:
            raise ValueError("Each monthly_totals.month must fall inside the declared period.")
        if previous is not None and month <= previous:
            raise ValueError("Values of monthly_totals.month must be unique and chronological.")
        previous = month
        _check_computed(monthly["expenses_total"], list(monthly["categories"].values()),
                        "Monthly expenses total")

    gaps = len(months) != end - start + 1
    reconciliation = document["reconciliation"]
    for field in _MONTHLY_FIELDS:
        values = [item[field] for item in months]
        if gaps:
            values.append(None)
        _check_computed(reconciliation[field], values, f"Reconciliation {field}")

    debit_operands = [reconciliation["source_debits_total"]]
    debit_operands.extend(
        -Fraction(reconciliation[field]) if reconciliation[field] is not None else None
        for field in ("expenses_total", "excluded_debits_total")
    )
    credit_operands = [reconciliation["source_credits_total"]]
    credit_operands.extend(
        -Fraction(reconciliation[field]) if reconciliation[field] is not None else None
        for field in ("excluded_income_total", "excluded_transfer_credits_total",
                      "refunds_total", "other_excluded_credits_total")
    )
    _check_computed(reconciliation["debits_difference"], debit_operands,
                    "Debit difference")
    _check_computed(reconciliation["credits_difference"], credit_operands,
                    "Credit difference")

    unknown = currency is None or any(
        value is None
        for monthly in months
        for value in (*monthly["categories"].values(),
                      *(monthly[field] for field in _MONTHLY_FIELDS))
    ) or any(value is None for field, value in reconciliation.items() if field != "status")
    if reconciliation["status"] == "reconciled" and (
        unknown or gaps or reconciliation["debits_difference"] != 0
        or reconciliation["credits_difference"] != 0
    ):
        raise ValueError("Reconciled status requires known amounts and currency, complete months, and zero differences.")

    warnings = []
    if reconciliation["status"] == "unreconciled":
        warnings.append("Aggregate is unreconciled; review the reconciliation before use.")
    if unknown:
        warnings.append("Currency or amounts are unknown; null does not mean zero.")
    if gaps:
        warnings.append("The period has missing months; missing months do not mean zero.")
    return document, warnings
