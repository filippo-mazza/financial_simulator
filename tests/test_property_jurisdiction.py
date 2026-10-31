import json
import math
from dataclasses import replace

import pytest

from app import (
    ExpenseConfig,
    IncomeConfig,
    InvestmentConfig,
    LoanConfig,
    SimulationConfig,
    create_simulation_export,
    read_simulation_export,
    resolved_property_purchase,
    run_simulation,
)
from financial_assumptions import FinancialAssumptions
from property_jurisdiction import (
    PropertyJurisdictionProfile,
    estimate_property_costs,
    profiles_by_name,
)
from property_purchase import AutomaticPurchasePolicy, PropertyPurchase


def _zero_property_returns():
    defaults = FinancialAssumptions()
    means = dict(defaults.investment_return_means)
    deviations = dict(defaults.investment_return_std_devs)
    means["real_estate"] = 0
    deviations["real_estate"] = 0
    return FinancialAssumptions(
        investment_return_means=means,
        investment_return_std_devs=deviations,
    )


@pytest.mark.parametrize((
    "profile", "value", "rent", "ownership", "expected",
), [
    (
        PropertyJurisdictionProfile("Fictional North", 5, 1, 1.2, 10),
        200_000, 1_000, 50, (10_000, 1_000, 100, 100, 200),
    ),
    (
        PropertyJurisdictionProfile("Fictional South", 2, 0.5, 2.4, 20),
        100_000, 500, 25, (2_000, 125, 50, 100, 150),
    ),
])
def test_profile_estimates_scale_value_costs_by_ownership_but_not_user_rent(
        profile, value, rent, ownership, expected):
    estimate = estimate_property_costs(profile, value, rent, ownership)

    assert (
        estimate.acquisition_costs,
        estimate.annual_property_tax,
        estimate.monthly_maintenance,
        estimate.monthly_rental_expenses,
        estimate.monthly_operating_costs,
    ) == expected


def test_profiles_validate_editable_rates_and_unique_names():
    first = PropertyJurisdictionProfile("Fictional A", 1, 2, 3, 4)
    second = PropertyJurisdictionProfile("Fictional B", 4, 3, 2, 1)

    assert set(profiles_by_name([first, second])) == {"Fictional A", "Fictional B"}
    with pytest.raises(ValueError, match="unique"):
        profiles_by_name([first, first])
    with pytest.raises(ValueError, match="between 0 and 100"):
        PropertyJurisdictionProfile("Fictional C", annual_property_tax_percentage=101)


def test_selected_profile_adds_existing_property_costs_once():
    profile = PropertyJurisdictionProfile(
        "Fictional Rental District", 5, 1, 1.2, 10,
    )
    config = SimulationConfig(
        years=2,
        initial_cash=10_000,
        yearly_fee=0,
        investments=[InvestmentConfig(
            "Synthetic rental", "real_estate", 200_000, 0,
            monthly_expenses=20,
            yearly_tax=120,
            ownership_percentage=50,
            rental_income_name="Synthetic rent",
            jurisdiction_profile_name=profile.name,
        )],
        incomes=[IncomeConfig("Synthetic rent", "rent", 1_000, 0)],
        expenses=[],
        loans=[],
        assumptions=_zero_property_returns(),
        jurisdiction_profiles=[profile],
    )

    details = run_simulation(config)[2]
    costs = details["property_operating_costs"]["Synthetic rental"]

    assert all(math.isclose(amount, 220) for amount in costs[:11])
    assert math.isclose(costs[11], 1_340)
    assert math.isclose(costs[12], 223.3)
    assert math.isclose(costs[22], 223.3)
    assert math.isclose(costs[23], 1_360.1)
    monthly_expenses = details["expense_values"][
        "Synthetic rental — property expenses (linked)"
    ]
    assert all(math.isclose(amount, 220) for amount in monthly_expenses[:12])
    assert all(
        math.isclose(amount, 223.3) for amount in monthly_expenses[12:]
    )
    yearly_tax = details["expense_values"][
        "Synthetic rental — property tax (linked)"
    ]
    assert yearly_tax[:11] == [0] * 11
    assert math.isclose(yearly_tax[11], 1_120)
    assert yearly_tax[12:23] == [0] * 11
    assert math.isclose(yearly_tax[23], 1_136.8)


def test_unassigned_profile_preserves_legacy_simulation_series():
    legacy_config = SimulationConfig(
        years=2,
        seed=1729,
        initial_cash=20_000,
        yearly_fee=0,
        investments=[
            InvestmentConfig(
                "Synthetic home", "real_estate", 200_000, 0,
                monthly_expenses=20,
                yearly_tax=120,
                ownership_percentage=50,
                linked_loan_name="Synthetic mortgage",
                rental_income_name="Synthetic rent",
            ),
            InvestmentConfig("Synthetic stocks", "stock", 5_000, 100),
        ],
        incomes=[IncomeConfig("Synthetic rent", "rent", 1_000, 0)],
        expenses=[ExpenseConfig("Living costs", "utilities", 500)],
        loans=[LoanConfig("Synthetic mortgage", 40_000, 2, 10)],
        assumptions=_zero_property_returns(),
    )
    profile_config = replace(
        legacy_config,
        jurisdiction_profiles=[
            PropertyJurisdictionProfile("Unassigned profile", 5, 1, 1.2, 10)
        ],
    )

    legacy_result = run_simulation(legacy_config)
    profile_result = run_simulation(profile_config)

    assert legacy_result[0] == profile_result[0]
    assert legacy_result[1] == profile_result[1]
    for key in ("bank_account", "investment_values", "loan_balances"):
        assert legacy_result[2][key] == profile_result[2][key]


def test_seeded_profile_estimates_and_purchase_outcome_are_reproducible():
    profile = PropertyJurisdictionProfile(
        "Fictional Reproducible District", 5, 1, 1.2, 10,
    )
    config = SimulationConfig(
        years=2,
        seed=8675309,
        initial_cash=80_000,
        yearly_fee=0,
        investments=[
            InvestmentConfig(
                "Synthetic rental", "real_estate", 200_000, 0,
                monthly_expenses=20,
                yearly_tax=120,
                ownership_percentage=50,
                rental_income_name="Synthetic rent",
                jurisdiction_profile_name=profile.name,
            ),
            InvestmentConfig("Synthetic stocks", "stock", 5_000, 100),
        ],
        incomes=[IncomeConfig("Synthetic rent", "rent", 1_000, 0)],
        expenses=[],
        loans=[],
        assumptions=_zero_property_returns(),
        property_purchase=AutomaticPurchasePolicy(
            PropertyPurchase(
                100_000, 20, transaction_fees=1_000,
                monthly_expenses=10, yearly_tax=100,
            ),
            minimum_available_cash=10_000,
            minimum_cash_reserve=2_000,
        ),
        jurisdiction_profiles=[profile],
        property_purchase_jurisdiction_profile=profile.name,
    )

    first = run_simulation(config)
    second = run_simulation(config)

    assert first == second
    assert first[2]["purchase_outcome"]["purchased"] is True
    assert first[2]["purchase_events"][0]["transaction_fees"] == 6_000
    assert math.isclose(
        first[2]["property_operating_costs"]["Synthetic rental"][0], 220
    )


def test_purchase_profile_resolves_to_dollar_costs_without_mutating_input():
    profile = PropertyJurisdictionProfile(
        "Fictional Purchase District", 5, 1, 1.2, 10,
    )
    purchase = PropertyPurchase(
        100_000, 20, transaction_fees=1_000,
        monthly_expenses=10, yearly_tax=100,
    )
    policy = AutomaticPurchasePolicy(purchase, 20_000, 2_000)
    config = SimulationConfig(
        property_purchase=policy,
        property_purchase_jurisdiction_profile=profile.name,
        jurisdiction_profiles=[profile],
    )

    resolved = resolved_property_purchase(config)

    assert resolved.purchase.transaction_fees == 6_000
    assert resolved.purchase.monthly_expenses == 110
    assert resolved.purchase.yearly_tax == 1_100
    assert resolved.minimum_required_cash == 28_000
    assert purchase.transaction_fees == 1_000


def test_jurisdiction_configuration_round_trips_and_legacy_export_defaults_empty():
    profile = PropertyJurisdictionProfile("Fictional Export", 2, 0.5, 1, 5)
    config = SimulationConfig(
        years=1,
        investments=[], incomes=[], expenses=[], loans=[],
        jurisdiction_profiles=[profile],
    )
    result = run_simulation(config)

    exported = create_simulation_export(config, *result)
    restored = read_simulation_export(exported)
    legacy = json.loads(exported)
    del legacy["parameters"]["jurisdiction_profiles"]
    del legacy["parameters"]["property_purchase_jurisdiction_profile"]

    assert restored.jurisdiction_profiles == [profile]
    assert read_simulation_export(json.dumps(legacy)).jurisdiction_profiles == []


def test_unknown_selected_profile_is_rejected_before_simulation():
    config = SimulationConfig(
        investments=[InvestmentConfig(
            "Synthetic home", "real_estate", 100_000, 0,
            jurisdiction_profile_name="Missing profile",
        )],
        incomes=[], expenses=[], loans=[],
    )

    with pytest.raises(ValueError, match="Unknown property jurisdiction profile"):
        run_simulation(config)
