import math

import pytest

from app import (SimulationConfig, automatic_purchase_warning,
                 create_simulation_export, purchase_outcome_from_details, read_simulation_export,
                 run_simulation, run_simulation_batch)
from bank_account import BankAccount, InsufficientFundsException
from expense import Expense
from income_source import IncomeSource
from loan import Loan
from property_purchase import (AutomaticPurchasePolicy,
                               MortgageAffordabilityPolicy, PropertyPurchase,
                               PropertyPurchaseError,
                               evaluate_mortgage_affordability,
                               execute_property_purchase)
from utils import compute_net_worth_over_time


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


def test_delayed_purchase_pads_detail_series_and_exports():
    config = SimulationConfig(
        years=1, initial_cash=200_000, investments=[], incomes=[], expenses=[], loans=[],
        property_purchase=PropertyPurchase(100_000, 20, 500, 0, 25, purchase_month=3),
    )

    result = run_simulation(config)
    details = result[2]

    assert details["investment_values"]["Purchased property"][:3] == [0, 0, 0]
    assert details["loan_balances"]["Property mortgage"][:3] == [0, 0, 0]
    assert details["loan_payments"]["Property mortgage"][:3] == [0, 0, 0]
    assert len(details["investment_values"]["Purchased property"]) == 12
    assert len(details["loan_balances"]["Property mortgage"]) == 12
    create_simulation_export(config, *result)


def test_purchase_uses_scenario_real_estate_return_assumptions():
    means = dict(SimulationConfig().assumptions.investment_return_means)
    means["real_estate"] = 0
    std_devs = dict(SimulationConfig().assumptions.investment_return_std_devs)
    std_devs["real_estate"] = 0
    config = SimulationConfig(
        years=1, initial_cash=200_000, investments=[], incomes=[], expenses=[], loans=[],
        assumptions=replace_assumptions(means, std_devs),
        property_purchase=PropertyPurchase(100_000, 20, purchase_month=0),
    )

    result = run_simulation(config)
    assert result[2]["investment_values"]["Purchased property"][-1] == 100_000


def replace_assumptions(means, std_devs):
    from financial_assumptions import FinancialAssumptions
    return FinancialAssumptions(
        investment_return_means=means,
        investment_return_std_devs=std_devs,
    )


def test_automatic_purchase_uses_threshold_equality_and_preserves_reserve():
    policy = AutomaticPurchasePolicy(PropertyPurchase(100_000, 20, 500), 30_500, 10_000)
    result = compute_net_worth_over_time(
        1, BankAccount(30_500), [], [], [], [], property_purchase=policy,
    )
    details = result[2]
    assert details["purchase_outcome"]["purchased"] is True
    assert details["purchase_outcome"]["purchase_month"] == 0
    assert details["bank_account"][0] == 10_000
    assert len(details["purchase_events"]) == 1


def test_automatic_purchase_ends_selected_rent_in_closing_month():
    policy = AutomaticPurchasePolicy(PropertyPurchase(100_000, 20, 500), 30_500, 10_000)
    rent = Expense("Rent", "housing", 1_000)
    result = compute_net_worth_over_time(
        1, BankAccount(30_500), [], [], [], [rent], property_purchase=policy,
        selected_rent_expense="Rent",
    )
    assert result[2]["expense_values"]["Rent"] == [0] * 12
    assert result[2]["purchase_outcome"]["purchased"] is True
    assert result[2]["bank_account"][0] == 10_000
    assert result[2]["purchase_outcome"]["minimum_cash_reserve"] == 10_000
    assert result[2]["purchase_outcome"]["cash_after_purchase"] == 10_000


def test_linked_property_expense_name_collision_does_not_overwrite_history():
    policy = PropertyPurchase(
        100_000, 20, 500, purchase_month=1,
        monthly_expenses=100, property_name="House",
    )
    conflicting = Expense("House — property expenses (linked)", "housing", 25)
    with pytest.raises(ValueError, match="already exists"):
        compute_net_worth_over_time(
            1, BankAccount(30_500), [], [], [], [conflicting],
            property_purchase=policy,
        )


def test_automatic_purchase_waits_when_fees_and_reserve_are_not_covered():
    policy = AutomaticPurchasePolicy(PropertyPurchase(100_000, 20, 500), 20_000, 10_000)
    result = compute_net_worth_over_time(
        1, BankAccount(20_000), [], [], [], [], property_purchase=policy,
    )
    assert result[2]["purchase_outcome"]["purchased"] is False
    assert result[2]["purchase_outcome"]["purchase_month"] is None
    assert result[2]["purchase_events"] == []


def test_automatic_purchase_defers_after_monthly_costs_without_stopping_simulation():
    policy = AutomaticPurchasePolicy(
        PropertyPurchase(250_000, 20, 5_000), 50_000, 0,
    )
    groceries = Expense("Groceries", "groceries", 1_000)

    net_worth, _, details = compute_net_worth_over_time(
        1, BankAccount(55_000), [], [], [], [groceries], property_purchase=policy,
    )

    assert policy.minimum_required_cash == 55_000
    assert len(net_worth) == 12
    assert details["purchase_outcome"]["purchased"] is False
    assert details["purchase_events"] == []
    assert details["expense_values"]["Groceries"] == [1_000] * 12
    assert details["bank_account"][0] == 54_000
    warning = automatic_purchase_warning(policy, 1, 1)
    assert "$55,000 after monthly commitments" in warning
    assert "continued without the purchase" in warning


def test_fixed_purchase_still_rejects_insufficient_closing_cash():
    with pytest.raises(InsufficientFundsException, match="property purchase"):
        compute_net_worth_over_time(
            1, BankAccount(54_999), [], [], [], [],
            property_purchase=PropertyPurchase(250_000, 20, 5_000),
        )


def test_automatic_purchase_is_independent_per_trajectory():
    config = SimulationConfig(
        years=1, initial_cash=0, monthly_income=1_000, monthly_expense=0,
        property_purchase=AutomaticPurchasePolicy(PropertyPurchase(5_000, 20), 1_000, 0),
    )
    batch = run_simulation_batch(config, 2)
    outcomes = [trajectory.details["purchase_outcome"] for trajectory in batch.trajectories]
    assert all(outcome["purchased"] for outcome in outcomes)
    assert all(0 <= outcome["purchase_month"] < 12 for outcome in outcomes)
    assert len({outcome["purchase_month"] for outcome in outcomes}) == 1
    assert "2 of 2 trajectories" in (
        batch.average_result[2]["purchase_events"][0]["label"]
    )


def test_purchase_outcome_normalizes_scheduled_purchase_events():
    assert purchase_outcome_from_details({"purchase_events": [{"month": 7}]}) == {
        "purchased": True, "purchase_month": 7,
    }
    assert purchase_outcome_from_details({"purchase_events": []}) is None


def test_purchase_outcome_prefers_automatic_outcome():
    outcome = {"purchased": False, "purchase_month": None}
    assert purchase_outcome_from_details({
        "purchase_outcome": outcome, "purchase_events": [{"month": 7}],
    }) == outcome


def test_fully_paid_purchase_creates_no_mortgage():
    account = BankAccount(120_000)
    investments = []
    loans = []

    event = execute_property_purchase(
        PropertyPurchase(100_000, 100, 250), account, investments, loans
    )

    assert account.subaccounts["Main"] == 19_750
    assert len(investments) == 1
    assert loans == []
    assert event["mortgage_principal"] == 0


def test_automatic_no_purchase_preserves_selected_rent():
    policy = AutomaticPurchasePolicy(PropertyPurchase(100_000, 20), 50_000, 0)
    rent = Expense("Rent", "housing", 500)
    result = compute_net_worth_over_time(
        1, BankAccount(10_000), [], [], [], [rent], property_purchase=policy,
        selected_rent_expense="Rent",
    )

    assert result[2]["purchase_outcome"]["purchased"] is False
    assert result[2]["expense_values"]["Rent"] == [500] * 12


def test_purchase_ends_only_selected_rent_and_starts_linked_costs():
    policy = PropertyPurchase(
        100_000, 20, purchase_month=1, monthly_expenses=300, property_name="House"
    )
    rent = Expense("Rent", "housing", 500)
    groceries = Expense("Groceries", "groceries", 200)
    result = compute_net_worth_over_time(
        1, BankAccount(30_000), [], [], [], [rent, groceries],
        property_purchase=policy, selected_rent_expense="Rent",
    )
    details = result[2]

    assert details["expense_values"]["Rent"] == [500, 0] + [0] * 10
    assert details["expense_values"]["Groceries"] == [200] * 12
    assert details["expense_values"]["House — property expenses (linked)"][0] == 0
    assert details["expense_values"]["House — property expenses (linked)"][1] == 0
    assert details["expense_values"]["House — property expenses (linked)"][2:] == [300] * 10


def test_purchase_records_configured_annual_property_tax():
    policy = PropertyPurchase(
        100_000, 20, purchase_month=0, yearly_tax=1_200, property_name="House"
    )
    result = compute_net_worth_over_time(
        2, BankAccount(30_000), [], [], [], [], property_purchase=policy
    )

    taxes = result[2]["expense_values"]["House — property tax (linked)"]
    assert taxes[:12] == [0] * 12
    assert taxes[12] == 1_200


def test_mortgage_balance_decreases_after_repayment_begins():
    result = compute_net_worth_over_time(
        1, BankAccount(30_000), [], [], [], [],
        property_purchase=PropertyPurchase(100_000, 20, purchase_month=0),
    )
    balances = result[2]["loan_balances"]["Property mortgage"]

    assert balances[0] == 80_000
    assert balances[1] < balances[0]


def test_affordability_counts_existing_debt_and_new_mortgage_once():
    purchase = PropertyPurchase(100_000, 20, mortgage_duration_years=25)
    policy = MortgageAffordabilityPolicy(
        max_debt_service_ratio_pct=40,
        qualifying_income_names=("First salary",),
    )

    assessment = evaluate_mortgage_affordability(
        purchase,
        policy,
        [IncomeSource("First salary", "salary", 3_000, 0, 0)],
        [Loan("Existing loan", 12_000, 0, 1)],
    )

    assert assessment["eligible"] is False
    assert assessment["existing_monthly_debt_payments"] == 1_000
    assert math.isclose(assessment["proposed_mortgage_payment"], 800 / 3)
    assert math.isclose(assessment["total_monthly_debt_payments"], 3_800 / 3)
    assert math.isclose(assessment["debt_service_ratio_pct"], 380 / 9)
    assert "above the configured 40.0% limit" in assessment["reason"]


def test_second_selected_income_can_make_mortgage_affordable():
    purchase = PropertyPurchase(100_000, 20, mortgage_duration_years=25)
    policy = MortgageAffordabilityPolicy(
        max_debt_service_ratio_pct=40,
        qualifying_income_names=("First salary", "Second salary"),
    )

    assessment = evaluate_mortgage_affordability(
        purchase,
        policy,
        [
            IncomeSource("First salary", "salary", 3_000, 0, 0),
            IncomeSource("Second salary", "salary", 2_000, 0, 0),
        ],
        [Loan("Existing loan", 12_000, 0, 1)],
    )

    assert assessment["eligible"] is True
    assert assessment["qualifying_monthly_income"] == 5_000
    assert assessment["qualifying_income_names"] == [
        "First salary", "Second salary",
    ]


def test_dependent_living_buffer_can_block_otherwise_affordable_mortgage():
    policy = MortgageAffordabilityPolicy(
        max_debt_service_ratio_pct=50,
        qualifying_income_names=("Salary",),
        dependent_count=2,
        monthly_buffer_per_dependent=1_000,
    )

    assessment = evaluate_mortgage_affordability(
        PropertyPurchase(100_000, 20, mortgage_duration_years=25),
        policy,
        [IncomeSource("Salary", "salary", 2_000, 0, 0)],
        [],
    )

    assert assessment["debt_service_ratio_pct"] < 50
    assert assessment["monthly_living_buffer"] == 2_000
    assert assessment["eligible"] is False
    assert "below the configured household buffer" in assessment["reason"]


def test_annual_bonus_is_excluded_and_variable_income_uses_explicit_percentage():
    policy = MortgageAffordabilityPolicy(
        qualifying_income_names=("Salary", "Freelance"),
        variable_income_inclusion_pct=50,
    )

    assessment = evaluate_mortgage_affordability(
        PropertyPurchase(100_000, 100),
        policy,
        [
            IncomeSource("Salary", "salary", 3_000, 0, 100),
            IncomeSource("Freelance", "freelance", 2_000, 0, 0),
        ],
        [],
    )

    assert assessment["qualifying_monthly_income"] == 4_000
    assert assessment["eligible"] is True


def test_configured_bonus_does_not_increase_qualifying_income():
    policy = MortgageAffordabilityPolicy(
        qualifying_income_names=("Salary",),
    )
    purchase = PropertyPurchase(100_000, 20)

    without_bonus = evaluate_mortgage_affordability(
        purchase, policy,
        [IncomeSource("Salary", "salary", 3_000, 30, 0)], [],
    )
    with_bonus = evaluate_mortgage_affordability(
        purchase, policy,
        [IncomeSource("Salary", "salary", 3_000, 30, 100)], [],
    )

    assert without_bonus["qualifying_monthly_income"] == 2_100
    assert with_bonus["qualifying_monthly_income"] == 2_100
    assert with_bonus["annual_bonus_treatment"] == "excluded"
    assert with_bonus["debt_service_ratio_pct"] == (
        without_bonus["debt_service_ratio_pct"]
    )


def test_month_twelve_bonus_payment_does_not_change_affordability_income():
    affordability = MortgageAffordabilityPolicy(
        max_debt_service_ratio_pct=5,
        qualifying_income_names=("Salary",),
    )
    result = compute_net_worth_over_time(
        1,
        BankAccount(100_000),
        [],
        [],
        [IncomeSource("Salary", "salary", 3_000, 0, 100)],
        [],
        seed=42,
        property_purchase=AutomaticPurchasePolicy(
            PropertyPurchase(100_000, 20, mortgage_duration_years=25),
            20_000,
            0,
            affordability,
        ),
    )
    details = result[2]

    assert details["income_values"]["Salary"][11] > 3_000
    assert details["purchase_outcome"]["purchased"] is False
    assert (
        details["purchase_outcome"]["affordability"]["qualifying_monthly_income"]
        == 3_000
    )


def test_automatic_purchase_waits_when_mortgage_affordability_fails():
    affordability = MortgageAffordabilityPolicy(
        max_debt_service_ratio_pct=20,
        qualifying_income_names=("Salary",),
    )
    purchase = AutomaticPurchasePolicy(
        PropertyPurchase(100_000, 20, mortgage_duration_years=25),
        20_000,
        0,
        affordability,
    )

    result = compute_net_worth_over_time(
        1,
        BankAccount(100_000),
        [],
        [],
        [IncomeSource("Salary", "salary", 1_000, 0, 0)],
        [],
        property_purchase=purchase,
    )
    outcome = result[2]["purchase_outcome"]

    assert outcome["purchased"] is False
    assert outcome["affordability"]["eligible"] is False
    assert outcome["failure_reason"] == outcome["affordability"]["reason"]
    assert result[2]["purchase_events"] == []


def test_automatic_purchase_closes_when_cash_and_affordability_pass():
    affordability = MortgageAffordabilityPolicy(
        max_debt_service_ratio_pct=40,
        qualifying_income_names=("Salary",),
        base_monthly_living_buffer=1_000,
    )
    purchase = AutomaticPurchasePolicy(
        PropertyPurchase(100_000, 20, mortgage_duration_years=25),
        20_000,
        0,
        affordability,
    )

    result = compute_net_worth_over_time(
        1,
        BankAccount(100_000),
        [],
        [],
        [IncomeSource("Salary", "salary", 3_000, 0, 0)],
        [],
        property_purchase=purchase,
    )
    outcome = result[2]["purchase_outcome"]

    assert outcome["purchased"] is True
    assert outcome["purchase_month"] == 0
    assert outcome["affordability"]["eligible"] is True
    assert outcome["failure_reason"] is None
    assert len(result[2]["purchase_events"]) == 1


def test_mortgage_affordability_policy_round_trips_in_export():
    affordability = MortgageAffordabilityPolicy(
        max_debt_service_ratio_pct=35,
        qualifying_income_names=("Salary", "Partner salary"),
        variable_income_inclusion_pct=40,
        base_monthly_living_buffer=500,
        adult_count=2,
        monthly_buffer_per_adult=300,
        dependent_count=1,
        monthly_buffer_per_dependent=200,
    )
    config = SimulationConfig(
        years=1,
        initial_cash=0,
        investments=[],
        incomes=[],
        expenses=[],
        loans=[],
        property_purchase=AutomaticPurchasePolicy(
            PropertyPurchase(100_000, 20), 20_000, 5_000, affordability,
        ),
    )

    exported = create_simulation_export(config, *run_simulation(config))
    restored = read_simulation_export(exported)

    assert "Infinity" not in exported
    assert restored.property_purchase == config.property_purchase
