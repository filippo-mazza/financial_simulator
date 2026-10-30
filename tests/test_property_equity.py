import json
import math

import pytest

from app import (
    IncomeConfig,
    InvestmentConfig,
    LoanConfig,
    SimulationConfig,
    create_simulation_export,
    get_month_details,
    read_simulation_export,
    run_simulation,
)
from financial_assumptions import FinancialAssumptions
from property_purchase import PropertyPurchase


def _zero_real_estate_returns():
    defaults = FinancialAssumptions()
    means = dict(defaults.investment_return_means)
    deviations = dict(defaults.investment_return_std_devs)
    means["real_estate"] = 0
    deviations["real_estate"] = 0
    return FinancialAssumptions(
        investment_return_means=means,
        investment_return_std_devs=deviations,
    )


def _linked_property_config(**investment_overrides):
    values = {
        "name": "Fictional rental",
        "investment_type": "real_estate",
        "initial_amount": 100_000,
        "monthly_contribution": 0,
        "monthly_expenses": 200,
        "yearly_tax": 1_200,
        "ownership_percentage": 50,
        "linked_loan_name": "Rental mortgage",
        "rental_income_name": "Rental receipts",
    }
    values.update(investment_overrides)
    return SimulationConfig(
        years=1,
        initial_cash=10_000,
        yearly_fee=0,
        investments=[InvestmentConfig(**values)],
        incomes=[IncomeConfig("Rental receipts", "rent", 1_000, 10, 0)],
        expenses=[],
        loans=[LoanConfig("Rental mortgage", 40_000, 0, 10)],
        assumptions=_zero_real_estate_returns(),
    )


def test_property_position_reconciles_owned_value_debt_income_and_costs_once():
    net_worth, _, details = run_simulation(_linked_property_config())
    mortgage_payment = 40_000 / 120
    remaining_debt = 40_000 - mortgage_payment
    expected_cash = 10_000 + 900 - 200 - mortgage_payment

    assert details["property_market_values"]["Fictional rental"][0] == 100_000
    assert details["property_owned_values"]["Fictional rental"][0] == 50_000
    assert math.isclose(
        details["property_debt_balances"]["Fictional rental"][0],
        remaining_debt,
    )
    assert math.isclose(
        details["property_equity"]["Fictional rental"][0],
        50_000 - remaining_debt,
    )
    assert details["property_rental_income"]["Fictional rental"][0] == 900
    assert details["property_operating_costs"]["Fictional rental"][0] == 200
    assert details["income_values"]["Rental receipts"][0] == 900
    assert details["expense_values"][
        "Fictional rental — property expenses (linked)"
    ][0] == 200
    assert math.isclose(details["bank_account"][0], expected_cash)
    assert details["investment_values"]["Fictional rental"][0] == 50_000
    assert math.isclose(net_worth[0], expected_cash + 50_000 - remaining_debt)


def test_property_operating_costs_include_annual_tax_in_charge_month():
    details = run_simulation(_linked_property_config())[2]

    assert details["property_operating_costs"]["Fictional rental"][:11] == [200] * 11
    assert details["property_operating_costs"]["Fictional rental"][11] == 1_400


def test_property_links_are_exact_unique_and_type_checked():
    with pytest.raises(ValueError, match="exactly one configured loan"):
        run_simulation(_linked_property_config(linked_loan_name="Missing mortgage"))
    with pytest.raises(ValueError, match="exactly one rent income"):
        run_simulation(_linked_property_config(rental_income_name="Missing rent"))
    with pytest.raises(ValueError, match="above 0 and at most 100"):
        run_simulation(_linked_property_config(ownership_percentage=0))

    config = _linked_property_config()
    config.investments.append(InvestmentConfig(
        "Second rental", "real_estate", 50_000, 0,
        linked_loan_name="Rental mortgage",
    ))
    with pytest.raises(ValueError, match="only one property"):
        run_simulation(config)


def test_property_position_configuration_round_trips_json():
    config = _linked_property_config()
    result = run_simulation(config)

    exported = create_simulation_export(config, *result)
    restored = read_simulation_export(exported)

    assert restored.investments == config.investments
    first_position = json.loads(exported)["results"]["monthly"][0][
        "property_positions"
    ]["Fictional rental"]
    assert first_position["market_value"] == 100_000
    assert first_position["owned_value"] == 50_000
    assert math.isclose(
        first_position["equity"],
        first_position["owned_value"] - first_position["attributable_debt"],
    )

    balances, flows = get_month_details(result[0], result[2], 1)
    assert any(
        row["Category"] == "Property equity"
        and row["Item"] == "Fictional rental"
        for row in balances
    )
    assert any(
        row["Category"] == "Property rental income (linked)"
        and row["Amount"] == 900
        for row in flows
    )


def test_purchased_property_reports_full_ownership_and_linked_mortgage_equity():
    config = SimulationConfig(
        years=1,
        initial_cash=30_000,
        yearly_fee=0,
        investments=[],
        incomes=[],
        expenses=[],
        loans=[],
        assumptions=_zero_real_estate_returns(),
        property_purchase=PropertyPurchase(
            100_000, 20, purchase_month=0, property_name="Future home",
            mortgage_name="Future mortgage",
        ),
    )

    details = run_simulation(config)[2]

    assert details["property_market_values"]["Future home"][0] == 100_000
    assert details["property_owned_values"]["Future home"][0] == 100_000
    assert details["property_debt_balances"]["Future home"][0] == 80_000
    assert details["property_equity"]["Future home"][0] == 20_000


def test_liability_share_scales_attributable_debt_and_net_worth():
    full_nw, _, full = run_simulation(_linked_property_config())
    share_nw, _, shared = run_simulation(
        _linked_property_config(liability_share_percentage=50)
    )
    name = "Fictional rental"
    full_debt = full["property_debt_balances"][name][0]
    assert math.isclose(shared["property_debt_balances"][name][0], full_debt / 2)
    assert math.isclose(
        shared["property_equity"][name][0],
        shared["property_owned_values"][name][0] - full_debt / 2,
    )
    assert math.isclose(share_nw[0] - full_nw[0], full_debt / 2, abs_tol=1e-6)


def test_invalid_liability_share_is_rejected():
    with pytest.raises(ValueError):
        run_simulation(_linked_property_config(liability_share_percentage=0))


def test_partial_ownership_without_loan_contributes_owned_value_only():
    net_worth, _, details = run_simulation(_linked_property_config(
        linked_loan_name=None, rental_income_name=None,
        monthly_expenses=0, yearly_tax=0,
    ))
    name = "Fictional rental"
    assert details["property_owned_values"][name][0] == 50_000
    assert details["property_debt_balances"][name][0] == 0
    assert details["property_equity"][name][0] == 50_000
    assert details["investment_values"][name][0] == 50_000
    remaining_loan = details["loan_balances"]["Rental mortgage"][0]
    assert math.isclose(
        net_worth[0], details["bank_account"][0] + 50_000 - remaining_loan
    )


def test_duplicate_rent_link_is_rejected_before_simulation():
    config = _linked_property_config()
    config.investments.append(InvestmentConfig(
        "Second rental", "real_estate", 50_000, 0,
        rental_income_name="Rental receipts",
    ))
    with pytest.raises(ValueError, match="only one property"):
        run_simulation(config)
