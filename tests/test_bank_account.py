import sys
import types
import pytest

# Provide a minimal numpy module so utils can be imported without the real dependency
sys.modules['numpy'] = types.ModuleType('numpy')

from bank_account import BankAccount, InsufficientFundsException
from utils import compute_net_worth_over_time

class DummyInvestment:
    def __init__(self):
        self.typology = 'stock'
        self.monthly_contribution = 100
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


def test_withdraw_success():
    account = BankAccount(initial_balance=200)
    account.withdraw(50)
    assert account.subaccounts["Main"] == 150


def test_withdraw_insufficient_funds():
    account = BankAccount(initial_balance=50)
    with pytest.raises(InsufficientFundsException):
        account.withdraw(100)


def test_net_worth_over_one_month():
    bank = BankAccount(initial_balance=1000)
    inv = DummyInvestment()
    income = DummyIncome(1000)
    expense = DummyExpense(100)

    net_worth, _, _ = compute_net_worth_over_time(
        years=1,
        bank_account=bank,
        investments=[inv],
        loans=[],
        income_sources=[income],
        expenses=[expense],
    )

    assert net_worth[0] == 1850
