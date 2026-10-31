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

from dataclasses import replace as _replace  # noqa: E402

import property_jurisdiction_catalog as catalog_module  # noqa: E402
from property_jurisdiction import (  # noqa: E402
    AcquisitionComponent,
    describe_profile_assumptions,
)
from property_jurisdiction_catalog import (  # noqa: E402
    GENERIC_PROFILE_NAME,
    default_property_jurisdiction_profiles,
    preset_update_notices,
)


def _preset(profile_id):
    return next(
        p for p in default_property_jurisdiction_profiles()
        if p.profile_id == profile_id
    )


def _acquisition(profile_id, price, **kwargs):
    return estimate_property_costs(_preset(profile_id), price, **kwargs)


def test_catalog_has_generic_profile_and_exact_provenance_for_every_preset():
    catalog = default_property_jurisdiction_profiles()
    assert catalog[0].name == GENERIC_PROFILE_NAME
    assert estimate_property_costs(catalog[0], 300_000).acquisition_costs == 0
    presets = catalog[1:]
    assert len(presets) >= 8
    assert not any("Fictional" in profile.name for profile in catalog)
    assert len({profile.profile_id for profile in catalog}) == len(catalog)
    assert len({profile.name for profile in catalog}) == len(catalog)
    for profile in presets:
        assert profile.source_url.startswith("https://")
        assert profile.source_url.count("/") > 3, "must be a specific page, not a homepage"
        assert profile.data_as_of == "2026-10-31"
        assert profile.unsupported_cases and profile.purchase_type
        # Profiles with omissions are never labelled fully verified.
        assert profile.verification_status in {"partially_verified", "unverified"}
        assert profile.property_tax_basis == "user"
        assert profile.annual_property_tax_percentage == 0
        assert profile.property_tax_base == "assessed_value"
        for component in profile.acquisition_components:
            if component.basis == "statutory":
                assert component.verified and component.source_url
            if component.basis == "indicative":
                assert not component.verified


def test_every_preset_breakdown_sums_exactly_and_components_are_separate():
    for profile in default_property_jurisdiction_profiles():
        estimate = estimate_property_costs(
            profile, 250_000, mortgage_amount=150_000,
        )
        assert estimate.acquisition_costs == sum(
            amount for _, amount, _ in estimate.acquisition_breakdown
        )
        assert estimate.acquisition_costs >= 0
        assert all(basis in {"statutory", "indicative", "user"}
                   for _, _, basis in estimate.acquisition_breakdown)
    austria = _acquisition("at-resale", 400_000, mortgage_amount=300_000)
    expected = (
        ("Grunderwerbsteuer (transfer tax)", 14_000, "statutory"),
        ("Land-register ownership entry", 4_400, "statutory"),
        ("Mortgage pledge registration", 3_600, "statutory"),
        ("Pledge application fee", 85, "statutory"),
        ("Legal / notary fees (budget allowance)", 8_000, "indicative"),
    )
    assert len(austria.acquisition_breakdown) == len(expected)
    for (label, amount, basis), (e_label, e_amount, e_basis) in zip(
            austria.acquisition_breakdown, expected):
        assert (label, basis) == (e_label, e_basis)
        assert math.isclose(amount, e_amount)
    cash_only = _acquisition("at-resale", 400_000)
    assert [label for label, _, _ in cash_only.acquisition_breakdown] == [
        "Grunderwerbsteuer (transfer tax)", "Land-register ownership entry",
        "Legal / notary fees (budget allowance)",
    ]
    bavaria = _acquisition("de-bavaria-resale", 400_000)
    assert [(l, b) for l, _, b in bavaria.acquisition_breakdown] == [
        ("Grunderwerbsteuer (transfer tax)", "statutory"),
        ("Notary and land-register fees (budget allowance)", "indicative"),
    ]
    assert all(math.isclose(a, e) for (_, a, _), e in zip(
        bavaria.acquisition_breakdown, [14_000, 8_000]))


@pytest.mark.parametrize(("price", "expected"), [
    (600_000, 60_000), (600_001, 60_000.11), (700_000, 71_000),
    (900_000, 93_000), (900_001, 93_000.12), (1_500_000, 165_000),
    (2_000_000, 230_000),
])
def test_catalonia_itp_uses_marginal_bands_at_boundaries(price, expected):
    assert math.isclose(
        _acquisition("es-catalonia-resale", price).acquisition_costs,
        expected, abs_tol=0.01,
    )


def test_portugal_imt_marginal_bands_flat_switch_and_stamp_duty():
    def imt(price, **kwargs):
        estimate = _acquisition("pt-resale-hpp", price, assessed_value=price)
        return next((amount for label, amount, _ in estimate.acquisition_breakdown
                     if label.startswith("IMT")), 0.0)

    assert imt(106_346) == 0
    assert math.isclose(imt(145_470), (145_470 - 106_346) * 0.02)
    marginal_top = (
        (145_470 - 106_346) * 0.02 + (198_347 - 145_470) * 0.05
        + (330_539 - 198_347) * 0.07 + (660_982 - 330_539) * 0.08
    )
    assert math.isclose(imt(660_982), marginal_top)
    assert math.isclose(imt(660_983), 660_983 * 0.06)  # flat on whole base
    assert math.isclose(imt(1_150_853), 1_150_853 * 0.06)
    assert math.isclose(imt(1_150_854), 1_150_854 * 0.075)
    full = _acquisition("pt-resale-hpp", 500_000)
    assert full.acquisition_breakdown[-1][0] == "Stamp duty"
    assert math.isclose(full.acquisition_breakdown[-1][1], 4_000)


def test_portugal_uses_higher_of_price_and_assessed_value_and_warns_when_missing():
    no_vpt = _acquisition("pt-resale-hpp", 200_000)
    assert any("assessed value not entered" in w for w in no_vpt.warnings)
    assert all(basis == "indicative" for _, _, basis in no_vpt.acquisition_breakdown)
    higher = _acquisition("pt-resale-hpp", 200_000, assessed_value=300_000)
    lower = _acquisition("pt-resale-hpp", 200_000, assessed_value=100_000)
    assert higher.warnings == () and higher.acquisition_costs > no_vpt.acquisition_costs
    assert lower.acquisition_costs == no_vpt.acquisition_costs
    assert math.isclose(higher.acquisition_breakdown[-1][1], 2_400)


def test_lazio_minimum_cadastral_base_and_proxy_warning():
    # EUR 40,000 price: 2% would be 800, the EUR 1,000 minimum applies (+100).
    proxy = _acquisition("it-lazio-resale-primary", 40_000)
    assert proxy.acquisition_costs == 1_100
    assert proxy.warnings and "proxy" in proxy.warnings[0]
    assert proxy.acquisition_breakdown[0][2] == "indicative"
    # Same sale price, different cadastral income -> different tax.
    low = _acquisition("it-lazio-resale-primary", 200_000, cadastral_income=400)
    high = _acquisition("it-lazio-resale-primary", 200_000, cadastral_income=1_500)
    assert low.warnings == () == high.warnings
    assert low.acquisition_breakdown[0] == (
        "Registration tax (prima casa)", 1_000, "statutory")  # 400*115.5*2% = 924 < min
    assert math.isclose(high.acquisition_breakdown[0][1], 1_500 * 1.05 * 110 * 0.02)
    assert low.acquisition_costs == 1_100
    assert high.acquisition_costs != low.acquisition_costs
    # Same inputs, price-independent once the cadastral base is supplied.
    assert _acquisition(
        "it-lazio-resale-primary", 900_000, cadastral_income=1_500,
    ).acquisition_costs == high.acquisition_costs
    second = _acquisition("it-lazio-resale-second", 200_000, cadastral_income=1_000)
    assert math.isclose(second.acquisition_breakdown[0][1], 1_000 * 1.05 * 120 * 0.09)


def test_netherlands_transaction_types_and_starter_exemption_threshold():
    assert _acquisition("nl-resale-owner-occupied", 400_000).acquisition_costs == 8_000
    assert _acquisition("nl-resale-investor", 400_000).acquisition_costs == 32_000
    assert _acquisition("nl-resale-starter", 555_000).acquisition_costs == 0
    assert math.isclose(
        _acquisition("nl-resale-starter", 555_001).acquisition_costs, 555_001 * 0.02)
    assert "NOT checked" in _preset("nl-resale-starter").unsupported_cases


def test_madrid_general_and_habitual_home_relief():
    assert _acquisition("es-madrid-resale", 250_000).acquisition_costs == 15_000
    relief = _acquisition("es-madrid-resale-habitual", 250_000)
    assert math.isclose(relief.acquisition_costs, 13_500)  # 10% off the 6% tax
    assert math.isclose(
        _acquisition("es-madrid-resale-habitual", 250_001).acquisition_costs,
        250_001 * 0.06)
    assert "NOT checked" in _preset("es-madrid-resale-habitual").unsupported_cases


def test_france_combined_rates_security_contribution_and_notary_tariff():
    four = _acquisition("fr-existing-4-5", 300_000)
    five = _acquisition("fr-existing-5", 300_000)
    assert math.isclose(four.acquisition_breakdown[0][1], 300_000 * 0.0580665)
    assert math.isclose(five.acquisition_breakdown[0][1], 300_000 * 0.063185)
    assert math.isclose(four.acquisition_breakdown[1][1], 300)
    tariff = 6_500 * 0.0387 + 10_500 * 0.01596 + 43_000 * 0.01064 + 240_000 * 0.00799
    assert math.isclose(four.acquisition_breakdown[2][1], tariff)
    small = _acquisition("fr-existing-4-5", 1_000)
    assert small.acquisition_breakdown[1][1] == 15  # minimum
    assert _preset("fr-new").verification_status == "unverified"


def test_unsupported_cases_are_documented_for_every_preset_and_help_text_lists_them():
    for profile in default_property_jurisdiction_profiles()[1:]:
        text = describe_profile_assumptions(profile)
        assert "Not covered" in text and profile.source_url in text
        assert profile.name in text
    assert "0 does not mean no tax" in _preset("pt-resale-hpp").notes


def test_component_override_replaces_method_and_is_reported_as_user():
    preset = _preset("es-catalonia-resale")
    component = _replace(preset.acquisition_components[0], override_percentage=4.0)
    overridden = _replace(preset, acquisition_components=(component,))
    estimate = estimate_property_costs(overridden, 700_000)
    assert estimate.acquisition_costs == 28_000
    assert estimate.acquisition_breakdown[0][2] == "user"
    assert component.effective_verified is False


def test_preset_override_and_components_survive_export_round_trip():
    preset = _preset("pt-resale-hpp")
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
    assert restored.version == preset.version == 2
    assert restored.acquisition_components == preset.acquisition_components
    assert restored.additional_sources == preset.additional_sources


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
    del exported["parameters"]["purchase_cadastral_income"]
    del exported["parameters"]["purchase_assessed_value"]
    restored_config = read_simulation_export(json.dumps(exported))
    restored = restored_config.jurisdiction_profiles[0]
    assert restored == PropertyJurisdictionProfile(**legacy)
    assert restored.acquisition_cost_basis == "user"
    assert restored.version == 1 and restored.acquisition_components == ()
    assert restored_config.purchase_cadastral_income is None
    assert estimate_property_costs(restored, 100_000).acquisition_costs == 4_000


def test_saved_older_preset_version_is_kept_and_only_produces_a_notice():
    current = _preset("de-bavaria-resale")
    saved = _replace(
        current, version=1, acquisition_components=(),
        acquisition_cost_percentage=5.5, acquisition_cost_basis="user",
    )
    notices = preset_update_notices([saved])
    assert len(notices) == 1 and "left unchanged" in notices[0]
    assert preset_update_notices([current]) == []
    assert preset_update_notices([_replace(saved, profile_id="")]) == []
    config = SimulationConfig(
        years=1, investments=[], incomes=[], expenses=[], loans=[],
        jurisdiction_profiles=[saved],
    )
    restored = read_simulation_export(
        create_simulation_export(config, *run_simulation(config))
    ).jurisdiction_profiles[0]
    assert restored == saved
    assert estimate_property_costs(restored, 100_000).acquisition_costs == 5_500


@pytest.mark.parametrize("kwargs", [
    {"acquisition_cost_basis": "official"},
    {"property_tax_base": "rent"},
    {"fixed_acquisition_costs": -1},
    {"version": 0},
    {"source_url": "http://example.com"},
    {"other_transaction_cost_percentage": 101},
    {"verification_status": "certified"},
    {"purchase_type": "auction"},
    {"additional_sources": ("ftp://x",)},
    {"acquisition_components": ("bad",)},
    {"acquisition_components": ({"label": "x", "method": "brackets"},)},
    {"acquisition_components": (
        {"label": "x", "method": "brackets", "brackets": ((100, 1), (50, 2), (None, 3))},
    )},
    {"acquisition_components": (
        {"label": "x", "method": "brackets", "brackets": ((100, 1),)},
    )},
    {"acquisition_components": ({"label": "x", "base": "market"},)},
    {"acquisition_components": ({"label": "x", "rate_percentage": 101},)},
])
def test_extended_profile_fields_are_validated(kwargs):
    with pytest.raises(ValueError):
        PropertyJurisdictionProfile("Fictional X", **kwargs)


def test_estimate_rejects_invalid_optional_inputs():
    profile = PropertyJurisdictionProfile("Fictional X")
    with pytest.raises(ValueError):
        estimate_property_costs(profile, 1, mortgage_amount=-1)
    with pytest.raises(ValueError):
        estimate_property_costs(profile, 1, cadastral_income=-1)


def test_real_world_profile_drives_purchase_fees_once_with_financed_amount():
    austria = _preset("at-resale")
    config = SimulationConfig(
        property_purchase=PropertyPurchase(
            400_000, 25, transaction_fees=1_000, monthly_expenses=10),
        property_purchase_jurisdiction_profile=austria.name,
        jurisdiction_profiles=[austria],
    )
    resolved = resolved_property_purchase(config)
    # financed 300,000: 4.6% + 2% allowance on price, 1.2% pledge on loan, EUR 85
    assert math.isclose(resolved.transaction_fees, 1_000 + 26_400 + 3_600 + 85)
    assert math.isclose(resolved.monthly_expenses, 10 + 400_000 * 0.01 / 12)
    cash_buyer = _replace(config, property_purchase=PropertyPurchase(
        400_000, 100, transaction_fees=1_000, monthly_expenses=10))
    assert math.isclose(
        resolved_property_purchase(cash_buyer).transaction_fees, 1_000 + 26_400)


def test_lazio_purchase_uses_configured_cadastral_income():
    lazio = _preset("it-lazio-resale-primary")
    base = SimulationConfig(
        property_purchase=PropertyPurchase(200_000, 20),
        property_purchase_jurisdiction_profile=lazio.name,
        jurisdiction_profiles=[lazio],
    )
    proxy = resolved_property_purchase(base).transaction_fees
    with_income = resolved_property_purchase(
        _replace(base, purchase_cadastral_income=1_500)).transaction_fees
    assert proxy == 4_100
    assert math.isclose(with_income, 1_500 * 1.05 * 110 * 0.02 + 100)
