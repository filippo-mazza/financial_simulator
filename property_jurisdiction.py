"""Editable, jurisdiction-specific estimates for property costs.

Profiles are deliberately data-only.  The simulation engine continues to work
with dollar-denominated transaction and operating costs; this module converts
transparent percentage assumptions into those amounts before a run starts.
"""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class PropertyJurisdictionProfile:
    """Illustrative property-cost assumptions for one named jurisdiction."""

    name: str
    acquisition_cost_percentage: float = 0.0
    annual_property_tax_percentage: float = 0.0
    annual_maintenance_percentage: float = 0.0
    rental_expense_percentage: float = 0.0

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Jurisdiction profile name is required")
        object.__setattr__(self, "name", self.name.strip())
        for field_name in (
            "acquisition_cost_percentage",
            "annual_property_tax_percentage",
            "annual_maintenance_percentage",
            "rental_expense_percentage",
        ):
            value = getattr(self, field_name)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or not 0 <= value <= 100):
                raise ValueError(
                    f"{field_name.replace('_', ' ')} must be between 0 and 100"
                )
            object.__setattr__(self, field_name, float(value))


@dataclass(frozen=True)
class PropertyCostEstimate:
    """Dollar estimates produced from one profile and one property."""

    acquisition_costs: float
    annual_property_tax: float
    monthly_maintenance: float
    monthly_rental_expenses: float

    @property
    def monthly_operating_costs(self):
        return self.monthly_maintenance + self.monthly_rental_expenses


def default_property_jurisdiction_profiles():
    """Return editable, explicitly fictional examples for the UI."""
    return [
        PropertyJurisdictionProfile(
            "Fictional Metro", 4.0, 1.0, 1.0, 8.0,
        ),
        PropertyJurisdictionProfile(
            "Fictional Coast", 7.0, 0.6, 1.5, 12.0,
        ),
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
    return PropertyCostEstimate(
        acquisition_costs=(
            property_value * profile.acquisition_cost_percentage / 100
        ),
        annual_property_tax=(
            owned_value * profile.annual_property_tax_percentage / 100
        ),
        monthly_maintenance=(
            owned_value * profile.annual_maintenance_percentage / 100 / 12
        ),
        monthly_rental_expenses=(
            gross_monthly_rent * profile.rental_expense_percentage / 100
        ),
    )
