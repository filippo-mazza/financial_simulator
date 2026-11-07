from dataclasses import replace
from datetime import datetime, timezone
import json

import pytest

from app import (
    ExpenseConfig, FinancialAssumptions, IncomeConfig,
    InvestmentConfig,
    SimulationConfig,
    comparison_balance_rows,
    comparison_cash_flow_rows,
    create_scenario_workspace_export,
    create_simulation_export,
    read_scenario_workspace_export,
    read_simulation_export,
    paired_delta_quantiles,
    run_paired_scenario_comparison,
    run_simulation,
    scenario_input_differences,
)
from property_purchase import PropertyPurchase
from scenario_workspace import (
    ALTERNATIVE_ID,
    BASELINE_ID,
    ScenarioWorkspace,
)


def _config():
    return SimulationConfig(
        years=1,
        seed=17,
        initial_cash=5_000,
        yearly_fee=0,
        investments=[InvestmentConfig("Savings", "safe", 1_000, 0)],
        incomes=[IncomeConfig("Salary", "salary", 3_000, 0)],
        expenses=[],
        loans=[],
    )


def test_workspace_duplicates_configs_without_shared_mutable_state():
    original = _config()
    workspace = ScenarioWorkspace.from_config(original)

    alternative = workspace.config_for(ALTERNATIVE_ID)
    alternative.investments[0].initial_amount = 9_000
    workspace = workspace.save(ALTERNATIVE_ID, alternative)
    original.investments[0].initial_amount = 99_000

    assert workspace.config_for(BASELINE_ID).investments[0].initial_amount == 1_000
    assert workspace.config_for(ALTERNATIVE_ID).investments[0].initial_amount == 9_000
    exposed = workspace.config_for(ALTERNATIVE_ID)
    exposed.investments[0].initial_amount = 1
    assert workspace.config_for(ALTERNATIVE_ID).investments[0].initial_amount == 9_000


def test_identical_variants_are_reproducible_and_changed_variant_is_isolated():
    workspace = ScenarioWorkspace.from_config(_config())

    assert run_simulation(workspace.config_for(BASELINE_ID)) == run_simulation(
        workspace.config_for(ALTERNATIVE_ID)
    )

    alternative = workspace.config_for(ALTERNATIVE_ID)
    alternative.initial_cash = 15_000
    workspace = workspace.save(ALTERNATIVE_ID, alternative)

    assert workspace.config_for(BASELINE_ID).initial_cash == 5_000
    assert workspace.config_for(ALTERNATIVE_ID).initial_cash == 15_000
    assert run_simulation(workspace.config_for(BASELINE_ID))[0] != run_simulation(
        workspace.config_for(ALTERNATIVE_ID)
    )[0]


def test_workspace_names_activation_reset_and_validation():
    workspace = ScenarioWorkspace.from_config(_config())
    workspace = workspace.rename(BASELINE_ID, "Current plan")
    workspace = workspace.rename(ALTERNATIVE_ID, "Higher savings")
    workspace = workspace.activate(ALTERNATIVE_ID)
    changed = replace(workspace.config_for(ALTERNATIVE_ID), initial_cash=20_000)
    workspace = workspace.save(ALTERNATIVE_ID, changed)

    assert workspace.active_variant.name == "Higher savings"
    with pytest.raises(ValueError, match="unique"):
        workspace.rename(ALTERNATIVE_ID, "Current plan")
    with pytest.raises(ValueError, match="required"):
        workspace.rename(ALTERNATIVE_ID, " ")

    reset = workspace.reset_from(ALTERNATIVE_ID, BASELINE_ID)
    assert reset.config_for(ALTERNATIVE_ID) == reset.config_for(BASELINE_ID)


def test_workspace_parameters_round_trip_without_results():
    workspace = ScenarioWorkspace.from_config(_config())
    workspace = workspace.rename(BASELINE_ID, "Synthetic baseline")
    workspace = workspace.rename(ALTERNATIVE_ID, "Synthetic alternative")
    workspace = workspace.activate(ALTERNATIVE_ID)
    alternative = replace(workspace.config_for(ALTERNATIVE_ID), initial_cash=12_000)
    workspace = workspace.save(ALTERNATIVE_ID, alternative)

    exported = create_scenario_workspace_export(
        workspace, datetime(2026, 10, 31, 12, tzinfo=timezone.utc),
    )
    document = json.loads(exported)
    restored = read_scenario_workspace_export(exported)

    assert document["format"] == "financial-simulator-scenario-workspace"
    assert document["schema_version"] == 1
    assert document["exported_at_utc"] == "2026-10-31T12:00:00+00:00"
    assert all("results" not in item["scenario"] for item in document["variants"])
    assert restored.active_id == ALTERNATIVE_ID
    assert restored.variant(BASELINE_ID).name == "Synthetic baseline"
    assert restored.variant(ALTERNATIVE_ID).name == "Synthetic alternative"
    assert restored.config_for(BASELINE_ID) == workspace.config_for(BASELINE_ID)
    assert restored.config_for(ALTERNATIVE_ID) == workspace.config_for(ALTERNATIVE_ID)


def test_workspace_reader_rejects_wrong_shape_without_affecting_single_exports():
    config = _config()
    result = run_simulation(config)
    single_export = create_simulation_export(config, *result)

    assert read_simulation_export(single_export) == config
    with pytest.raises(ValueError, match="not a Financial Simulator scenario workspace"):
        read_scenario_workspace_export(single_export)

    workspace_document = json.loads(
        create_scenario_workspace_export(ScenarioWorkspace.from_config(config))
    )
    workspace_document["variants"].pop()
    with pytest.raises(ValueError, match="exactly two"):
        read_scenario_workspace_export(json.dumps(workspace_document))


def test_paired_comparison_of_identical_variants_has_zero_differences():
    workspace = ScenarioWorkspace.from_config(_config())

    comparison = run_paired_scenario_comparison(workspace, 3)

    assert [item.seed for item in comparison.baseline_batch.trajectories] == [
        item.seed for item in comparison.alternative_batch.trajectories
    ] == [17, 18, 19]
    assert comparison.warnings == ()
    assert all(
        row["B minus A"] == 0 for row in comparison_balance_rows(comparison)
    )
    assert all(
        row["mean"] == row["p10"] == row["median"] == row["p90"] == 0
        for row in paired_delta_quantiles(comparison)
    )
    assert all(
        row["B minus A"] == 0 for row in comparison_cash_flow_rows(comparison)
    )
    assert scenario_input_differences(comparison) == []


def test_paired_comparison_reports_changed_income_and_input_difference():
    workspace = ScenarioWorkspace.from_config(_config())
    alternative = workspace.config_for(ALTERNATIVE_ID)
    alternative.incomes[0].monthly_amount = 3_500
    workspace = workspace.save(ALTERNATIVE_ID, alternative)

    comparison = run_paired_scenario_comparison(workspace, 2)
    income_row = next(
        row for row in comparison_cash_flow_rows(comparison)
        if row["Tracked flow"] == "Net income received"
    )

    assert income_row["B minus A"] > 0
    assert comparison_balance_rows(comparison)[0]["B minus A"] > 0
    assert any(
        row["Input"].endswith("incomes[0].monthly_amount")
        for row in scenario_input_differences(comparison)
    )


def test_paired_comparison_handles_purchase_and_no_purchase_without_overlap():
    assumptions = FinancialAssumptions()
    means = dict.fromkeys(assumptions.investment_return_means, 0)
    deviations = dict.fromkeys(assumptions.investment_return_std_devs, 0)
    controlled = FinancialAssumptions(
        investment_return_means=means,
        investment_return_std_devs=deviations,
        stock_dividend_rate=0,
        contribution_fee=0,
        income_tax_rates=dict(assumptions.income_tax_rates),
    )
    baseline = SimulationConfig(
        years=2, seed=7, initial_cash=30_000, yearly_fee=0,
        investments=[], incomes=[], expenses=[], loans=[], assumptions=controlled,
    )
    workspace = ScenarioWorkspace.from_config(baseline)
    alternative = workspace.config_for(ALTERNATIVE_ID)
    alternative.property_purchase = PropertyPurchase(
        100_000, 20, mortgage_interest_rate=0, purchase_month=0,
        yearly_tax=1_200,
    )
    workspace = workspace.save(ALTERNATIVE_ID, alternative)

    comparison = run_paired_scenario_comparison(workspace, 2)
    balances = {row["Balance"]: row for row in comparison_balance_rows(comparison)}
    tax = next(
        row for row in comparison_cash_flow_rows(comparison)
        if row["Tracked flow"] == "Property ownership tax"
    )

    assert balances["Property equity"]["Alternative B"] > 0
    assert balances["Outstanding debt"]["Alternative B"] > 0
    assert tax["B minus A"] == 1_200
    assert any("structure differs" in warning.lower() for warning in comparison.warnings)


def test_comparison_uses_baseline_horizon_seed_and_warns_on_assumptions():
    workspace = ScenarioWorkspace.from_config(_config())
    alternative = workspace.config_for(ALTERNATIVE_ID)
    alternative.years = 2
    alternative.seed = 99
    alternative.assumptions.contribution_fee = 25
    workspace = workspace.save(ALTERNATIVE_ID, alternative)

    comparison = run_paired_scenario_comparison(workspace, 1)

    assert comparison.horizon_years == 1
    assert comparison.base_seed == 17
    assert comparison.alternative_config.years == 1
    assert comparison.alternative_config.seed == 17
    assert len(comparison.warnings) == 3
