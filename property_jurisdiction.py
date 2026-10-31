"""Editable, jurisdiction-specific estimates for property costs.

Profiles are deliberately data-only.  The simulation engine continues to work
with dollar-denominated transaction and operating costs; this module converts
transparent percentage assumptions into those amounts before a run starts.
"""

from dataclasses import dataclass
import math


VALUE_BASES = ("statutory", "indicative", "user")
PROPERTY_TAX_BASES = ("market_value", "assessed_value")
VERIFICATION_STATUSES = ("unverified", "partially_verified", "verified")
COMPONENT_METHODS = ("rate", "brackets", "fixed")
COMPONENT_BASES = (
    "price", "higher_of_price_and_assessed", "cadastral_value", "mortgage_amount",
)
PURCHASE_TYPES = ("", "resale", "new_build")
OCCUPANCY_TYPES = ("", "primary_home", "secondary_or_investment", "any")
_TEXT_FIELDS = (
    "profile_id", "transaction_scope", "unsupported_cases", "source_url",
    "data_as_of", "notes", "purchase_type", "occupancy",
)


def _finite_number(value):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value))


def _normalize_bands(bands):
    """Validate ``(upper_limit | None, rate_pct, flat)`` bands.

    A marginal band charges ``rate`` only on the slice of the base inside the
    band.  A ``flat`` band charges ``rate`` on the *whole* base once the base
    falls inside it.  Upper limits are inclusive and must increase; the last
    band is open-ended (``None``).
    """
    if not isinstance(bands, (list, tuple)):
        raise ValueError("brackets must be a list")
    result = []
    previous = 0.0
    for band in bands:
        if not isinstance(band, (list, tuple)) or len(band) not in (2, 3):
            raise ValueError("each bracket needs (upper_limit, rate[, flat])")
        upper, rate = band[0], band[1]
        flat = bool(band[2]) if len(band) == 3 else False
        if upper is not None and (not _finite_number(upper) or upper <= previous):
            raise ValueError("bracket upper limits must increase")
        if not _finite_number(rate) or not 0 <= rate <= 100:
            raise ValueError("bracket rates must be between 0 and 100")
        result.append((None if upper is None else float(upper), float(rate), flat))
        if upper is None:
            break
        previous = upper
    if not result or result[-1][0] is not None or len(result) != len(bands):
        raise ValueError("brackets must end with one open-ended (None) band")
    return tuple(result)


@dataclass(frozen=True)
class AcquisitionComponent:
    """One separately labelled, separately overridable acquisition charge.

    ``method`` is ``rate`` (``rate_percentage`` of the base), ``brackets``
    (see ``_normalize_bands``) or ``fixed`` (``amount``).  ``minimum`` floors
    the computed charge.  ``condition="mortgage"`` applies it only when the
    purchase is financed.  ``override_percentage`` lets the user replace the
    whole method with a flat percentage of the base; the component is then
    reported as a user assumption.  ``verified`` is per component: it means
    the rate/scope was checked against ``source_url``, not that the profile
    is complete.
    """

    label: str
    basis: str = "user"
    method: str = "rate"
    rate_percentage: float = 0.0
    amount: float = 0.0
    brackets: tuple = ()
    minimum: float = 0.0
    base: str = "price"
    base_multiplier: float = 1.0
    condition: str = ""
    override_percentage: float | None = None
    verified: bool = False
    source_url: str = ""
    note: str = ""

    def __post_init__(self):
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError("Acquisition component label is required")
        object.__setattr__(self, "label", self.label.strip())
        if self.basis not in VALUE_BASES:
            raise ValueError(f"component basis must be one of {VALUE_BASES}")
        if self.method not in COMPONENT_METHODS:
            raise ValueError(f"component method must be one of {COMPONENT_METHODS}")
        if self.base not in COMPONENT_BASES:
            raise ValueError(f"component base must be one of {COMPONENT_BASES}")
        if self.condition not in ("", "mortgage"):
            raise ValueError("component condition must be empty or 'mortgage'")
        for name in ("rate_percentage",):
            value = getattr(self, name)
            if not _finite_number(value) or not 0 <= value <= 100:
                raise ValueError(f"{name.replace('_', ' ')} must be between 0 and 100")
        for name in ("amount", "minimum"):
            value = getattr(self, name)
            if not _finite_number(value) or value < 0:
                raise ValueError(f"component {name} must be non-negative")
        if (not _finite_number(self.base_multiplier)
                or self.base_multiplier <= 0):
            raise ValueError("base multiplier must be positive")
        if self.override_percentage is not None and (
                not _finite_number(self.override_percentage)
                or not 0 <= self.override_percentage <= 100):
            raise ValueError("override percentage must be between 0 and 100")
        object.__setattr__(self, "brackets", _normalize_bands(self.brackets)
                           if self.brackets or self.method == "brackets" else ())
        if self.method == "brackets" and not self.brackets:
            raise ValueError("bracket components need brackets")
        for name in ("rate_percentage", "amount", "minimum", "base_multiplier"):
            object.__setattr__(self, name, float(getattr(self, name)))
        if self.override_percentage is not None:
            object.__setattr__(
                self, "override_percentage", float(self.override_percentage))
        if not isinstance(self.verified, bool):
            raise ValueError("component verified flag must be true or false")
        if not isinstance(self.note, str) or not isinstance(self.source_url, str):
            raise ValueError("component note and source URL must be text")
        if self.source_url and not self.source_url.startswith("https://"):
            raise ValueError("component source URL must start with https://")

    @property
    def effective_basis(self):
        return "user" if self.override_percentage is not None else self.basis

    @property
    def effective_verified(self):
        return self.verified and self.override_percentage is None

    def charge(self, base_amount):
        """Return the charge for an already-resolved base amount."""
        if self.method == "fixed":
            return self.amount
        if self.override_percentage is not None:
            charge = base_amount * self.override_percentage / 100
        elif self.method == "rate":
            charge = base_amount * self.rate_percentage / 100
        else:
            charge = _bracket_charge(self.brackets, base_amount)
        return max(charge, self.minimum) if base_amount > 0 else 0.0


def _bracket_charge(bands, base_amount):
    charge = 0.0
    lower = 0.0
    for upper, rate, flat in bands:
        if upper is None or base_amount <= upper:
            return base_amount * rate / 100 if flat else (
                charge + (base_amount - lower) * rate / 100
            )
        charge += (upper - lower) * rate / 100
        lower = upper
    return charge


def _reject_component(item):
    raise ValueError("acquisition components must be objects")


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
    purchase_type: str = ""
    occupancy: str = ""
    additional_sources: tuple = ()
    acquisition_components: tuple = ()

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
        if self.purchase_type not in PURCHASE_TYPES:
            raise ValueError(f"purchase type must be one of {PURCHASE_TYPES}")
        if self.occupancy not in OCCUPANCY_TYPES:
            raise ValueError(f"occupancy must be one of {OCCUPANCY_TYPES}")
        sources = self.additional_sources
        if (not isinstance(sources, (list, tuple)) or not all(
                isinstance(url, str) and url.startswith("https://")
                for url in sources)):
            raise ValueError("additional sources must be https:// URLs")
        object.__setattr__(self, "additional_sources", tuple(sources))
        components = self.acquisition_components
        if not isinstance(components, (list, tuple)):
            raise ValueError("acquisition components must be a list")
        object.__setattr__(self, "acquisition_components", tuple(
            item if isinstance(item, AcquisitionComponent)
            else AcquisitionComponent(**item) if isinstance(item, dict)
            else _reject_component(item)
            for item in components
        ))


@dataclass(frozen=True)
class PropertyCostEstimate:
    """Dollar estimates produced from one profile and one property."""

    acquisition_costs: float
    annual_property_tax: float
    monthly_maintenance: float
    monthly_rental_expenses: float
    acquisition_breakdown: tuple = ()  # ((label, amount, basis), ...)
    warnings: tuple = ()

    @property
    def monthly_operating_costs(self):
        return self.monthly_maintenance + self.monthly_rental_expenses


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
        ownership_percentage=100.0, *, mortgage_amount=0.0,
        cadastral_income=None, assessed_value=None):
    """Convert a profile to user-attributable cost estimates.

    Value-based recurring costs are scaled by ownership.  Gross rent is already
    expected to be the user's attributable monthly rent, matching the simulator's
    linked-income contract, so it is not scaled a second time.

    ``mortgage_amount``, ``cadastral_income`` (annual, e.g. Italian rendita) and
    ``assessed_value`` feed components whose base is not the price.  When one
    is missing the component falls back to the price as an explicit proxy and
    the estimate carries a warning; nothing is silently zeroed.
    """
    if not isinstance(profile, PropertyJurisdictionProfile):
        raise ValueError("A valid jurisdiction profile is required")
    values = (property_value, gross_monthly_rent, ownership_percentage,
              mortgage_amount)
    if any(not _finite_number(value) for value in values):
        raise ValueError("Property estimate inputs must be finite numbers")
    for optional in (cadastral_income, assessed_value):
        if optional is not None and (not _finite_number(optional) or optional < 0):
            raise ValueError("Cadastral income and assessed value must be non-negative")
    if property_value < 0 or gross_monthly_rent < 0 or mortgage_amount < 0:
        raise ValueError("Property value, rent and mortgage cannot be negative")
    if not 0 < ownership_percentage <= 100:
        raise ValueError("Ownership percentage must be above 0 and at most 100")

    owned_value = property_value * ownership_percentage / 100
    warnings = []
    entries = []
    for percentage, label, basis in (
        (profile.acquisition_cost_percentage,
         "Transfer / registration taxes", profile.acquisition_cost_basis),
        (profile.other_transaction_cost_percentage,
         "Other transaction costs", profile.other_transaction_cost_basis),
    ):
        entries.append((label, property_value * percentage / 100, basis))
    entries.append((
        "Fixed fees", profile.fixed_acquisition_costs,
        profile.fixed_acquisition_cost_basis,
    ))
    for component in profile.acquisition_components:
        if component.condition == "mortgage" and mortgage_amount <= 0:
            continue
        base = property_value
        basis = component.effective_basis
        if component.base == "mortgage_amount":
            base = mortgage_amount
        elif component.base == "higher_of_price_and_assessed":
            if assessed_value is None:
                warnings.append(
                    f"{component.label}: assessed value not entered; the "
                    "price is used and may understate the tax base."
                )
                basis = "indicative" if basis != "user" else basis
            else:
                base = max(property_value, assessed_value)
        elif component.base == "cadastral_value":
            if cadastral_income is None:
                warnings.append(
                    f"{component.label}: cadastral income not entered; the "
                    "sale price is used as a proxy, so this is only an "
                    "indicative estimate."
                )
                basis = "indicative" if basis != "user" else basis
            else:
                base = cadastral_income * component.base_multiplier
        entries.append((component.label, component.charge(base), basis))
    breakdown = tuple(
        (label, amount, basis) for label, amount, basis in entries if amount
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
        warnings=tuple(warnings),
    )


def describe_profile_assumptions(profile):
    """Return Markdown describing a profile's assumptions, sources and limits."""
    labels = {"statutory": "statutory", "indicative": "indicative allowance",
              "user": "user assumption"}
    lines = [f"### {profile.name}"]
    if profile.profile_id:
        lines.append(
            f"`{profile.profile_id}` v{profile.version} · {profile.verification_status}"
            f" · data as of {profile.data_as_of or 'n/a'}"
        )
    if profile.transaction_scope:
        lines.append(f"**Covers:** {profile.transaction_scope}")
    for component in profile.acquisition_components:
        detail = {
            "rate": f"{component.rate_percentage:g}% of base",
            "fixed": f"{component.amount:,.0f} fixed",
            "brackets": "banded: " + "; ".join(
                f"{'up to ' + format(upper, ',.0f') if upper is not None else 'above'}"
                f" {rate:g}%{' on whole base' if flat else ''}"
                for upper, rate, flat in component.brackets
            ),
        }[component.method]
        extras = [f"base: {component.base.replace('_', ' ')}"]
        if component.minimum:
            extras.append(f"minimum {component.minimum:,.0f}")
        if component.condition:
            extras.append("only when financed")
        lines.append(
            f"- {component.label}: {detail} ({', '.join(extras)}) — "
            f"{labels[component.effective_basis]}, "
            f"{'source-checked' if component.effective_verified else 'not source-checked'}"
            + (f" — {component.note}" if component.note else "")
            + (f" — [source]({component.source_url})" if component.source_url else "")
        )
    if profile.acquisition_cost_percentage or profile.other_transaction_cost_percentage \
            or profile.fixed_acquisition_costs:
        lines.append("- Flat acquisition inputs are user-defined or legacy values.")
    if profile.unsupported_cases:
        lines.append(f"**Not covered:** {profile.unsupported_cases}")
    if profile.notes:
        lines.append(profile.notes)
    for url in (profile.source_url, *profile.additional_sources):
        if url:
            lines.append(f"Source: {url}")
    return "\n\n".join(lines)
