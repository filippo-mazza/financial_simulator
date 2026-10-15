import math
from dataclasses import replace

from app import (
    ExpenseConfig, IncomeConfig, InvestmentConfig, LoanConfig, SimulationConfig,
    run_simulation,
)
from bank_account import BankAccount
from utils import compute_net_worth_over_time


class DeterministicInvestment:
    name = "Fund"
    typology = "stock"
    monthly_contribution = 100
    contribution_fee = 10
    auto_invest_enabled = True
    auto_invest_min_cash = 0
    auto_invest_percentage = 10
    auto_invest_max_per_year = 2

    def __init__(self):
        self.current_value = 1000

    def step(self):
        self.current_value += 20
        return 5

    def add_contribution(self, amount):
        fee = min(self.contribution_fee, amount)
        self.current_value += amount - fee
        return fee


class DeterministicIncome:
    name = "Salary"
    monthly_income = 500

    def step(self):
        return self.monthly_income


class DeterministicExpense:
    name = "Living costs"

    def step(self):
        return 100


class DeterministicLoan:
    name = "Short loan"

    def __init__(self):
        self.remaining_amount = 600
        self.is_repaid = False

    def step(self):
        if self.is_repaid:
            return None
        self.remaining_amount -= 50
        if self.remaining_amount <= 0:
            self.remaining_amount = 0
            self.is_repaid = True
        return 50


def test_every_month_reconciles_cash_assets_liabilities_and_transfers():
    starting_cash = 10_000
    starting_investment = 1_000
    yearly_bank_fee = 24
    investment = DeterministicInvestment()

    net_worth, repayment_dates, details = compute_net_worth_over_time(
        years=1,
        bank_account=BankAccount(starting_cash, yearly_bank_fee),
        investments=[investment],
        loans=[DeterministicLoan()],
        income_sources=[DeterministicIncome()],
        expenses=[DeterministicExpense()],
        seed=123,
    )

    previous_cash = starting_cash
    previous_investment = starting_investment
    for month in range(12):
        income = sum(values[month] for values in details["income_values"].values())
        expenses = sum(values[month] for values in details["expense_values"].values())
        loan_payments = sum(values[month] for values in details["loan_payments"].values())
        automatic_transfer = sum(
            values[month] for values in details["automatic_contributions"].values()
        )
        automatic_fee = sum(
            values[month] for values in details["automatic_contribution_fees"].values()
        )

        # The fixture pays a $5 dividend, transfers a regular gross $100, and
        # charges its $10 fee inside the investment. The annual bank fee occurs
        # after the twelfth month's other transactions.
        expected_cash = (
            previous_cash + income - expenses - loan_payments + 5 - 100
            - automatic_transfer - (yearly_bank_fee if month == 11 else 0)
        )
        expected_investment = (
            previous_investment + 20 + 100 - 10
            + automatic_transfer - automatic_fee
        )
        loan_balance = sum(
            values[month] for values in details["loan_balances"].values()
        )

        assert math.isclose(details["bank_account"][month], expected_cash)
        assert math.isclose(details["investments"][month], expected_investment)
        assert math.isclose(net_worth[month], (
            details["bank_account"][month]
            + details["investments"][month]
            - loan_balance
        ))
        assert details["bank_account"][month] >= 0
        assert net_worth[month] >= 0
        previous_cash = details["bank_account"][month]
        previous_investment = details["investments"][month]

    assert repayment_dates == {"Short loan": 11}
    assert details["loan_balances"]["Short loan"][-1] == 0
    assert details["automatic_contributions"]["Fund"][0] > 0
    assert details["automatic_contributions"]["Fund"][1] > 0
    assert details["automatic_contributions"]["Fund"][2:] == [0] * 10


def test_equivalent_stochastic_scenarios_are_fully_reproducible_with_fixed_seed():
    config = SimulationConfig(
        years=2,
        seed=8675309,
        initial_cash=100_000,
        yearly_fee=25,
        investments=[InvestmentConfig("Stocks", "stock", 5_000, 100)],
        incomes=[IncomeConfig("Consulting", "freelance", 3_000, bonus_pct=10)],
        expenses=[ExpenseConfig("Emergencies", "unexpected", 0)],
        loans=[LoanConfig("Short loan", 2_400, 3, 1)],
    )

    first = run_simulation(config)
    second = run_simulation(config)
    different_seed = run_simulation(replace(config, seed=config.seed + 1))

    assert first == second
    assert first != different_seed
    assert len(first[0]) == 24
    assert first[2]["investment_values"]["Stocks"]
    assert first[2]["income_values"]["Consulting"]
    assert first[2]["expense_values"]["Emergencies"]
    assert first[2]["loan_balances"]["Short loan"][-1] == 0
