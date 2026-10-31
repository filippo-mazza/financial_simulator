import json
import math
from dataclasses import replace

import pytest

from app import (
    ExpenseConfig,
    IncomeConfig,
    InvestmentConfig,
    LoanConfig,
    SimulationConfig,
    create_simulation_export,
    read_simulation_export,
    resolved_property_purchase,
    run_simulation,
)
from financial_assumptions import FinancialAssumptions
from property_jurisdiction import (
    PropertyJurisdictionProfile,
    estimate_property_costs,
    profiles_by_name,
)
from property_purchase import AutomaticPurchasePolicy, PropertyPurchase


def _zero_property_returns():
    defaults = FinancialAssumptions()
    means = dict(defaults.investment_return_means)
    deviations = dict(defaults.investment_return_std_devs)
    means["real_estate"] = 0
    deviations["real_estate"] = 0
    return FinancialAssumptions(
        investment_return_means=means,
        investment_return_std_devs=deviations,
    )


@pytest.mark.parametrize((
    "profile", "value", "rent", "ownership", "expected",
), [
    (
        PropertyJurisdictionProfile("Fictional North", 5, 1, 1.2, 10),
        200_000, 1_000, 50, (10_000, 1_000, 100, 100, 200),
    ),
    (
        PropertyJurisdictionProfile("Fictional South", 2, 0.5, 2.4, 20),
        100_000, 500, 25, (2_000, 125, 50, 100, 150),
    ),
])
def test_profile_estimates_scale_value_costs_by_ownership_but_not_user_rent(
        profile, value, rent, ownership, expected):
    estimate = estimate_property_costs(profile, value, rent, ownership)

    assert (
        estimate.acquisition_costs,
        estimate.annual_property_tax,
        estimate.monthly_maintenance,
        estimate.monthly_rental_expenses,
        estimate.monthly_operating_costs,
    ) == expected


def test_profiles_validate_editable_rates_and_unique_names():
    first = PropertyJurisdictionProfile("Fictional A", 1, 2, 3, 4)
    second = PropertyJurisdictionProfile("Fictional B", 4, 3, 2, 1)

    assert set(profiles_by_name([first, second])) == {"Fictional A", "Fictional B"}
    with pytest.raises(ValueError, match="unique"):
        profiles_by_name([first, first])
    with pytest.raises(ValueError, match="between 0 and 100"):
        PropertyJurisdictionProfile("Fictional C", annual_property_tax_percentage=101)


def test_selected_profile_adds_existing_property_costs_once():
    profile = PropertyJurisdictionProfile(
        "Fictional Rental District", 5, 1, 1.2, 10,
    )
    config = SimulationConfig(
        years=2,
        initial_cash=10_000,
        yearly_fee=0,
        investments=[InvestmentConfig(
            "Synthetic rental", "real_estate", 200_000, 0,
            monthly_expenses=20,
            yearly_tax=120,
            ownership_percentage=50,
            rental_income_name="Synthetic rent",
            jurisdiction_profile_name=profile.name,
        )],
        incomes=[IncomeConfig("Synthetic rent", "rent", 1_000, 0)],
        expenses=[],
        loans=[],
        assumptions=_zero_property_returns(),
        jurisdiction_profiles=[profile],
    )

    details = run_simulation(config)[2]
    costs = details["property_operating_costs"]["Synthetic rental"]

    assert all(math.isclose(amount, 220) for amount in costs[:11])
    assert math.isclose(costs[11], 1_340)
    assert math.isclose(costs[12], 223.3)
    assert math.isclose(costs[22], 223.3)
    assert math.isclose(costs[23], 1_360.1)
    monthly_expenses = details["expense_values"][
        "Synthetic rental — property expenses (linked)"
    ]
    assert all(math.isclose(amount, 220) for amount in monthly_expenses[:12])
    assert all(
        math.isclose(amount, 223.3) for amount in monthly_expenses[12:]
    )
    yearly_tax = details["expense_values"][
        "Synthetic rental — property tax (linked)"
    ]
    assert yearly_tax[:11] == [0] * 11
    assert math.isclose(yearly_tax[11], 1_120)
    assert yearly_tax[12:23] == [0] * 11
    assert math.isclose(yearly_tax[23], 1_136.8)


def test_unassigned_profile_preserves_legacy_simulation_series():
    legacy_config = SimulationConfig(
        years=2,
        seed=1729,
        initial_cash=20_000,
        yearly_fee=0,
        investments=[
            InvestmentConfig(
                "Synthetic home", "real_estate", 200_000, 0,
                monthly_expenses=20,
                yearly_tax=120,
                ownership_percentage=50,
                linked_loan_name="Synthetic mortgage",
                rental_income_name="Synthetic rent",
            ),
            InvestmentConfig("Synthetic stocks", "stock", 5_000, 100),
        ],
        incomes=[IncomeConfig("Synthetic rent", "rent", 1_000, 0)],
        expenses=[ExpenseConfig("Living costs", "utilities", 500)],
        loans=[LoanConfig("Synthetic mortgage", 40_000, 2, 10)],
        assumptions=_zero_property_returns(),
    )
    profile_config = replace(
        legacy_config,
        jurisdiction_profiles=[
            PropertyJurisdictionProfile("Unassigned profile", 5, 1, 1.2, 10)
        ],
    )

    legacy_result = run_simulation(legacy_config)
    profile_result = run_simulation(profile_config)

    assert legacy_result[0] == profile_result[0]
    assert legacy_result[1] == profile_result[1]
    for key in ("bank_account", "investment_values", "loan_balances"):
        assert legacy_result[2][key] == profile_result[2][key]


def test_seeded_profile_estimates_and_purchase_outcome_are_reproducible():
    profile = PropertyJurisdictionProfile(
        "Fictional Reproducible District", 5, 1, 1.2, 10,
    )
    config = SimulationConfig(
        years=2,
        seed=8675309,
        initial_cash=80_000,
        yearly_fee=0,
        investments=[
            InvestmentConfig(
                "Synthetic rental", "real_estate", 200_000, 0,
                monthly_expenses=20,
                yearly_tax=120,
                ownership_percentage=50,
                rental_income_name="Synthetic rent",
                jurisdiction_profile_name=profile.name,
            ),
            InvestmentConfig("Synthetic stocks", "stock", 5_000, 100),
        ],
        incomes=[IncomeConfig("Synthetic rent", "rent", 1_000, 0)],
        expenses=[],
        loans=[],
        assumptions=_zero_property_returns(),
        property_purchase=AutomaticPurchasePolicy(
            PropertyPurchase(
                100_000, 20, transaction_fees=1_000,
                monthly_expenses=10, yearly_tax=100,
            ),
            minimum_available_cash=10_000,
            minimum_cash_reserve=2_000,
        ),
        jurisdiction_profiles=[profile],
        property_purchase_jurisdiction_profile=profile.name,
    )

    first = run_simulation(config)
    second = run_simulation(config)

    assert first == second
    assert first[2]["purchase_outcome"]["purchased"] is True
    assert first[2]["purchase_events"][0]["transaction_fees"] == 6_000
    assert math.isclose(
        first[2]["property_operating_costs"]["Synthetic rental"][0], 220
    )


def test_purchase_profile_resolves_to_dollar_costs_without_mutating_input():
    profile = PropertyJurisdictionProfile(
        "Fictional Purchase District", 5, 1, 1.2, 10,
    )
    purchase = PropertyPurchase(
        100_000, 20, transaction_fees=1_000,
        monthly_expenses=10, yearly_tax=100,
    )
    policy = AutomaticPurchasePolicy(purchase, 20_000, 2_000)
    config = SimulationConfig(
        property_purchase=policy,
        property_purchase_jurisdiction_profile=profile.name,
        jurisdiction_profiles=[profile],
    )

    resolved = resolved_property_purchase(config)

    assert resolved.purchase.transaction_fees == 6_000
    assert resolved.purchase.monthly_expenses == 110
    assert resolved.purchase.yearly_tax == 1_100
    assert resolved.minimum_required_cash == 28_000
    assert purchase.transaction_fees == 1_000


def test_jurisdiction_configuration_round_trips_and_legacy_export_defaults_empty():
    profile = PropertyJurisdictionProfile("Fictional Export", 2, 0.5, 1, 5)
    config = SimulationConfig(
        years=1,
        investments=[], incomes=[], expenses=[], loans=[],
        jurisdiction_profiles=[profile],
    )
    result = run_simulation(config)

    exported = create_simulation_export(config, *result)
    restored = read_simulation_export(exported)
    legacy = json.loads(exported)
    del legacy["parameters"]["jurisdiction_profiles"]
    del legacy["parameters"]["property_purchase_jurisdiction_profile"]

    assert restored.jurisdiction_profiles == [profile]
    assert read_simulation_export(json.dumps(legacy)).jurisdiction_profiles == []


def test_unknown_selected_profile_is_rejected_before_simulation():
    config = SimulationConfig(
        investments=[InvestmentConfig(
            "Synthetic home", "real_estate", 100_000, 0,
            jurisdiction_profile_name="Missing profile",
        )],
        incomes=[], expenses=[], loans=[],
    )

    with pytest.raises(ValueError, match="Unknown property jurisdiction profile"):
        run_simulation(config)


# --- researched-catalog and extended-schema coverage -----------------------

from property_jurisdiction import (  # noqa: E402
    GENERIC_PROFILE_NAME,
    default_property_jurisdiction_profiles,
    preset_update_notices,
)


def test_catalog_has_generic_profile_and_provenance_for_every_preset():
    catalog = default_property_jurisdiction_profiles()
    assert catalog[0].name == GENERIC_PROFILE_NAME
    assert estimate_property_costs(catalog[0], 300_000).acquisition_costs == 0
    presets = catalog[1:]
    assert len(presets) >= 5
    assert not any("Fictional" in profile.name for profile in catalog)
    assert len({profile.profile_id for profile in catalog}) == len(catalog)
    for profile in presets:
        assert profile.source_url.startswith("https://")
        assert profile.data_as_of and profile.unsupported_cases
        assert profile.verification_status == "unverified"
        assert profile.acquisition_cost_basis in {"statutory", "indicative"}
        assert profile.property_tax_basis == "user"
        assert profile.property_tax_base == "assessed_value"


def test_breakdown_sums_to_acquisition_costs_with_percentage_and_fixed_parts():
    by_id = {p.profile_id: p for p in default_property_jurisdiction_profiles()}
    bavaria = estimate_property_costs(by_id["de-bavaria-resale"], 400_000)
    assert [(l, a, b) for l, a, b in bavaria.acquisition_breakdown] == [
        ("Transfer / registration taxes", 14_000, "statutory"),
        ("Other transaction costs", 8_000, "indicative"),
    ]
    lazio = estimate_property_costs(by_id["it-lazio-resale-primary"], 200_000)
    assert lazio.acquisition_costs == 4_100
    assert lazio.acquisition_breakdown[-1] == ("Fixed fees", 100, "statutory")
    for profile in by_id.values():
        estimate = estimate_property_costs(profile, 250_000)
        assert math.isclose(
            estimate.acquisition_costs,
            sum(amount for _, amount, _ in estimate.acquisition_breakdown),
        )
        assert estimate.acquisition_costs >= 0
        assert estimate_property_costs(profile, 0).acquisition_costs == \
            profile.fixed_acquisition_costs


def test_preset_override_and_unsupported_text_survive_export_round_trip():
    preset = default_property_jurisdiction_profiles()[1]
    from dataclasses import replace as _replace
    overridden = _replace(
        preset, acquisition_cost_percentage=1.0, acquisition_cost_basis="user",
    )
    config = SimulationConfig(
        years=1, investments=[], incomes=[], expenses=[], loans=[],
        jurisdiction_profiles=[overridden],
    )
    restored = read_simulation_export(
        create_simulation_export(config, *run_simulation(config))
    ).jurisdiction_profiles[0]
    assert restored == overridden
    assert restored.version == preset.version
    assert restored.profile_id == preset.profile_id
    assert restored.unsupported_cases == preset.unsupported_cases
    assert estimate_property_costs(restored, 100_000).acquisition_costs == 1_000


def test_legacy_export_profile_loads_unchanged_as_user_defined():
    legacy = {
        "name": "Fictional Metro", "acquisition_cost_percentage": 4.0,
        "annual_property_tax_percentage": 1.0,
        "annual_maintenance_percentage": 1.0, "rental_expense_percentage": 8.0,
    }
    config = SimulationConfig(
        years=1, investments=[], incomes=[], expenses=[], loans=[],
        jurisdiction_profiles=[PropertyJurisdictionProfile("x")],
    )
    exported = json.loads(create_simulation_export(config, *run_simulation(config)))
    exported["parameters"]["jurisdiction_profiles"] = [legacy]
    restored = read_simulation_export(json.dumps(exported)).jurisdiction_profiles[0]
    assert restored == PropertyJurisdictionProfile(**legacy)
    assert restored.acquisition_cost_basis == "user"
    assert restored.version == 1 and restored.profile_id == ""


def test_newer_catalog_version_only_produces_notice_and_keeps_saved_values():
    preset = default_property_jurisdiction_profiles()[1]
    from dataclasses import replace as _replace
    old = _replace(preset, version=0 + 1)
    assert preset_update_notices([old]) == []
    import property_jurisdiction as module
    original = module.default_property_jurisdiction_profiles
    module.default_property_jurisdiction_profiles = lambda: [
        _replace(preset, version=2, acquisition_cost_percentage=9.0)
    ]
    try:
        notices = preset_update_notices([old])
    finally:
        module.default_property_jurisdiction_profiles = original
    assert len(notices) == 1 and "left unchanged" in notices[0]
    assert old.acquisition_cost_percentage == preset.acquisition_cost_percentage


@pytest.mark.parametrize("kwargs", [
    {"acquisition_cost_basis": "official"},
    {"property_tax_base": "rent"},
    {"fixed_acquisition_costs": -1},
    {"version": 0},
    {"source_url": "http://example.com"},
    {"other_transaction_cost_percentage": 101},
])
def test_extended_profile_fields_are_validated(kwargs):
    with pytest.raises(ValueError):
        PropertyJurisdictionProfile("Fictional X", **kwargs)


def test_real_world_profile_drives_purchase_fees_once():
    bavaria = next(
        p for p in default_property_jurisdiction_profiles()
        if p.profile_id == "de-bavaria-resale"
    )
    config = SimulationConfig(
        property_purchase=PropertyPurchase(
            100_000, 20, transaction_fees=1_000, monthly_expenses=10),
        property_purchase_jurisdiction_profile=bavaria.name,
        jurisdiction_profiles=[bavaria],
    )
    resolved = resolved_property_purchase(config)
    assert resolved.transaction_fees == 1_000 + 5_500
    assert resolved.monthly_expenses == 10 + 100_000 * 0.01 / 12
