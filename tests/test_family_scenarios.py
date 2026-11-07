import math

from app import (
    CurveballConfig, DependentConfig, ExpenseConfig, IncomeConfig, InvestmentConfig,
    SimulationConfig, run_simulation,
)


def family_config(**overrides):
    values = dict(
        years=10,
        initial_cash=50000,
        yearly_fee=0,
        investments=[],
        incomes=[],
        expenses=[],
        loans=[],
    )
    values.update(overrides)
    return SimulationConfig(**values)


def test_no_children_is_a_valid_family_setup():
    result = run_simulation(family_config(years=3))
    assert len(result[0]) == 36
    assert result[0][-1] == 50000


def test_one_child_adds_base_and_school_age_costs():
    result = run_simulation(family_config(
        years=2,
        dependents=[DependentConfig("A", 100, age=5, school_start_age=6,
                                   school_monthly_cost=400)],
    ))
    # Year one costs 100/month; after the sixth birthday year two costs 400/month.
    assert result[2]["bank_account"][11] == 48800
    assert result[2]["bank_account"][-1] == 42776


def test_multiple_children_keep_separate_age_schedules():
    result = run_simulation(family_config(
        years=2,
        dependents=[
            DependentConfig("Older", 100, age=5, school_start_age=6, school_monthly_cost=400),
            DependentConfig("Younger", 100, age=2, school_start_age=6, school_monthly_cost=300),
        ],
    ))
    # Only the older child reaches the configured threshold during this run.
    assert result[2]["bank_account"][11] == 47600
    assert abs(result[2]["bank_account"][-1] - 40352) < 1e-9


def test_child_already_at_school_age_includes_school_cost_immediately():
    result = run_simulation(family_config(
        years=1,
        dependents=[DependentConfig("School child", 100, age=8,
                                   school_start_age=6, school_monthly_cost=400)],
    ))
    assert result[2]["bank_account"][0] == 49500


def test_explicit_childcare_switches_to_school_without_double_counting():
    result = run_simulation(family_config(
        years=2,
        dependents=[DependentConfig(
            "Child", monthly_cost=0, age=5, school_start_age=6,
            school_monthly_cost=200, childcare_monthly_cost=500,
        )],
    ))

    assert result[2]["bank_account"][11] == 44000
    assert result[2]["bank_account"][-1] == 41600
    child_values = result[2]["expense_values"]["Child (child)"]
    assert child_values[:12] == [500] * 12
    assert child_values[12:] == [200] * 12


def test_disabled_curveball_matches_baseline_and_is_audited_as_off():
    baseline = family_config(
        years=1,
        initial_cash=0,
        incomes=[IncomeConfig("Salary", "salary", 1_000)],
    )
    disabled = SimulationConfig(
        **{**baseline.__dict__, "curveballs": [CurveballConfig(
            "Job loss", enabled=False, probability=1.0,
            start_month_min=1, start_month_max=1,
        )]},
    )

    base_result = run_simulation(baseline)
    disabled_result = run_simulation(disabled)

    assert disabled_result[0] == base_result[0]
    assert disabled_result[2]["curveball_events"][0]["triggered"] is False


def test_curveball_is_seeded_temporary_and_recovery_is_reported():
    config = family_config(
        years=1,
        initial_cash=0,
        incomes=[IncomeConfig("Salary", "salary", 1_000)],
        curveballs=[CurveballConfig(
            "Job loss", enabled=True, probability=1.0,
            start_month_min=2, start_month_max=2, duration_months=2,
            affected_income_name="Salary", income_reduction_pct=100,
            recovery_months=2,
        )],
    )

    result = run_simulation(config)
    income = result[2]["income_values"]["Salary"]
    event = result[2]["curveball_events"][0]

    assert income[0] == 700
    assert income[1:3] == [0, 0]
    assert income[3] == 350
    assert income[4] == 700
    assert event["triggered"] is True
    assert event["start_month"] == 2
    assert event["recovery_month"] == 5
    assert event["recovery_months_observed"] == 4


def test_overlapping_curveballs_stack_income_reductions():
    config = family_config(
        years=1,
        initial_cash=0,
        incomes=[IncomeConfig("Salary", "salary", 1_000)],
        curveballs=[
            CurveballConfig("Shock A", True, 1.0, 1, 1, 1, "Salary", 50, 0),
            CurveballConfig("Shock B", True, 1.0, 1, 1, 1, "Salary", 50, 0),
        ],
    )

    result = run_simulation(config)

    assert result[2]["income_values"]["Salary"][0] == 175
    assert result[2]["curveball_income_adjustments"]["Salary"][0] == 525


def test_starting_level_orders_ending_level_for_same_family_setup():
    low = run_simulation(family_config(initial_cash=10000, years=5))
    high = run_simulation(family_config(initial_cash=30000, years=5))
    assert high[0][-1] > low[0][-1]
    assert high[0][-1] - low[0][-1] == 20000


def test_market_scenarios_have_uncertain_but_finite_ending_levels():
    config = family_config(
        years=10,
        investments=[InvestmentConfig("Stocks", "stock", 1000, 100)],
    )
    endings = [run_simulation(SimulationConfig(**{**config.__dict__, "seed": seed}))[0][-1]
               for seed in (1, 2, 3)]
    assert all(math.isfinite(value) for value in endings)
    assert len(set(endings)) == 3
