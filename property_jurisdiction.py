"""Editable, jurisdiction-specific estimates for property costs.

Profiles are deliberately data-only.  The simulation engine continues to work
with dollar-denominated transaction and operating costs; this module converts
transparent percentage assumptions into those amounts before a run starts.
"""

from dataclasses import dataclass
import math


VALUE_BASES = ("statutory", "indicative", "user")
PROPERTY_TAX_BASES = ("market_value", "assessed_value")
VERIFICATION_STATUSES = ("unverified", "verified")
_TEXT_FIELDS = (
    "profile_id", "transaction_scope", "unsupported_cases", "source_url",
    "data_as_of", "notes",
)


@dataclass(frozen=True)
class PropertyJurisdictionProfile:
    """Property-cost assumptions for one named jurisdiction.

    The first five numeric fields are the original contract.  Every later
    field is optional metadata or an additive allowance, so exports written
    before they existed load unchanged as user-defined profiles.

    ``*_basis`` labels say whether a value is a ``statutory`` rate, an
    ``indicative`` planning allowance, or a ``user`` assumption.  Presets
    carry a ``profile_id`` and ``version``; scenarios store a full copy, so a
    newer catalog version never alters an existing scenario.
    """

    name: str
    acquisition_cost_percentage: float = 0.0
    annual_property_tax_percentage: float = 0.0
    annual_maintenance_percentage: float = 0.0
    rental_expense_percentage: float = 0.0
    other_transaction_cost_percentage: float = 0.0
    fixed_acquisition_costs: float = 0.0
    acquisition_cost_basis: str = "user"
    other_transaction_cost_basis: str = "user"
    fixed_acquisition_cost_basis: str = "user"
    property_tax_basis: str = "user"
    property_tax_base: str = "market_value"
    maintenance_basis: str = "user"
    rental_expense_basis: str = "user"
    profile_id: str = ""
    version: int = 1
    verification_status: str = "unverified"
    transaction_scope: str = ""
    unsupported_cases: str = ""
    source_url: str = ""
    data_as_of: str = ""
    notes: str = ""

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Jurisdiction profile name is required")
        object.__setattr__(self, "name", self.name.strip())
        for field_name in (
            "acquisition_cost_percentage",
            "annual_property_tax_percentage",
            "annual_maintenance_percentage",
            "rental_expense_percentage",
            "other_transaction_cost_percentage",
        ):
            value = getattr(self, field_name)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or not 0 <= value <= 100):
                raise ValueError(
                    f"{field_name.replace('_', ' ')} must be between 0 and 100"
                )
            object.__setattr__(self, field_name, float(value))
        fixed = self.fixed_acquisition_costs
        if (isinstance(fixed, bool) or not isinstance(fixed, (int, float))
                or not math.isfinite(fixed) or fixed < 0):
            raise ValueError("fixed acquisition costs must be a non-negative number")
        object.__setattr__(self, "fixed_acquisition_costs", float(fixed))
        for field_name in (
            "acquisition_cost_basis", "other_transaction_cost_basis",
            "fixed_acquisition_cost_basis", "property_tax_basis",
            "maintenance_basis", "rental_expense_basis",
        ):
            if getattr(self, field_name) not in VALUE_BASES:
                raise ValueError(f"{field_name.replace('_', ' ')} must be one of {VALUE_BASES}")
        if self.property_tax_base not in PROPERTY_TAX_BASES:
            raise ValueError(f"property tax base must be one of {PROPERTY_TAX_BASES}")
        if self.verification_status not in VERIFICATION_STATUSES:
            raise ValueError(
                f"verification status must be one of {VERIFICATION_STATUSES}"
            )
        if (isinstance(self.version, bool) or not isinstance(self.version, int)
                or self.version < 1):
            raise ValueError("profile version must be a positive integer")
        for field_name in _TEXT_FIELDS:
            value = getattr(self, field_name)
            if not isinstance(value, str):
                raise ValueError(f"{field_name.replace('_', ' ')} must be text")
            object.__setattr__(self, field_name, value.strip())
        if self.source_url and not self.source_url.startswith("https://"):
            raise ValueError("source URL must start with https://")


@dataclass(frozen=True)
class PropertyCostEstimate:
    """Dollar estimates produced from one profile and one property."""

    acquisition_costs: float
    annual_property_tax: float
    monthly_maintenance: float
    monthly_rental_expenses: float
    acquisition_breakdown: tuple = ()  # ((label, amount, basis), ...)

    @property
    def monthly_operating_costs(self):
        return self.monthly_maintenance + self.monthly_rental_expenses


GENERIC_PROFILE_NAME = "Generic / manual (no jurisdiction estimates)"
_CATALOG_AS_OF = "2026-10-31"
_COMMON_EXCLUSIONS = (
    "Not modelled: agent commissions, mortgage/lender fees, VAT regimes for "
    "new builds, exemptions or reliefs, and municipal surcharges."
)
_COMMON_NOTE = (
    "Entered from general knowledge in an environment without access to the "
    "primary source; confirm the rate on the official page before relying on "
    "it. Dated estimate, not legal or tax advice."
)
_MAINTENANCE = dict(annual_maintenance_percentage=1.0, maintenance_basis="indicative")


def default_property_jurisdiction_profiles():
    """Return the editable catalog offered by the UI.

    The first entry is an explicit all-zero manual profile used when a
    jurisdiction or transaction type is not covered.  Preset rates are
    statutory only for the single transfer-tax component named in each
    profile; everything else is an indicative allowance or left to the user.
    Annual property tax is municipal / assessed-value based in these places
    and is therefore left at zero for the user to enter.
    """
    return [
        PropertyJurisdictionProfile(
            GENERIC_PROFILE_NAME,
            profile_id="generic-manual",
            transaction_scope="Any; every value is a user assumption.",
            notes="Use when the jurisdiction or purchase type is not covered.",
        ),
        PropertyJurisdictionProfile(
            "Netherlands — resale, owner-occupied",
            2.0,
            acquisition_cost_basis="statutory",
            property_tax_base="assessed_value",
            profile_id="nl-resale-owner-occupied", version=1,
            transaction_scope=(
                "Existing owner-occupied home (overdrachtsbelasting, buyer)."
            ),
            unsupported_cases=(
                "Investor / non-owner-occupied purchases (higher rate), new "
                "builds under VAT, young-buyer exemption. " + _COMMON_EXCLUSIONS
            ),
            source_url="https://www.belastingdienst.nl/",
            data_as_of=_CATALOG_AS_OF,
            notes=(
                "Municipal property tax (OZB) is levied on the assessed WOZ "
                "value at municipal rates; enter it yourself. " + _COMMON_NOTE
            ),
            **_MAINTENANCE,
        ),
        PropertyJurisdictionProfile(
            "Austria — resale, non-family transfer",
            4.6,
            acquisition_cost_basis="statutory",
            property_tax_base="assessed_value",
            profile_id="at-resale", version=1,
            transaction_scope=(
                "Purchase for consideration: Grunderwerbsteuer 3.5% plus land "
                "register entry fee 1.1%."
            ),
            unsupported_cases=(
                "Family / gift / inheritance stepped-rate transfers. "
                + _COMMON_EXCLUSIONS
            ),
            source_url="https://www.bmf.gv.at/",
            data_as_of=_CATALOG_AS_OF,
            notes=(
                "Grundsteuer is based on the assessed unit value (Einheitswert), "
                "not market value; enter it yourself. " + _COMMON_NOTE
            ),
            **_MAINTENANCE,
        ),
        PropertyJurisdictionProfile(
            "Madrid (Spain) — resale",
            6.0,
            acquisition_cost_basis="statutory",
            property_tax_base="assessed_value",
            profile_id="es-madrid-resale", version=1,
            transaction_scope=(
                "Resale home bought from a private seller "
                "(Transmisiones Patrimoniales, general regional rate)."
            ),
            unsupported_cases=(
                "New builds (VAT plus stamp duty), reduced-rate regimes, "
                "other Spanish regions. " + _COMMON_EXCLUSIONS
            ),
            source_url="https://www.comunidad.madrid/",
            data_as_of=_CATALOG_AS_OF,
            notes=(
                "IBI is levied on the cadastral value at municipal rates; "
                "enter it yourself. " + _COMMON_NOTE
            ),
            **_MAINTENANCE,
        ),
        PropertyJurisdictionProfile(
            "Lazio (Italy) — resale, private seller",
            2.0,
            fixed_acquisition_costs=100.0,
            acquisition_cost_basis="indicative",
            fixed_acquisition_cost_basis="statutory",
            property_tax_base="assessed_value",
            profile_id="it-lazio-resale-primary", version=1,
            transaction_scope=(
                "Primary-residence ('prima casa') purchase from a private "
                "seller: registration tax plus fixed mortgage and cadastral "
                "taxes."
            ),
            unsupported_cases=(
                "Second homes (higher registration rate), purchases from "
                "developers (VAT regime). " + _COMMON_EXCLUSIONS
            ),
            source_url="https://www.agenziaentrate.gov.it/",
            data_as_of=_CATALOG_AS_OF,
            notes=(
                "Registration tax is normally computed on the cadastral value, "
                "so applying it to the price is an approximation. IMU is "
                "cadastral-based and generally not due on a primary "
                "residence. " + _COMMON_NOTE
            ),
            **_MAINTENANCE,
        ),
        PropertyJurisdictionProfile(
            "Bavaria (Germany) — resale",
            3.5,
            other_transaction_cost_percentage=2.0,
            acquisition_cost_basis="statutory",
            other_transaction_cost_basis="indicative",
            property_tax_base="assessed_value",
            profile_id="de-bavaria-resale", version=1,
            transaction_scope=(
                "Resale: Grunderwerbsteuer 3.5% plus an indicative allowance "
                "for notary and land-register fees."
            ),
            unsupported_cases=_COMMON_EXCLUSIONS,
            source_url="https://www.bayern.de/",
            data_as_of=_CATALOG_AS_OF,
            notes=(
                "Grundsteuer uses an assessed base and municipal multiplier; "
                "enter it yourself. " + _COMMON_NOTE
            ),
            **_MAINTENANCE,
        ),
    ]


def preset_update_notices(profiles):
    """Describe catalog presets that are newer than the profiles supplied.

    Scenario profiles are never rewritten; callers show these notices and let
    the user add the updated preset deliberately.
    """
    current = {
        item.profile_id: item for item in default_property_jurisdiction_profiles()
        if item.profile_id
    }
    return [
        f"'{profile.name}' uses catalog version {profile.version}; version "
        f"{current[profile.profile_id].version} is available. Your saved "
        "values were left unchanged."
        for profile in profiles
        if profile.profile_id in current
        and profile.version < current[profile.profile_id].version
    ]


def profiles_by_name(profiles):
    """Validate a collection and return its exact-name lookup."""
    if not isinstance(profiles, (list, tuple)):
        raise ValueError("Jurisdiction profiles must be a list")
    lookup = {}
    for profile in profiles:
        if not isinstance(profile, PropertyJurisdictionProfile):
            raise ValueError("Jurisdiction profiles must use PropertyJurisdictionProfile")
        if profile.name in lookup:
            raise ValueError("Jurisdiction profile names must be unique")
        lookup[profile.name] = profile
    return lookup


def estimate_property_costs(
        profile, property_value, gross_monthly_rent=0.0,
        ownership_percentage=100.0):
    """Convert a profile to user-attributable cost estimates.

    Value-based recurring costs are scaled by ownership.  Gross rent is already
    expected to be the user's attributable monthly rent, matching the simulator's
    linked-income contract, so it is not scaled a second time.
    """
    if not isinstance(profile, PropertyJurisdictionProfile):
        raise ValueError("A valid jurisdiction profile is required")
    values = (property_value, gross_monthly_rent, ownership_percentage)
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           or not math.isfinite(value) for value in values):
        raise ValueError("Property estimate inputs must be finite numbers")
    if property_value < 0 or gross_monthly_rent < 0:
        raise ValueError("Property value and rent cannot be negative")
    if not 0 < ownership_percentage <= 100:
        raise ValueError("Ownership percentage must be above 0 and at most 100")

    owned_value = property_value * ownership_percentage / 100
    breakdown = tuple(
        (label, amount, basis) for label, amount, basis in (
            ("Transfer / registration taxes",
             property_value * profile.acquisition_cost_percentage / 100,
             profile.acquisition_cost_basis),
            ("Other transaction costs",
             property_value * profile.other_transaction_cost_percentage / 100,
             profile.other_transaction_cost_basis),
            ("Fixed fees", profile.fixed_acquisition_costs,
             profile.fixed_acquisition_cost_basis),
        ) if amount
    )
    return PropertyCostEstimate(
        acquisition_costs=sum(amount for _, amount, _ in breakdown),
        annual_property_tax=(
            owned_value * profile.annual_property_tax_percentage / 100
        ),
        monthly_maintenance=(
            owned_value * profile.annual_maintenance_percentage / 100 / 12
        ),
        monthly_rental_expenses=(
            gross_monthly_rent * profile.rental_expense_percentage / 100
        ),
        acquisition_breakdown=breakdown,
    )
