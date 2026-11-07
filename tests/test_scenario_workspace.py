from dataclasses import replace
from datetime import datetime, timezone
import json

import pytest

from app import (
    CurveballConfig, ExpenseConfig, FinancialAssumptions, IncomeConfig,
    InvestmentConfig,
    SimulationConfig,
    comparison_balance_rows,
    comparison_cash_flow_rows,
    comparison_explanation,
    build_export_filename,
    build_sensitivity_parameters,
    create_scenario_workspace_export,
    create_simulation_export,
    deterministic_ab_suggestions,
    read_scenario_workspace_export,
    read_simulation_export,
    paired_delta_quantiles,
    run_abc_scenario_sensitivity,
    run_paired_scenario_comparison,
    run_sensitivity_analysis,
    run_simulation,
    scenario_input_differences,
)
from property_purchase import AutomaticPurchasePolicy, PropertyPurchase
from scenario_workspace import (
    ALTERNATIVE_ID,
    BASELINE_ID,
    TERTIARY_ID,
    ScenarioVariant,
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


def test_export_filename_sanitizes_extensions_and_timestamps_blank_names():
    exported_at = datetime(2026, 10, 31, 12, 0, 0, 123456, tzinfo=timezone.utc)

    assert build_export_filename(
        "Plan / A:*?.JSON.json", "Fallback", exported_at,
    ) == "Plan-A.json"
    assert build_export_filename("", "Baseline A", exported_at) == (
        "Baseline-A-20261031-120000-123456.json"
    )
    assert build_export_filename(
        "   ", "Baseline A", exported_at.replace(microsecond=123457),
    ) == "Baseline-A-20261031-120000-123457.json"
    assert build_export_filename(
        "/:*?<>|\\", "Fallback", exported_at,
    ) == "Fallback-20261031-120000-123456.json"


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
    with pytest.raises(ValueError, match="two or three"):
        read_scenario_workspace_export(json.dumps(workspace_document))


def test_abc_scenario_sensitivity_compares_three_saved_variants():
    workspace = ScenarioWorkspace.from_config(_config())
    b_config = workspace.config_for(ALTERNATIVE_ID)
    b_config.curveballs = [CurveballConfig(
        "Temporary job loss", True, 1.0, 1, 1, 3, "Salary", 100.0,
    )]
    workspace = workspace.save(ALTERNATIVE_ID, b_config)
    c_config = replace(
        workspace.config_for(BASELINE_ID),
        property_purchase=PropertyPurchase(10_000, 10),
    )
    workspace = workspace.add_variant(
        ScenarioVariant(TERTIARY_ID, "Alternative C", c_config),
    )

    result = run_abc_scenario_sensitivity(workspace, 1)

    assert [row["Scenario"] for row in result["rows"]] == [
        "Baseline A", "Alternative B", "Alternative C",
    ]
    assert result["rows"][1]["Mean"] < result["rows"][0]["Mean"]
    restored = read_scenario_workspace_export(create_scenario_workspace_export(workspace))
    assert restored.config_for(TERTIARY_ID) == c_config


def test_deterministic_ab_suggestions_offer_reproducible_input_based_variants():
    config = _config()

    first = deterministic_ab_suggestions(config)
    second = deterministic_ab_suggestions(config)

    assert [item.key for item in first] == [
        "automatic_property", "increase_investing", "larger_cash_buffer",
    ]
    assert [(item.title, item.description) for item in first] == [
        (item.title, item.description) for item in second
    ]
    property_suggestion = first[0].config.property_purchase
    assert property_suggestion is not None
    assert property_suggestion.purchase.purchase_price == 145_000
    assert first[1].config.investments[0].monthly_contribution == 100
    assert first[2].config.initial_cash == 6_000


def test_deterministic_ab_suggestions_remove_property_when_already_configured():
    config = _config()
    config.property_purchase = PropertyPurchase(
        120_000, 20, transaction_fees=3_600, purchase_month=0,
    )

    suggestions = deterministic_ab_suggestions(config)

    assert suggestions[0].key == "without_property"
    assert suggestions[0].config.property_purchase is None
    assert suggestions[0].config.selected_rent_expense is None


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
    explanation = comparison_explanation(comparison)
    assert "approximately the same average net worth" in explanation
    assert "details below" in explanation


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
    explanation = comparison_explanation(comparison)
    assert "average net worth of about" in explanation
    assert "details below" in explanation


def test_comparison_explanation_uses_popup_suggestion_name():
    config = _config()
    suggestion = deterministic_ab_suggestions(config)[0]
    workspace = ScenarioWorkspace.from_config(config).rename(
        BASELINE_ID, "Current scenario",
    ).rename(ALTERNATIVE_ID, suggestion.title).save(
        ALTERNATIVE_ID, suggestion.config,
    )

    explanation = comparison_explanation(run_paired_scenario_comparison(workspace, 1))

    assert f"{suggestion.title} (B)" in explanation
    assert "details below" in explanation


def test_comparison_explanation_treats_sub_dollar_delta_as_negligible():
    workspace = ScenarioWorkspace.from_config(_config())
    alternative = workspace.config_for(ALTERNATIVE_ID)
    alternative.initial_cash += 0.1
    workspace = workspace.save(ALTERNATIVE_ID, alternative)

    explanation = comparison_explanation(run_paired_scenario_comparison(workspace, 1))

    assert "approximately the same average net worth" in explanation


def test_comparison_explanation_handles_added_removed_and_multiple_inputs():
    config = _config()
    workspace = ScenarioWorkspace.from_config(config)
    alternative = workspace.config_for(ALTERNATIVE_ID)
    alternative.investments = []
    alternative.initial_cash += 1_000
    alternative.expenses = [ExpenseConfig("Food", "groceries", 200)]
    workspace = workspace.save(ALTERNATIVE_ID, alternative)

    explanation = comparison_explanation(run_paired_scenario_comparison(workspace, 1))

    assert "average net worth of about" in explanation
    assert "details below" in explanation


def test_comparison_explanation_preserves_removed_item_identity_after_shift():
    config = _config()
    config.incomes = [
        IncomeConfig("Salary", "salary", 3_000, 0),
        IncomeConfig("Side job", "salary", 500, 0),
    ]
    workspace = ScenarioWorkspace.from_config(config)
    alternative = workspace.config_for(ALTERNATIVE_ID)
    alternative.incomes = [alternative.incomes[1]]
    workspace = workspace.save(ALTERNATIVE_ID, alternative)

    explanation = comparison_explanation(run_paired_scenario_comparison(workspace, 1))

    assert "average net worth of about" in explanation
    assert "details below" in explanation


def test_sensitivity_analysis_ranks_paired_variations_without_mutating_config():
    config = _config()
    before = config.initial_cash

    result = run_sensitivity_analysis(config, 2)

    assert config.initial_cash == before
    assert result["simulation_count"] == 2
    assert result["rows"]
    assert result["rows"][0]["Impact"] >= result["rows"][-1]["Impact"]
    assert all("Low 10th–90th" in row for row in result["rows"])


def test_sensitivity_parameters_skip_zero_values_and_bound_rates():
    config = SimulationConfig(
        initial_cash=0, investments=[], incomes=[], expenses=[], loans=[],
    )

    parameters = build_sensitivity_parameters(config)

    assert parameters == []


def test_sensitivity_variants_replace_frozen_purchase_templates():
    config = _config()
    config.property_purchase = AutomaticPurchasePolicy(
        PropertyPurchase(100_000, 20, mortgage_interest_rate=4), 0, 0,
    )

    parameters = build_sensitivity_parameters(config, limit=100)

    mortgage = next(item for item in parameters if item.key == "mortgage_rate")
    assert mortgage.low_config.property_purchase.purchase.mortgage_interest_rate == 3
    assert mortgage.high_config.property_purchase.purchase.mortgage_interest_rate == 5
    assert config.property_purchase.purchase.mortgage_interest_rate == 4


def test_shortfall_sensitivity_counts_insolvent_trajectories():
    config = SimulationConfig(
        years=1, initial_cash=100, yearly_fee=0,
        investments=[],
        incomes=[IncomeConfig("Salary", "salary", 1_000, 0)],
        expenses=[ExpenseConfig("Living", "housing", 690)],
        loans=[],
    )

    result = run_sensitivity_analysis(
        config, 2, metric="cash_shortfall_probability",
    )

    assert result["rows"]
    income_row = next(row for row in result["rows"] if "income" in row["Parameter"])
    assert income_row["Low"] == 1.0


def test_comparison_warns_when_bonus_changes_draws():
    workspace = ScenarioWorkspace.from_config(_config())
    alternative = workspace.config_for(ALTERNATIVE_ID)
    alternative.incomes[0].bonus_pct = 10
    workspace = workspace.save(ALTERNATIVE_ID, alternative)

    comparison = run_paired_scenario_comparison(workspace, 1)

    assert any("structure differs" in warning.lower() for warning in comparison.warnings)


def test_comparison_warns_when_additional_unexpected_expense_changes_draws():
    workspace = ScenarioWorkspace.from_config(_config())
    alternative = workspace.config_for(ALTERNATIVE_ID)
    alternative.additional_expenses.append(
        ExpenseConfig("Emergencies", "unexpected", 0)
    )
    workspace = workspace.save(ALTERNATIVE_ID, alternative)

    comparison = run_paired_scenario_comparison(workspace, 1)

    assert any("structure differs" in warning.lower() for warning in comparison.warnings)


def test_input_differences_preserve_empty_none_and_missing_values():
    baseline = SimulationConfig(
        years=1, investments=None, incomes=[], expenses=[], loans=[],
    )
    workspace = ScenarioWorkspace.from_config(baseline)
    alternative = workspace.config_for(ALTERNATIVE_ID)
    alternative.investments = []
    workspace = workspace.save(ALTERNATIVE_ID, alternative)

    comparison = run_paired_scenario_comparison(workspace, 1)
    differences = scenario_input_differences(comparison)

    assert {
        "Input": "investments", "Baseline A": None, "Alternative B": [],
    } in differences

    baseline = replace(baseline, investments=[
        InvestmentConfig("Savings", "safe", 1_000, 0, linked_loan_name=None),
    ])
    workspace = ScenarioWorkspace.from_config(baseline)
    alternative = workspace.config_for(ALTERNATIVE_ID)
    alternative.investments = []
    workspace = workspace.save(ALTERNATIVE_ID, alternative)

    differences = scenario_input_differences(
        run_paired_scenario_comparison(workspace, 1)
    )
    assert {
        "Input": "investments[0].linked_loan_name",
        "Baseline A": None,
        "Alternative B": "(not present)",
    } in differences


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
