import sys
import types
import random

# Provide a minimal numpy module so utils can be imported without the real dependency
sys.modules['numpy'] = types.ModuleType('numpy')

from utils import compute_net_worth_over_time
from bank_account import BankAccount

class DummyInvestment:
    def __init__(self):
        self.typology = 'stock'
        self.monthly_contribution = 0
        self.current_value = 0
        self.added_counts = 0
    def step(self):
        return None
    def add_contribution(self, amount):
        if amount:
            self.added_counts += 1
        # mimic small fee like real implementation
        self.current_value += amount - 50

class DummyIncome:
    def __init__(self, amount):
        self.amount = amount
    def step(self):
        return self.amount

class DummyExpense:
    def __init__(self, amount):
        self.amount = amount
    def step(self):
        return self.amount

def test_contribution_limit_and_variable_use():
    bank = BankAccount(initial_balance=20000)
    inv = DummyInvestment()
    income = DummyIncome(1000)
    expense = DummyExpense(100)

    net_worth, _, details = compute_net_worth_over_time(
        years=1,
        bank_account=bank,
        investments=[inv],
        loans=[],
        income_sources=[income],
        expenses=[expense]
    )

    assert inv.added_counts == 3
    assert all(
        value == cash + investments
        for value, cash, investments in zip(
            net_worth, details["bank_account"], details["investments"]
        )
    )


def test_contributions_reset_each_year():
    bank = BankAccount(initial_balance=20000)
    inv = DummyInvestment()
    income = DummyIncome(1000)
    expense = DummyExpense(100)

    compute_net_worth_over_time(
        years=2,
        bank_account=bank,
        investments=[inv],
        loans=[],
        income_sources=[income],
        expenses=[expense]
    )

    assert inv.added_counts == 6


def test_seed_does_not_change_caller_random_state():
    random.seed(1234)
    expected_next_value = random.random()

    random.seed(1234)
    bank = BankAccount(initial_balance=1000)
    compute_net_worth_over_time(
        years=1,
        bank_account=bank,
        investments=[],
        loans=[],
        income_sources=[],
        expenses=[],
        seed=42,
    )

    assert random.random() == expected_next_value

