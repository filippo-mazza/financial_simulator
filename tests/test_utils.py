import sys
import types
import random

# Provide a minimal numpy module so utils can be imported without the real dependency
sys.modules['numpy'] = types.ModuleType('numpy')

from utils import compute_net_worth_over_time
from bank_account import BankAccount

class DummyInvestment:
    def __init__(self, auto_invest_enabled=False, typology='stock'):
        self.typology = typology
        self.monthly_contribution = 0
        self.current_value = 0
        self.added_counts = 0
        self.auto_invest_enabled = auto_invest_enabled
        self.auto_invest_min_cash = 10000
        self.auto_invest_percentage = 10
        self.auto_invest_max_per_year = 3
    def step(self):
        return None
    def add_contribution(self, amount):
        if amount:
            self.added_counts += 1
        # mimic small fee like real implementation
        self.current_value += amount - 50
        return 50

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

def test_automatic_contributions_are_disabled_by_default():
    bank = BankAccount(initial_balance=20000)
    inv = DummyInvestment()

    _, _, details = compute_net_worth_over_time(
        years=1, bank_account=bank, investments=[inv], loans=[],
        income_sources=[DummyIncome(1000)], expenses=[DummyExpense(100)],
    )

    assert inv.added_counts == 0
    assert details["automatic_contributions"]["Investment 1"] == [0] * 12
    assert details["automatic_contribution_fees"]["Investment 1"] == [0] * 12


def test_enabled_automatic_contributions_are_limited_and_reconcile():
    bank = BankAccount(initial_balance=20000)
    inv = DummyInvestment(auto_invest_enabled=True)
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
    assert details["automatic_contributions"]["Investment 1"][0] == 2090
    assert details["automatic_contribution_fees"]["Investment 1"][:3] == [50] * 3
    assert sum(details["automatic_contributions"]["Investment 1"][3:]) == 0
    assert details["bank_account"][0] == 18810
    assert details["investment_values"]["Investment 1"][0] == 2040
    assert 2090 == details["investment_values"]["Investment 1"][0] + 50
    assert all(
        value == cash + investments
        for value, cash, investments in zip(
            net_worth, details["bank_account"], details["investments"]
        )
    )


def test_legacy_contribution_method_cannot_make_a_small_sweep_negative():
    bank = BankAccount(initial_balance=40)
    inv = DummyInvestment(auto_invest_enabled=True)
    inv.auto_invest_min_cash = 0
    inv.auto_invest_max_per_year = 1

    net_worth, _, details = compute_net_worth_over_time(
        years=1, bank_account=bank, investments=[inv], loans=[],
        income_sources=[DummyIncome(100)], expenses=[DummyExpense(0)],
    )

    assert details["automatic_contributions"]["Investment 1"][0] == 14
    assert details["automatic_contribution_fees"]["Investment 1"][0] == 14
    assert details["investment_values"]["Investment 1"][0] == 0
    assert details["bank_account"][0] == 126
    assert net_worth[0] == 126


def test_contributions_reset_each_year():
    bank = BankAccount(initial_balance=20000)
    inv = DummyInvestment(auto_invest_enabled=True)
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


def test_automatic_contributions_ignore_ineligible_investments():
    bank = BankAccount(initial_balance=20000)
    inv = DummyInvestment(auto_invest_enabled=True, typology='bonds')

    compute_net_worth_over_time(
        years=1, bank_account=bank, investments=[inv], loans=[],
        income_sources=[DummyIncome(1000)], expenses=[DummyExpense(100)],
    )

    assert inv.added_counts == 0


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

