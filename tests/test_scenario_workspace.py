from dataclasses import replace
from datetime import datetime, timezone
import json

import pytest

from app import (
    IncomeConfig,
    InvestmentConfig,
    SimulationConfig,
    create_scenario_workspace_export,
    create_simulation_export,
    read_scenario_workspace_export,
    read_simulation_export,
    run_simulation,
)
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
