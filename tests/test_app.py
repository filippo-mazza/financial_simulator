import pytest

from bank_account import InsufficientFundsException
from app import (DependentConfig, ExpenseConfig, IncomeConfig, InvestmentConfig,
                 LoanConfig, SimulationConfig, build_components, run_simulation)


def test_build_components_exposes_all_model_options():
    config = SimulationConfig(income_type="freelance", expense_type="unexpected",
                              investment_type="bonds", fixed_rate=True)
    account, investments, loans, incomes, expenses = build_components(config)
    assert account.total_balance() == config.initial_cash
    assert investments[0].typology == "bonds"
    assert loans[0].is_fixed_rate is True
    assert incomes[0].income_type == "freelance"
    assert expenses[0].expense_type == "unexpected"


def test_run_simulation_is_reproducible_and_isolated():
    config = SimulationConfig(years=2, seed=17)
    first = run_simulation(config)
    second = run_simulation(config)
    assert first == second
    assert len(first[0]) == 24
    assert set(first[2]) == {"investments", "bank_account"}


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
