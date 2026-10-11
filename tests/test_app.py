from dataclasses import replace
from datetime import datetime, timezone
import json
import math

import pytest

from bank_account import InsufficientFundsException
from app import (_whole_amount, DependentConfig, ExpenseConfig, IncomeConfig, InvestmentConfig,
                 LoanConfig, SimulationConfig, build_components, build_simulation_events,
                 create_simulation_export, get_month_details, read_simulation_export,
                 run_simulation, run_simulations)


def test_build_components_exposes_all_model_options():
    config = SimulationConfig(income_type="freelance", expense_type="unexpected",
                              investment_type="bonds", fixed_rate=True)
    account, investments, loans, incomes, expenses = build_components(config)
    assert account.total_balance() == config.initial_cash
    assert investments[0].typology == "bonds"
    assert loans[0].is_fixed_rate is True
    assert incomes[0].income_type == "freelance"
    assert expenses[0].expense_type == "unexpected"


def test_major_ui_amounts_discard_cents():
    assert _whole_amount(5000.15) == 5000


def test_chart_events_include_school_starts_and_loan_repayments():
    config = SimulationConfig(dependents=[
        DependentConfig("Child 1", 300, age=5, school_start_age=6),
        DependentConfig("Child 2", 300, age=3, school_start_age=6),
        DependentConfig("Already enrolled", 300, age=7, school_start_age=6),
        DependentConfig("No school cost", 300, age=2, school_start_age=None),
    ])

    events = build_simulation_events(config, {"Home loan": 11}, months_simulated=48)

    assert events == [
        {"month": 12, "label": "Home loan repaid", "event_type": "Loan repaid"},
        {"month": 13, "label": "School starts for Child 1", "event_type": "School starts"},
        {"month": 37, "label": "School starts for Child 2", "event_type": "School starts"},
    ]


def test_run_simulation_is_reproducible_and_isolated():
    config = SimulationConfig(years=2, seed=17)
    first = run_simulation(config)
    second = run_simulation(config)
    assert first == second
    assert len(first[0]) == 24
    assert set(first[2]) == {
        "investments", "bank_account", "investment_values", "income_values",
        "expense_values", "loan_balances", "loan_payments",
    }


def test_multiple_simulations_return_monthly_averages():
    config = SimulationConfig(
        years=1, seed=20, initial_cash=5000, expenses=[], incomes=[], loans=[],
        investments=[InvestmentConfig("Stocks", "stock", 1000, 0)],
    )
    individual_runs = [run_simulation(replace(config, seed=seed)) for seed in (20, 21, 22)]

    averaged = run_simulations(config, 3)

    assert math.isclose(
        averaged[0][0], sum(run[0][0] for run in individual_runs) / 3,
    )
    assert math.isclose(
        averaged[2]["investments"][-1],
        sum(run[2]["investments"][-1] for run in individual_runs) / 3,
    )
    assert math.isclose(
        averaged[2]["investment_values"]["Stocks"][-1],
        sum(run[2]["investment_values"]["Stocks"][-1] for run in individual_runs) / 3,
    )
    assert averaged[1] == individual_runs[0][1]


def test_month_details_expose_individual_balances_and_flows():
    config = SimulationConfig(
        years=1, initial_cash=5000,
        investments=[InvestmentConfig("House", "real_estate", 1000, 0)],
        incomes=[IncomeConfig("Salary", "salary", 1000)],
        expenses=[ExpenseConfig("Groceries", "groceries", 200)],
        loans=[LoanConfig("Mortgage", 1200, 0, 1)],
    )
    net_worth, _, details = run_simulation(config)

    balances, flows = get_month_details(net_worth, details, month=1)

    assert any(row["Item"] == "House" for row in balances)
    assert any(row["Item"] == "Mortgage" for row in balances)
    assert any(row["Item"] == "Salary" for row in flows)
    assert any(row["Item"] == "Groceries" for row in flows)


def test_multiple_simulations_require_positive_count():
    with pytest.raises(ValueError, match="at least 1"):
        run_simulations(SimulationConfig(), 0)


def test_simulation_export_contains_parameters_and_monthly_results():
    config = SimulationConfig(
        years=1, seed=17, initial_cash=5000, investments=[], expenses=[],
        incomes=[], loans=[LoanConfig("Short loan", 1200, 0, 1)],
    )
    net_worth, repayment_dates, details = run_simulation(config)
    exported_at = datetime(2026, 10, 11, 12, 30, tzinfo=timezone.utc)

    document = json.loads(create_simulation_export(
        config, net_worth, repayment_dates, details, exported_at=exported_at,
    ))

    assert document["format"] == "financial-simulator-export"
    assert document["schema_version"] == 1
    assert document["results"]["simulation_count"] == 1
    assert document["exported_at_utc"] == "2026-10-11T12:30:00+00:00"
    assert document["parameters"]["seed"] == 17
    assert document["parameters"]["loans"][0]["name"] == "Short loan"
    assert document["results"]["summary"]["months_simulated"] == 12
    first_month = document["results"]["monthly"][0]
    assert first_month["month"] == 1
    assert first_month["net_worth"] == net_worth[0]
    assert first_month["cash"] == details["bank_account"][0]
    assert first_month["investments"] == details["investments"][0]
    assert first_month["loan_balances"]["Short loan"] == details["loan_balances"]["Short loan"][0]
    assert document["results"]["loan_repayments"] == [
        {"name": "Short loan", "month": repayment_dates["Short loan"] + 1}
    ]
    assert document["results"]["events"] == [
        {"month": repayment_dates["Short loan"] + 1,
         "label": "Short loan repaid", "event_type": "Loan repaid"}
    ]


def test_simulation_export_round_trip_restores_nested_parameters():
    config = SimulationConfig(
        years=7, seed=99, initial_cash=1234, yearly_fee=25,
        investments=[InvestmentConfig("Bonds", "bonds", 800, 75)],
        incomes=[IncomeConfig("Consulting", "freelance", 2500, 20, 5)],
        expenses=[ExpenseConfig("Rent", "housing", 900)],
        loans=[LoanConfig("Car", 5000, 3.5, 4, True, 500)],
        dependents=[DependentConfig("Child", 300, 4, 6, 200)],
    )
    results = run_simulation(config)

    restored = read_simulation_export(create_simulation_export(config, *results))

    assert restored == config


@pytest.mark.parametrize("document, message", [
    ("not JSON", "not valid UTF-8 JSON"),
    ('{"format": "something-else", "schema_version": 1}',
     "not a Financial Simulator export"),
    ('{"format": "financial-simulator-export", "schema_version": 99, "parameters": {}}',
     "version is not supported"),
])
def test_simulation_import_rejects_invalid_exports(document, message):
    with pytest.raises(ValueError, match=message):
        read_simulation_export(document)


def test_current_situation_items_become_model_expenses():
    config = SimulationConfig(
        years=1,
        additional_expenses=[ExpenseConfig("Gym", "healthcare", 50)],
        dependents=[DependentConfig("Daughter", 300)],
    )
    _, _, _, _, expenses = build_components(config)
    assert [expense.name for expense in expenses] == [
        "Living expenses", "Gym", "Daughter (child)"
    ]
    assert sum(expense.monthly_expense for expense in expenses) == 1850


def test_investments_are_configurable_and_can_be_empty():
    config = SimulationConfig(investments=[
        InvestmentConfig("Stocks", "stock", 1000, 50),
        InvestmentConfig("Bonds", "bonds", 500, 0),
    ])
    _, investments, _, _, _ = build_components(config)
    assert [investment.name for investment in investments] == ["Stocks", "Bonds"]

    empty = SimulationConfig(years=1, initial_cash=10000, investments=[])
    _, investments, _, _, _ = build_components(empty)
    assert investments == []
    result = run_simulation(empty)
    assert len(result[0]) == 12


def test_zero_collections_preserve_initial_cash_and_result_length():
    config = SimulationConfig(
        years=2, initial_cash=1250, yearly_fee=0, investments=[], incomes=[], expenses=[], loans=[]
    )
    result = run_simulation(config)
    assert len(result[0]) == 24
    assert result[0][0] == 1250
    assert result[0][-1] == 1250
    assert result[2]["investments"] == [0] * 24
    assert result[2]["bank_account"] == [1250] * 24


def test_yearly_bank_fee_reduces_cash_and_net_worth():
    config = SimulationConfig(
        years=2, initial_cash=1000, yearly_fee=110,
        investments=[], incomes=[], expenses=[], loans=[],
    )
    result = run_simulation(config)
    assert result[2]["bank_account"][10] == 1000
    assert result[2]["bank_account"][11] == 890
    assert result[0][11] == 890
    assert result[0][-1] == 780


def test_zero_stock_contribution_skips_transaction(monkeypatch):
    calls = []

    def record_contribution(self, amount):
        calls.append(amount)

    monkeypatch.setattr("investment.Investment.add_contribution", record_contribution)
    config = SimulationConfig(
        years=1, initial_cash=5000, yearly_fee=0,
        investments=[InvestmentConfig("Stocks", "stock", 1000, 0)],
        incomes=[], expenses=[], loans=[],
    )
    run_simulation(config)
    assert calls == []


def test_zero_interest_variable_rate_loan_is_interest_free():
    config = SimulationConfig(
        years=1, initial_cash=5000, yearly_fee=0,
        investments=[], incomes=[], expenses=[],
        loans=[LoanConfig("Interest-free loan", 1200, 0, 1)],
    )
    result = run_simulation(config)
    assert result[0][0] == 3800
    assert result[1] == {"Interest-free loan": 11}


def test_other_income_uses_default_taxation():
    config = SimulationConfig(
        years=1, initial_cash=0, yearly_fee=0, investments=[], expenses=[], loans=[],
        incomes=[IncomeConfig("Other", "other", 100)],
    )
    result = run_simulation(config)
    assert result[2]["bank_account"][0] == 85


def test_non_stock_contributions_are_applied():
    config = SimulationConfig(
        years=1, initial_cash=5000, yearly_fee=0,
        investments=[InvestmentConfig("Property", "real_estate", 1000, 100)],
        incomes=[], expenses=[], loans=[],
    )
    result = run_simulation(config)
    assert result[2]["bank_account"][0] == 4900


def test_loan_rejects_overpayment():
    config = SimulationConfig(
        years=1, initial_cash=5000, investments=[], incomes=[], expenses=[],
        loans=[LoanConfig("Invalid", 1000, 2, 10, already_paid=1001)],
    )
    with pytest.raises(ValueError, match="already_paid"):
        run_simulation(config)


def test_catastrophic_scenario_stops_when_cash_cannot_cover_obligations():
    config = SimulationConfig(
        years=5, initial_cash=100, yearly_fee=0,
        investments=[], incomes=[],
        expenses=[ExpenseConfig("Emergency care", "healthcare", 1000)],
        loans=[LoanConfig("Large debt", 50000, 2, 25)],
    )
    with pytest.raises(InsufficientFundsException, match=r"month 1.*expense 'Emergency care'.*\\\$1,000\.00.*\\\$100\.00"):
        run_simulation(config)


def test_multiple_collections_have_expected_first_month_result():
    config = SimulationConfig(
        years=1, initial_cash=5000, investments=[],
        incomes=[IncomeConfig("Salary", "salary", 1000, tax_rate=10)],
        expenses=[ExpenseConfig("Rent", "housing", 200)],
        loans=[LoanConfig("Debt", 1200, 0, 1, fixed_rate=True)],
    )
    result = run_simulation(config)
    # Cash is 5,600; net worth also subtracts the loan's 1,100 balance.
    assert result[0][0] == 4500
    assert result[2]["investments"][-1] == 0


def test_child_school_cost_starts_at_configured_age():
    config = SimulationConfig(
        years=7, initial_cash=100000, investments=[], incomes=[], expenses=[], loans=[],
        dependents=[DependentConfig("Child", 100, age=5, school_start_age=6,
                                   school_monthly_cost=400)],
    )
    _, _, _, _, expenses = build_components(config)
    child = expenses[0]
    assert child.step() == 100
    for _ in range(11):
        child.step()
    assert child.current_age == 6
    assert child.monthly_expense == 502
    assert child.step() == 502
