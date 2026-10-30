"""Financial regression tests for retained Monte Carlo runs and exports."""
from dataclasses import asdict, replace
import copy
import json
import math

import pytest

import app
from app import (
    ExpenseConfig, FinancialAssumptions, IncomeConfig, InvestmentConfig,
    LoanConfig, SimulationConfig, create_simulation_export,
    read_simulation_export, run_simulation, run_simulation_batch,
    run_simulations,
)

SCALAR_DETAILS = ("bank_account", "investments")
MAPPED_DETAILS = (
    "investment_values", "automatic_contributions",
    "automatic_contribution_fees", "income_rates", "income_values",
    "expense_values", "loan_balances", "loan_payments",
)


def _zero_return_assumptions(**changes):
    values = asdict(FinancialAssumptions())
    values["investment_return_means"] = dict.fromkeys(values["investment_return_means"], 0)
    values["investment_return_std_devs"] = dict.fromkeys(
        values["investment_return_std_devs"], 0
    )
    values["stock_dividend_rate"] = 0
    values.update(changes)
    return FinancialAssumptions(**values)


def _stochastic_config():
    return SimulationConfig(
        years=2, seed=100, initial_cash=30000, yearly_fee=25,
        investments=[
            InvestmentConfig(
                "Stocks", "stock", 1000, 50, auto_invest_enabled=True,
                auto_invest_min_cash=10000, auto_invest_percentage=10,
                auto_invest_max_per_year=2,
            ),
            InvestmentConfig("Bonds", "bonds", 500, 0),
        ],
        incomes=[IncomeConfig("Salary", "salary", 2000)],
        expenses=[ExpenseConfig("Living", "housing", 900)],
        loans=[LoanConfig("Car", 3600, 3, 1)],
    )


def _zero_scenarios():
    return {
        "zero_return": SimulationConfig(
            years=1, seed=5, initial_cash=20000, yearly_fee=0,
            investments=[InvestmentConfig(
                "Stocks", "stock", 0, 0, auto_invest_enabled=True,
                auto_invest_min_cash=10000, auto_invest_percentage=10,
                auto_invest_max_per_year=1,
            )],
            incomes=[IncomeConfig("Salary", "salary", 1000, tax_rate=0)],
            expenses=[], loans=[],
            assumptions=_zero_return_assumptions(),
        ),
        "zero_income_expense": SimulationConfig(
            years=1, seed=5, initial_cash=5000, yearly_fee=0,
            investments=[], incomes=[IncomeConfig("Salary", "salary", 0)],
            expenses=[ExpenseConfig("Living", "housing", 0)], loans=[],
        ),
        "deterministic_loan": SimulationConfig(
            years=1, seed=5, initial_cash=5000, yearly_fee=0,
            investments=[], incomes=[], expenses=[],
            loans=[LoanConfig("Loan", 1200, 0, 1)],
            assumptions=_zero_return_assumptions(),
        ),
    }


def _assert_same(left, right):
    if isinstance(left, dict):
        assert left.keys() == right.keys()
        for key in left:
            _assert_same(left[key], right[key])
    elif isinstance(left, (list, tuple)):
        assert len(left) == len(right)
        for a, b in zip(left, right):
            _assert_same(a, b)
    elif isinstance(left, float) or isinstance(right, float):
        assert math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-9)
    else:
        assert left == right


def test_every_retained_run_matches_standalone_simulation_completely():
    config = _stochastic_config()
    batch = run_simulation_batch(config, 4)

    for index, trajectory in enumerate(batch.trajectories):
        effective_seed = config.seed + index
        assert trajectory.seed == effective_seed
        expected = run_simulation(replace(config, seed=effective_seed))
        assert len(trajectory.net_worth) == config.years * 12
        assert trajectory.net_worth == expected[0]
        assert trajectory.repayment_dates == expected[1]
        assert trajectory.details == expected[2]


def test_overview_is_arithmetic_mean_of_every_month_and_detail_field():
    config = _stochastic_config()
    batch = run_simulation_batch(config, 4)
    net_worth, _, details = batch.average_result
    runs = [trajectory.details for trajectory in batch.trajectories]
    months = config.years * 12

    for month in range(months):
        assert math.isclose(
            net_worth[month],
            sum(t.net_worth[month] for t in batch.trajectories) / 4,
            rel_tol=1e-9, abs_tol=1e-9,
        )
        for name in SCALAR_DETAILS:
            assert math.isclose(
                details[name][month], sum(r[name][month] for r in runs) / 4,
                rel_tol=1e-9, abs_tol=1e-9,
            )
    for name in MAPPED_DETAILS:
        assert details[name].keys() == set().union(*(r[name] for r in runs))
        for item, values in details[name].items():
            assert len(values) == months
            for month in range(months):
                expected = sum(r[name][item][month] for r in runs) / 4
                assert math.isclose(values[month], expected, rel_tol=1e-9, abs_tol=1e-9)
    assert run_simulations(config, 4)[2] == details


def test_net_worth_reconciles_with_cash_investments_and_loans():
    config = _stochastic_config()
    batch = run_simulation_batch(config, 3)

    for series in [t for t in batch.trajectories] + [None]:
        if series is None:
            net_worth, _, details = batch.average_result
        else:
            net_worth, details = series.net_worth, series.details
        for month, value in enumerate(net_worth):
            liabilities = sum(v[month] for v in details["loan_balances"].values())
            assets = details["bank_account"][month] + details["investments"][month]
            assert math.isclose(value, assets - liabilities, rel_tol=1e-9, abs_tol=1e-6)
            assert math.isclose(
                details["investments"][month],
                sum(v[month] for v in details["investment_values"].values()),
                rel_tol=1e-9, abs_tol=1e-6,
            )


def test_automatic_transfer_and_fee_reconcile_without_duplication():
    config = _zero_scenarios()["zero_return"]
    batch = run_simulation_batch(config, 3)
    document = json.loads(create_simulation_export(
        config, *batch.average_result, simulation_count=3,
        trajectories=batch.trajectories,
    ))

    for trajectory, exported in zip(
        batch.trajectories, document["results"]["trajectories"]
    ):
        details = trajectory.details
        assert details["automatic_contributions"]["Stocks"] == [2070] + [0] * 11
        assert details["automatic_contribution_fees"]["Stocks"] == [50] + [0] * 11
        assert details["bank_account"] == [18630 + 700 * m for m in range(12)]
        assert details["investment_values"]["Stocks"] == [2020] * 12
        # Net income adds 700 a month; the transfer is neutral and only the
        # single 50 fee leaves the household.
        assert trajectory.net_worth == [
            20000 + 700 * (m + 1) - 50 for m in range(12)
        ]
        monthly = exported["monthly"]
        assert [m["automatic_contributions"]["Stocks"] for m in monthly] == (
            [2070] + [0] * 11
        )
        assert [m["automatic_contribution_fees"]["Stocks"] for m in monthly] == (
            [50] + [0] * 11
        )
        assert [m["net_worth"] for m in monthly] == trajectory.net_worth
    assert batch.average_result[2]["automatic_contributions"]["Stocks"] == (
        [2070] + [0] * 11
    )
    assert batch.average_result[0] == [
        20000 + 700 * (m + 1) - 50 for m in range(12)
    ]


def test_zero_income_and_expense_leave_cash_unchanged_through_retention():
    config = _zero_scenarios()["zero_income_expense"]
    batch = run_simulation_batch(config, 3)

    for net_worth, _, details in [t.result for t in batch.trajectories] + [
        batch.average_result
    ]:
        assert net_worth == [5000] * 12
        assert details["bank_account"] == [5000] * 12
        assert details["investments"] == [0] * 12
        assert details["income_values"]["Salary"] == [0] * 12
        assert details["expense_values"]["Living"] == [0] * 12
        assert details["loan_balances"] == {}


def test_deterministic_loan_is_repaid_once_and_not_duplicated():
    config = _zero_scenarios()["deterministic_loan"]
    batch = run_simulation_batch(config, 3)
    document = json.loads(create_simulation_export(
        config, *batch.average_result, simulation_count=3,
        trajectories=batch.trajectories,
    ))

    for net_worth, repayments, details in [t.result for t in batch.trajectories] + [
        batch.average_result
    ]:
        assert details["loan_payments"]["Loan"] == [100] * 12
        assert sum(details["loan_payments"]["Loan"]) == 1200
        assert details["loan_balances"]["Loan"] == [1100 - 100 * m for m in range(12)]
        assert details["bank_account"] == [5000 - 100 * (m + 1) for m in range(12)]
        # Repayment moves cash into the liability; net worth is unchanged.
        assert net_worth == [3800] * 12
        assert repayments == {"Loan": 11}
    for run in document["results"]["trajectories"]:
        assert run["loan_repayments"] == [{"name": "Loan", "month": 12}]
        assert sum(m["loan_payments"]["Loan"] for m in run["monthly"]) == 1200
    assert document["results"]["loan_repayments"] == [{"name": "Loan", "month": 12}]


@pytest.mark.parametrize("name", ["zero_return", "zero_income_expense",
                                  "deterministic_loan"])
def test_exported_trajectories_equal_retained_runs(name):
    config = _zero_scenarios()[name]
    batch = run_simulation_batch(config, 2)
    document = json.loads(create_simulation_export(
        config, *batch.average_result, simulation_count=2,
        trajectories=batch.trajectories,
    ))

    assert len(document["results"]["trajectories"]) == 2
    for trajectory, exported in zip(batch.trajectories,
                                    document["results"]["trajectories"]):
        assert exported["seed"] == trajectory.seed
        assert exported["monthly"] == app._monthly_results(
            trajectory.net_worth, trajectory.details
        )
    assert document["results"]["monthly"] == app._monthly_results(
        batch.average_result[0], batch.average_result[2]
    )
    assert read_simulation_export(json.dumps(document)) == config


def test_legacy_exports_without_trajectories_still_import():
    config = _zero_scenarios()["zero_return"]
    result = run_simulation(config)
    single = json.loads(create_simulation_export(config, *result))
    legacy = copy.deepcopy(single)
    del legacy["features"]
    del legacy["results"]["simulation_count"]

    for document in (single, legacy):
        assert "monte_carlo_trajectories" not in document.get("features", {})
        assert "trajectories" not in document["results"]
        imported, warnings = read_simulation_export(
            json.dumps(document), include_warnings=True
        )
        assert imported.years == config.years
        assert imported.initial_cash == config.initial_cash
    assert warnings  # legacy files without auto-invest metadata warn


def _trajectory_document(count=2):
    config = _zero_scenarios()["zero_return"]
    batch = run_simulation_batch(config, count)
    return json.loads(create_simulation_export(
        config, *batch.average_result, simulation_count=count,
        trajectories=batch.trajectories,
    ))


def _drop_run(document):
    document["results"]["trajectories"].pop()


def _extra_run(document):
    document["results"]["trajectories"].append(
        copy.deepcopy(document["results"]["trajectories"][-1])
    )


def _short_month_list(document):
    document["results"]["trajectories"][1]["monthly"].pop()


def _long_month_list(document):
    run = document["results"]["trajectories"][0]
    run["monthly"].append(copy.deepcopy(run["monthly"][-1]))


def _bad_run_number(document):
    document["results"]["trajectories"][1]["run_number"] = 5


def _bad_seed(document):
    document["results"]["trajectories"][0]["seed"] += 1


def _bad_count(document):
    document["results"]["simulation_count"] = 0


def _non_list_trajectories(document):
    document["results"]["trajectories"] = {"run": 1}


def _non_dict_trajectory(document):
    document["results"]["trajectories"][0] = "run"


def _unsupported_version(document):
    document["features"]["monte_carlo_trajectories"] = 99


@pytest.mark.parametrize("mutate", [
    _drop_run, _extra_run, _short_month_list, _long_month_list, _bad_run_number,
    _bad_seed, _bad_count, _non_list_trajectories, _non_dict_trajectory,
    _unsupported_version,
])
def test_malformed_trajectory_exports_fail_safely(mutate, monkeypatch):
    document = _trajectory_document()
    mutate(document)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("an import must not generate financial results")

    monkeypatch.setattr(app, "run_simulation", forbidden)
    monkeypatch.setattr(app, "run_simulation_batch", forbidden)
    monkeypatch.setattr(app, "run_simulations", forbidden)
    monkeypatch.setattr(app, "compute_net_worth_over_time", forbidden)

    with pytest.raises(ValueError):
        read_simulation_export(json.dumps(document))


def test_export_rejects_trajectory_count_mismatch():
    config = _zero_scenarios()["zero_return"]
    batch = run_simulation_batch(config, 2)

    with pytest.raises(ValueError, match="trajectory count"):
        create_simulation_export(
            config, *batch.average_result, simulation_count=3,
            trajectories=batch.trajectories,
        )
