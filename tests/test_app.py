from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
import math

import pytest

from bank_account import InsufficientFundsException
from app import (CHART_EVENT_COLORS, _consume_run_request, _whole_amount,
                 DependentConfig, ExpenseConfig,
                 FinancialAssumptions, IncomeConfig, InvestmentConfig, LoanConfig, SimulationConfig,
                 build_components, build_simulation_events, create_simulation_export,
                 get_month_details, monte_carlo_sample_guidance,
                 monte_carlo_view_explanation,
                 read_simulation_export, run_simulation,
                 run_simulation_batch, run_simulations)
from investment import Investment
from property_purchase import AutomaticPurchasePolicy, PropertyPurchase


def test_build_components_exposes_all_model_options():
    config = SimulationConfig(income_type="freelance", expense_type="unexpected",
                              investment_type="bonds", fixed_rate=True)
    account, investments, loans, incomes, expenses = build_components(config)
    assert account.total_balance() == config.initial_cash
    assert investments[0].typology == "bonds"
    assert loans[0].is_fixed_rate is True
    assert incomes[0].income_type == "freelance"
    assert expenses[0].expense_type == "unexpected"


def test_build_components_supports_pre_feature_investment_class(monkeypatch):
    class LegacyInvestment:
        def __init__(self, name, typology, initial_cost, monthly_contribution):
            self.name = name
            self.typology = typology
            self.initial_cost = initial_cost
            self.monthly_contribution = monthly_contribution

    monkeypatch.setattr("app.Investment", LegacyInvestment)
    assumptions = _controlled_assumptions(
        investment_return_means={
            "stock": 12, "real_estate": 0, "bonds": 0, "safe": 0,
        },
        stock_dividend_rate=0.04,
        contribution_fee=7,
    )
    config = SimulationConfig(
        investments=[InvestmentConfig(
            "Stocks", "stock", 1000, 0,
            auto_invest_enabled=True, auto_invest_min_cash=15000,
            auto_invest_percentage=5, auto_invest_max_per_year=2,
        )],
        incomes=[], expenses=[], loans=[], assumptions=assumptions,
    )

    _, investments, _, _, _ = build_components(config)

    assert investments[0].auto_invest_enabled is True
    assert investments[0].auto_invest_min_cash == 15000
    assert investments[0].auto_invest_percentage == 5
    assert investments[0].auto_invest_max_per_year == 2
    assert type(investments[0]).TOPOLOGY_DETAILS["stock"] == {
        "mean": 12, "std_dev": 0,
    }
    assert type(investments[0]).DIVIDEND == 0.04
    assert type(investments[0]).CONTRIBUTION_FEE == 7


def test_build_components_supports_cached_income_source_constructor(monkeypatch):
    class LegacyIncomeSource:
        def __init__(self, name, income_type, monthly_income,
                     specific_taxation=None, bonus_pct=None):
            self.specific_taxation = specific_taxation

    monkeypatch.setattr("app.IncomeSource", LegacyIncomeSource)
    assumptions = _controlled_assumptions(income_tax_rates={
        **FinancialAssumptions().income_tax_rates, "salary": 0,
    })

    _, _, _, incomes, _ = build_components(SimulationConfig(
        investments=[], incomes=[IncomeConfig("Salary", "salary", 1000)],
        expenses=[], loans=[], assumptions=assumptions,
    ))

    assert incomes[0].specific_taxation == 0


def test_extreme_negative_return_is_clamped_to_total_loss(monkeypatch):
    monkeypatch.setattr("investment.random.gauss", lambda *_args: -150)

    investment = Investment("Volatile", "stock", 1000, 0)

    assert investment.compounded_annual_gain == 0
    investment.step()
    assert investment.current_value == 0


def test_major_ui_amounts_discard_cents():
    assert _whole_amount(5000.15) == 5000


def test_import_run_request_is_consumed_once():
    session_state = {"run_after_import": True}

    assert _consume_run_request(session_state, button_clicked=False) is True
    assert "run_after_import" not in session_state
    assert _consume_run_request(session_state, button_clicked=False) is False


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


def test_chart_events_include_only_completed_property_purchases():
    config = SimulationConfig(
        dependents=[],
        property_purchase=AutomaticPurchasePolicy(
            PropertyPurchase(100_000, 20, property_name="Family home"),
            20_000, 0,
        ),
    )

    completed = build_simulation_events(
        config, {}, months_simulated=24,
        purchase_events=[{"month": 7, "property_name": "Family home"}],
    )
    not_completed = build_simulation_events(
        config, {}, months_simulated=24, purchase_events=[],
    )

    assert completed == [{
        "month": 8,
        "label": "Family home purchased",
        "event_type": "Property purchase",
    }]
    assert not_completed == []
    assert CHART_EVENT_COLORS["Property purchase"] == "#0e7490"


def test_run_simulation_is_reproducible_and_isolated():
    config = SimulationConfig(years=2, seed=17)
    first = run_simulation(config)
    second = run_simulation(config)
    assert first == second
    assert len(first[0]) == 24
    assert set(first[2]) == {
        "investments", "bank_account", "investment_values", "income_rates",
        "income_values", "expense_values", "loan_balances", "loan_payments",
        "automatic_contributions", "automatic_contribution_fees",
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
    assert averaged[2]["automatic_contributions"]["Stocks"] == [0] * 12
    assert averaged[1] == individual_runs[0][1]


def test_monte_carlo_view_explanations_distinguish_average_and_individual_paths():
    overview = monte_carlo_view_explanation("Overview", 25)
    single_run = monte_carlo_view_explanation("Overview", 1)
    individual = monte_carlo_view_explanation("Individual run", 25)

    assert "Arithmetic-average overview" in overview
    assert "not a guaranteed or individually realizable household path" in overview
    assert "hide liquidity shortfalls" in overview
    assert "issue #25" in overview and "issue #67" in overview
    assert "arithmetic average is the same as Run 1" in single_run
    assert "does not hide differences between runs" in single_run
    assert "not a guaranteed forecast" in single_run
    assert "hide liquidity shortfalls" not in single_run
    assert "one simulated trajectory rather than an average" in individual
    assert "shortfalls the Overview may hide" in individual


def test_monte_carlo_sample_guidance_warns_for_small_samples_only():
    warning = monte_carlo_sample_guidance(2)
    assert "estimates based on the selected 2 simulation(s)" in warning
    assert "especially small" in warning
    assert "tail probabilities" in warning

    guidance = monte_carlo_sample_guidance(25)
    assert "estimates based on the selected 25 simulation(s)" in guidance
    assert "especially small" not in guidance


def test_simulation_batch_retains_reproducible_individual_trajectories():
    config = SimulationConfig(
        years=1, seed=20, initial_cash=5000, expenses=[], incomes=[], loans=[],
        investments=[InvestmentConfig("Stocks", "stock", 1000, 0)],
    )

    batch = run_simulation_batch(config, 3)

    assert [trajectory.run_number for trajectory in batch.trajectories] == [1, 2, 3]
    assert [trajectory.seed for trajectory in batch.trajectories] == [20, 21, 22]
    assert [trajectory.result for trajectory in batch.trajectories] == [
        run_simulation(replace(config, seed=seed)) for seed in (20, 21, 22)
    ]
    assert batch.average_result == run_simulations(config, 3)


def test_month_details_expose_individual_balances_and_flows():
    config = SimulationConfig(
        years=2, initial_cash=5000,
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
    assert any(row["Category"] == "Automatic investment contribution" for row in flows)


def test_month_details_show_salary_inflation_from_following_year():
    config = SimulationConfig(
        years=2, seed=1, initial_cash=0,
        investments=[], incomes=[IncomeConfig("Salary", "salary", 1000)],
        expenses=[], loans=[],
    )
    net_worth, _, details = run_simulation(config)

    _, month_13_flows = get_month_details(net_worth, details, month=13)
    salary_rate = next(
        row for row in month_13_flows
        if row["Category"] == "Gross monthly income rate" and row["Item"] == "Salary"
    )
    assert details["income_rates"]["Salary"][11] == 1000
    assert salary_rate["Amount"] == 1020


def test_multiple_simulations_require_positive_count():
    with pytest.raises(ValueError, match="at least 1"):
        run_simulations(SimulationConfig(), 0)


def test_multiple_simulations_enforce_practical_limit():
    with pytest.raises(ValueError, match="cannot exceed 25"):
        run_simulations(SimulationConfig(), 26)


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
    assert document["features"]["financial_assumptions"] == 1
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
    assert first_month["automatic_contributions"] == {}
    assert first_month["automatic_contribution_fees"] == {}
    assert first_month["income_rates"] == {
        name: values[0] for name, values in details["income_rates"].items()
    }
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
        investments=[InvestmentConfig(
            "Safe fund", "safe", 800, 75,
            auto_invest_enabled=True, auto_invest_min_cash=12000,
            auto_invest_percentage=7.5, auto_invest_max_per_year=2,
        )],
        incomes=[IncomeConfig("Consulting", "freelance", 2500, 20, 5)],
        expenses=[ExpenseConfig("Rent", "housing", 900)],
        loans=[LoanConfig("Car", 5000, 3.5, 4, True, 500)],
        dependents=[DependentConfig("Child", 300, 4, 6, 200)],
    )
    results = run_simulation(config)

    restored, import_warnings = read_simulation_export(
        create_simulation_export(config, *results), include_warnings=True,
    )

    assert restored == config
    assert import_warnings == []


def test_simulation_export_retains_versioned_trajectories_and_imports_them():
    config = SimulationConfig(
        years=1, seed=7, investments=[], incomes=[], expenses=[], loans=[],
    )
    batch = run_simulation_batch(config, 2)

    exported = create_simulation_export(
        config, *batch.average_result, simulation_count=2,
        trajectories=batch.trajectories,
    )
    document = json.loads(exported)

    assert document["schema_version"] == 1
    assert document["features"]["monte_carlo_trajectories"] == 1
    assert [run["run_number"] for run in document["results"]["trajectories"]] == [1, 2]
    assert [run["seed"] for run in document["results"]["trajectories"]] == [7, 8]
    assert all(len(run["monthly"]) == 12 for run in document["results"]["trajectories"])
    assert read_simulation_export(exported) == config


def test_property_purchase_export_preserves_fees_and_outcomes_per_trajectory():
    config = SimulationConfig(
        years=1, initial_cash=200_000, investments=[], incomes=[], expenses=[], loans=[],
        property_purchase=AutomaticPurchasePolicy(
            PropertyPurchase(100_000, 20, 500, 0, 25), 30_000, 10_000,
        ),
    )
    batch = run_simulation_batch(config, 2)

    document = json.loads(create_simulation_export(
        config, *batch.average_result, simulation_count=2,
        trajectories=batch.trajectories,
    ))

    assert document["results"]["purchase_fees"] == [500] + [0] * 11
    assert document["results"]["purchase_outcome"]["purchased"] is True
    assert document["results"]["purchase_outcome"]["trajectories"][0][
        "minimum_cash_reserve"
    ] == 10_000
    assert document["results"]["purchase_outcome"]["trajectories"][0][
        "cash_after_purchase"
    ] >= 10_000
    assert all(run["purchase_fees"] == [500] + [0] * 11
               for run in document["results"]["trajectories"])
    assert all(run["purchase_outcome"]["purchased"] is True
               for run in document["results"]["trajectories"])


def test_trajectory_import_rejects_inconsistent_seed():
    config = SimulationConfig(
        years=1, seed=7, investments=[], incomes=[], expenses=[], loans=[],
    )
    batch = run_simulation_batch(config, 2)
    document = json.loads(create_simulation_export(
        config, *batch.average_result, simulation_count=2,
        trajectories=batch.trajectories,
    ))
    document["results"]["trajectories"][1]["seed"] = 99

    with pytest.raises(ValueError, match="trajectory seed"):
        read_simulation_export(json.dumps(document))


@pytest.mark.parametrize("invalid_seed", [None, "7", True])
def test_trajectory_import_rejects_nonnumeric_seed(invalid_seed):
    config = SimulationConfig(
        years=1, seed=7, investments=[], incomes=[], expenses=[], loans=[],
    )
    batch = run_simulation_batch(config, 1)
    document = json.loads(create_simulation_export(
        config, *batch.average_result, trajectories=batch.trajectories,
    ))
    document["parameters"]["seed"] = invalid_seed

    with pytest.raises(ValueError, match="simulation seed must be an integer"):
        read_simulation_export(json.dumps(document))


def test_financial_assumptions_round_trip_and_legacy_default():
    assumptions = FinancialAssumptions(
        investment_return_means={
            "stock": 6, "real_estate": 3, "bonds": 2, "safe": 1,
        },
        investment_return_std_devs={
            "stock": 15, "real_estate": 6, "bonds": 4, "safe": 0.5,
        },
        stock_dividend_rate=0.03,
        contribution_fee=12,
        income_tax_rates={
            "salary": 25, "rent": 10, "dividend": 15, "royalty": 12,
            "freelance": 28, "pension": 5, "other": 8,
        },
    )
    config = SimulationConfig(
        years=1, investments=[], incomes=[], expenses=[], loans=[], assumptions=assumptions,
    )
    document = json.loads(create_simulation_export(config, *run_simulation(config)))

    assert read_simulation_export(json.dumps(document)).assumptions == assumptions

    document["parameters"].pop("assumptions")
    assert read_simulation_export(json.dumps(document)).assumptions == FinancialAssumptions()


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"stock_dividend_rate": float("nan")}, "stock dividend rate"),
        ({"contribution_fee": -1}, "contribution fee"),
        ({"income_tax_rates": {"salary": 20}}, "income tax rates"),
    ],
)
def test_financial_assumptions_reject_invalid_values(changes, message):
    with pytest.raises(ValueError, match=message):
        FinancialAssumptions(**changes)


def _controlled_assumptions(**changes):
    values = asdict(FinancialAssumptions())
    values["investment_return_means"] = dict.fromkeys(values["investment_return_means"], 0)
    values["investment_return_std_devs"] = dict.fromkeys(
        values["investment_return_std_devs"], 0
    )
    values.update(changes)
    return FinancialAssumptions(**values)


def test_return_assumption_changes_only_investment_outputs():
    base = SimulationConfig(
        years=1, initial_cash=0, yearly_fee=0, incomes=[], expenses=[], loans=[],
        investments=[InvestmentConfig("Stocks", "stock", 1000, 0)],
        assumptions=_controlled_assumptions(stock_dividend_rate=0),
    )
    changed_values = asdict(base.assumptions)
    changed_values["investment_return_means"]["stock"] = 12
    changed = replace(base, assumptions=FinancialAssumptions(**changed_values))

    base_result = run_simulation(base)
    changed_result = run_simulation(changed)

    assert base_result[2]["bank_account"] == changed_result[2]["bank_account"]
    assert base_result[2]["investment_values"] != changed_result[2]["investment_values"]


def test_dividend_fee_and_tax_assumptions_change_their_expected_flows():
    dividend_base = SimulationConfig(
        years=1, initial_cash=0, yearly_fee=0, incomes=[], expenses=[], loans=[],
        investments=[InvestmentConfig("Stocks", "stock", 1000, 0)],
        assumptions=_controlled_assumptions(stock_dividend_rate=0),
    )
    dividend_changed = replace(
        dividend_base,
        assumptions=_controlled_assumptions(stock_dividend_rate=0.12),
    )
    assert run_simulation(dividend_base)[2]["investment_values"] == run_simulation(
        dividend_changed
    )[2]["investment_values"]
    assert run_simulation(dividend_base)[2]["bank_account"][-1] == 0
    assert run_simulation(dividend_changed)[2]["bank_account"][-1] == 120

    fee_base = replace(
        dividend_base, initial_cash=5000,
        investments=[InvestmentConfig("Stocks", "stock", 0, 100)],
        assumptions=_controlled_assumptions(contribution_fee=0, stock_dividend_rate=0),
    )
    fee_changed = replace(
        fee_base,
        assumptions=_controlled_assumptions(contribution_fee=50, stock_dividend_rate=0),
    )
    assert run_simulation(fee_base)[2]["bank_account"] == run_simulation(
        fee_changed
    )[2]["bank_account"]
    assert run_simulation(fee_base)[2]["investment_values"]["Stocks"][-1] == 1200
    assert run_simulation(fee_changed)[2]["investment_values"]["Stocks"][-1] == 600

    zero_taxes = dict.fromkeys(FinancialAssumptions().income_tax_rates, 0)
    half_salary_tax = {**zero_taxes, "salary": 50}
    income_base = SimulationConfig(
        years=1, initial_cash=0, yearly_fee=0, investments=[], expenses=[], loans=[],
        incomes=[IncomeConfig("Salary", "salary", 1000)],
        assumptions=_controlled_assumptions(income_tax_rates=zero_taxes),
    )
    income_changed = replace(
        income_base,
        assumptions=_controlled_assumptions(income_tax_rates=half_salary_tax),
    )
    assert run_simulation(income_base)[2]["income_rates"] == run_simulation(
        income_changed
    )[2]["income_rates"]
    assert run_simulation(income_base)[2]["income_values"]["Salary"][0] == 1000
    assert run_simulation(income_changed)[2]["income_values"]["Salary"][0] == 500


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


def test_explicit_monthly_contribution_still_runs_when_auto_invest_is_disabled(monkeypatch):
    calls = []

    def record_contribution(self, amount):
        calls.append(amount)
        return 0

    monkeypatch.setattr("investment.Investment.add_contribution", record_contribution)
    config = SimulationConfig(
        years=1, initial_cash=5000, yearly_fee=0,
        investments=[InvestmentConfig("Stocks", "stock", 1000, 100)],
        incomes=[], expenses=[], loans=[],
    )

    run_simulation(config)

    assert calls == [100] * 12


def test_enabled_automatic_contribution_records_gross_transfer_and_fee():
    config = SimulationConfig(
        years=1, initial_cash=20000, yearly_fee=0,
        investments=[InvestmentConfig(
            "Stocks", "stock", 0, 0,
            auto_invest_enabled=True, auto_invest_min_cash=10000,
            auto_invest_percentage=10, auto_invest_max_per_year=1,
        )],
        incomes=[IncomeConfig("Salary", "salary", 1000, tax_rate=10)],
        expenses=[ExpenseConfig("Living", "housing", 100)], loans=[],
    )

    net_worth, _, details = run_simulation(config)

    assert details["automatic_contributions"]["Stocks"][0] == 2080
    assert details["automatic_contribution_fees"]["Stocks"][0] == 50
    assert details["automatic_contributions"]["Stocks"][1:] == [0] * 11
    assert details["bank_account"][0] == 18720
    assert details["investment_values"]["Stocks"][0] == 2030
    assert net_worth[0] == 20750


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


def test_real_estate_costs_are_linked_expenses_and_tax_is_annual():
    config = SimulationConfig(
        years=1, initial_cash=5000, yearly_fee=0,
        investments=[InvestmentConfig(
            "Property", "real_estate", 1000, monthly_contribution=999,
            monthly_expenses=100, yearly_tax=1200,
        )],
        incomes=[], expenses=[], loans=[],
    )

    _, investments, _, _, expenses = build_components(config)
    result = run_simulation(config)

    assert investments[0].monthly_contribution == 0
    assert [expense.name for expense in expenses] == [
        "Property — property expenses (linked)",
        "Property — property tax (linked)",
    ]
    assert result[2]["bank_account"][0] == 4900
    assert result[2]["bank_account"][10] == 3900
    assert result[2]["bank_account"][11] == 2600
    assert result[2]["expense_values"]["Property — property tax (linked)"][:11] == [0] * 11
    assert result[2]["expense_values"]["Property — property tax (linked)"][11] == 1200


def test_legacy_real_estate_export_maps_contribution_to_property_expenses():
    config = SimulationConfig(
        investments=[InvestmentConfig("Property", "real_estate", 1000, 100)]
    )
    result = run_simulation(config)
    document = json.loads(create_simulation_export(config, *result))
    investment = document["parameters"]["investments"][0]
    investment.pop("monthly_expenses")
    investment.pop("yearly_tax")
    investment.pop("auto_invest_enabled")
    investment.pop("auto_invest_min_cash")
    investment.pop("auto_invest_percentage")
    investment.pop("auto_invest_max_per_year")
    document.pop("features")

    restored, import_warnings = read_simulation_export(
        json.dumps(document), include_warnings=True,
    )

    assert restored.investments[0].monthly_contribution == 0
    assert restored.investments[0].monthly_expenses == 100
    assert restored.investments[0].auto_invest_enabled is False
    assert len(import_warnings) == 1
    assert "disabled for safety" in import_warnings[0]


@pytest.mark.parametrize("feature_version", [None, 999])
def test_unrecognized_auto_invest_export_version_forces_policy_off(feature_version):
    config = SimulationConfig(investments=[InvestmentConfig(
        "Stocks", "stock", 1000, 0, auto_invest_enabled=True,
    )])
    document = json.loads(create_simulation_export(config, *run_simulation(config)))
    if feature_version is None:
        document.pop("features")
    else:
        document["features"]["automatic_investment_contributions"] = feature_version

    restored, import_warnings = read_simulation_export(
        json.dumps(document), include_warnings=True,
    )

    assert document["parameters"]["investments"][0]["auto_invest_enabled"] is True
    assert restored.investments[0].auto_invest_enabled is False
    assert len(import_warnings) == 1
    assert "disabled for safety" in import_warnings[0]


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
