import math

import pytest

from app import SimulationConfig, create_simulation_export, read_simulation_export, run_simulation
from bank_account import BankAccount, InsufficientFundsException
from property_purchase import PropertyPurchase, PropertyPurchaseError, execute_property_purchase


def test_purchase_debits_cash_and_adds_asset_and_mortgage_once():
    account = BankAccount(200_000)
    investments = []
    loans = []
    purchase = PropertyPurchase(100_000, 20, 500, 3, 25)

    event = execute_property_purchase(purchase, account, investments, loans)

    assert account.subaccounts["Main"] == 179_500
    assert len(investments) == 1
    assert investments[0].current_value == 100_000
    assert len(loans) == 1
    assert loans[0].initial_amount == 80_000
    assert event["mortgage_principal"] == 80_000
    assert account.total_balance() + investments[0].current_value - loans[0].remaining_amount == 199_500


def test_purchase_is_recorded_after_month_zero_and_mortgage_starts_next_month():
    config = SimulationConfig(
        years=1, initial_cash=200_000, investments=[], incomes=[], expenses=[], loans=[],
        property_purchase=PropertyPurchase(100_000, 20, 500, 0, 25, purchase_month=0),
    )

    net_worth, repayment_dates, details = run_simulation(config)

    assert details["purchase_fees"] == [500] + [0] * 11
    assert details["purchase_events"][0]["month"] == 0
    assert details["bank_account"][0] == 179_500
    assert details["investment_values"]["Purchased property"][0] == 100_000
    assert details["loan_balances"]["Property mortgage"][0] == 80_000
    assert details["loan_payments"]["Property mortgage"][0] == 0
    assert details["loan_payments"]["Property mortgage"][1] > 0
    assert math.isclose(net_worth[0], 199_500)
    assert repayment_dates == {}


def test_invalid_or_unaffordable_purchase_does_not_mutate_components():
    account = BankAccount(10_000)
    investments = []
    loans = []
    purchase = PropertyPurchase(100_000, 20, 1_000)

    with pytest.raises(InsufficientFundsException):
        execute_property_purchase(purchase, account, investments, loans)
    assert account.subaccounts["Main"] == 10_000
    assert investments == []
    assert loans == []

    with pytest.raises(PropertyPurchaseError):
        PropertyPurchase(100, 101).validate()


def test_purchase_configuration_round_trips_and_legacy_config_stays_disabled():
    config = SimulationConfig(
        initial_cash=200_000, investments=[], incomes=[], expenses=[], loans=[],
        property_purchase=PropertyPurchase(100_000, 20, 500),
    )
    result = run_simulation(config)
    restored = read_simulation_export(create_simulation_export(config, *result))
    assert restored.property_purchase == config.property_purchase

    legacy = read_simulation_export(create_simulation_export(SimulationConfig(), *run_simulation(SimulationConfig())))
    assert legacy.property_purchase is None
