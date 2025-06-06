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
    """Withdraw succeeds when enough funds are available."""
    account = BankAccount(initial_balance=200)
    account.withdraw(50)
    assert account.subaccounts["Main"] == 150


def test_withdraw_insufficient_funds():
    """Withdraw raises when balance is too low."""
    account = BankAccount(initial_balance=50)
    with pytest.raises(InsufficientFundsException):
        account.withdraw(100)


def test_net_worth_over_one_month():
    """Net worth after the first month should reflect income, expense and investment."""
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

    # Month 1: 1000 start +1000 income -100 expense -100 investment +50 investment value
    assert net_worth[0] == 1850


def test_net_worth_over_six_months():
    """Check accumulated net worth in the middle of the year."""
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

    # After six months bank balance is 5800 and investment value 300
    assert net_worth[5] == 6100


def test_net_worth_over_one_year():
    """The final month applies an extra 10% investment contribution."""
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

    # Month 12 triggers an additional contribution leaving 9540 cash and 1610 in investments
    assert net_worth[11] == 11150

