import math

from app import DependentConfig, ExpenseConfig, InvestmentConfig, SimulationConfig, run_simulation


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
