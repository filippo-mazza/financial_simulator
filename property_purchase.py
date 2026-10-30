"""Atomic, opt-in property purchase transactions.

This deliberately models only a 100%-owned property purchased at its nominal
price.  Fees are paid from cash and are not capitalised into the asset.
"""

from dataclasses import dataclass, replace
import math

from bank_account import InsufficientFundsException
from investment import Investment
from loan import Loan


class PropertyPurchaseError(ValueError):
    """Raised when a purchase cannot be validated or applied."""


VARIABLE_INCOME_TYPES = frozenset({"freelance", "royalty", "other"})


@dataclass(frozen=True)
class MortgageAffordabilityPolicy:
    """Editable planning assumptions for an optional mortgage eligibility gate.

    Income inclusion percentages are deliberately explicit rather than lender
    defaults.  A value of 0 excludes that income category; 100 includes the
    configured amount in full.  The policy is a planning heuristic and is not a
    prediction or guarantee of mortgage approval.
    """

    max_debt_service_ratio_pct: float = 33.0
    qualifying_income_names: tuple[str, ...] = ()
    bonus_income_inclusion_pct: float = 0.0
    variable_income_inclusion_pct: float = 0.0
    base_monthly_living_buffer: float = 0.0
    adult_count: int = 1
    monthly_buffer_per_adult: float = 0.0
    dependent_count: int = 0
    monthly_buffer_per_dependent: float = 0.0

    @property
    def monthly_living_buffer(self):
        return (
            self.base_monthly_living_buffer
            + self.adult_count * self.monthly_buffer_per_adult
            + self.dependent_count * self.monthly_buffer_per_dependent
        )

    def validate(self):
        percentages = (
            (self.max_debt_service_ratio_pct, "maximum debt-service ratio"),
            (self.bonus_income_inclusion_pct, "bonus income inclusion"),
            (self.variable_income_inclusion_pct, "variable income inclusion"),
        )
        for value, label in percentages:
            if not math.isfinite(value) or not 0 <= value <= 100:
                raise PropertyPurchaseError(f"{label} must be between 0 and 100")
        amounts = (
            (self.base_monthly_living_buffer, "base monthly living buffer"),
            (self.monthly_buffer_per_adult, "monthly buffer per adult"),
            (self.monthly_buffer_per_dependent, "monthly buffer per dependent"),
        )
        for value, label in amounts:
            if not math.isfinite(value) or value < 0:
                raise PropertyPurchaseError(f"{label} must be a finite nonnegative amount")
        for value, label in (
            (self.adult_count, "adult count"),
            (self.dependent_count, "dependent count"),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise PropertyPurchaseError(f"{label} must be a nonnegative integer")
        if (not isinstance(self.qualifying_income_names, (tuple, list))
                or not all(isinstance(name, str) and name for name in self.qualifying_income_names)):
            raise PropertyPurchaseError("qualifying income names must be a sequence of names")
        return self


@dataclass(frozen=True)
class AutomaticPurchasePolicy:
    """One purchase that closes when a trajectory has enough Main cash.

    ``minimum_cash_reserve`` is the cash that must remain immediately after
    closing, in addition to the purchase's down payment and fees. It is a
    point-in-time affordability test, not a protected subaccount; later modeled
    cash flows may use it. The embedded purchase is a template whose month is
    chosen when the first eligible month is reached.
    """

    purchase: "PropertyPurchase"
    minimum_available_cash: float
    minimum_cash_reserve: float
    mortgage_affordability: MortgageAffordabilityPolicy | None = None

    @property
    def minimum_required_cash(self):
        """Cash needed to satisfy both the threshold and closing reserve."""
        closing_cash = (
            self.purchase.down_payment
            + self.purchase.transaction_fees
            + self.minimum_cash_reserve
        )
        return max(self.minimum_available_cash, closing_cash)

    def validate(self):
        for value, label in (
            (self.minimum_available_cash, "minimum available cash"),
            (self.minimum_cash_reserve, "minimum cash reserve"),
        ):
            if not math.isfinite(value) or value < 0:
                raise PropertyPurchaseError(f"{label} must be a finite nonnegative amount")
        self.purchase.validate(None)
        if self.mortgage_affordability is not None:
            self.mortgage_affordability.validate()
        return self

    def purchase_for_month(self, month):
        return replace(self.purchase, purchase_month=month)


def _net_regular_income(source, monthly_rate):
    tax_rate = (
        source.specific_taxation
        if source.specific_taxation is not None
        else source.income_taxation[source.income_type]
    )
    return monthly_rate * (1 - tax_rate / 100)


def evaluate_mortgage_affordability(
        purchase, policy, income_sources, loans, monthly_income_rates=None):
    """Return a transparent point-in-time affordability assessment.

    Existing debt uses each active loan's current scheduled payment.  The new
    mortgage payment is calculated by the same ``Loan`` class used when the
    purchase closes, so it is included exactly once.
    """
    policy.validate()
    selected_names = set(policy.qualifying_income_names)
    qualifying_income = 0.0
    included_sources = []
    for index, source in enumerate(income_sources):
        if source.name not in selected_names:
            continue
        if isinstance(monthly_income_rates, dict):
            rate = monthly_income_rates.get(source.name, source.monthly_income)
        elif monthly_income_rates is not None:
            rate = monthly_income_rates[index]
        else:
            rate = source.monthly_income
        net_regular = _net_regular_income(source, rate)
        if source.income_type in VARIABLE_INCOME_TYPES:
            net_regular *= policy.variable_income_inclusion_pct / 100
        bonus_pct = getattr(source, "bonus_pct", 0) or 0
        bonus_monthly = (
            net_regular
            * bonus_pct / 100
            * policy.bonus_income_inclusion_pct / 100
        )
        qualifying_income += net_regular + bonus_monthly
        included_sources.append(source.name)

    existing_debt_payment = sum(
        loan.monthly_repayment for loan in loans if not loan.is_repaid
    )
    proposed_mortgage_payment = 0.0
    if purchase.mortgage_principal > 0:
        proposed_mortgage_payment = Loan(
            "affordability preview", purchase.mortgage_principal,
            purchase.mortgage_interest_rate, purchase.mortgage_duration_years,
            purchase.fixed_rate,
        ).monthly_repayment
    total_debt_payment = existing_debt_payment + proposed_mortgage_payment
    debt_service_ratio_pct = (
        total_debt_payment / qualifying_income * 100
        if qualifying_income > 0 else None
    )
    income_after_debt = qualifying_income - total_debt_payment
    ratio_passes = (
        debt_service_ratio_pct is not None
        and debt_service_ratio_pct <= policy.max_debt_service_ratio_pct
    )
    buffer_passes = income_after_debt >= policy.monthly_living_buffer
    eligible = bool(selected_names) and ratio_passes and buffer_passes
    if not selected_names:
        reason = "No qualifying income sources were selected."
    elif qualifying_income <= 0:
        reason = "Selected income sources provide no qualifying monthly income."
    elif not ratio_passes:
        reason = (
            f"Total monthly debt service ${total_debt_payment:,.0f} is "
            f"{debt_service_ratio_pct:.1f}% of qualifying income, above the "
            f"configured {policy.max_debt_service_ratio_pct:.1f}% limit."
        )
    elif not buffer_passes:
        reason = (
            f"Qualifying income after debt service ${income_after_debt:,.0f} is "
            f"below the configured household buffer "
            f"${policy.monthly_living_buffer:,.0f}."
        )
    else:
        reason = (
            f"Debt service is {debt_service_ratio_pct:.1f}% of qualifying income "
            f"and leaves ${income_after_debt:,.0f} after debt payments."
        )
    return {
        "eligible": eligible,
        "reason": reason,
        "qualifying_income_names": included_sources,
        "qualifying_monthly_income": qualifying_income,
        "existing_monthly_debt_payments": existing_debt_payment,
        "proposed_mortgage_payment": proposed_mortgage_payment,
        "total_monthly_debt_payments": total_debt_payment,
        "debt_service_ratio_pct": debt_service_ratio_pct,
        "max_debt_service_ratio_pct": policy.max_debt_service_ratio_pct,
        "monthly_living_buffer": policy.monthly_living_buffer,
        "income_after_debt": income_after_debt,
        "bonus_income_inclusion_pct": policy.bonus_income_inclusion_pct,
        "variable_income_inclusion_pct": policy.variable_income_inclusion_pct,
    }


@dataclass(frozen=True)
class PropertyPurchase:
    purchase_price: float
    down_payment_percentage: float
    transaction_fees: float = 0.0
    mortgage_interest_rate: float = 0.0
    mortgage_duration_years: int = 25
    purchase_month: int = 0
    property_name: str = "Purchased property"
    mortgage_name: str = "Property mortgage"
    fixed_rate: bool = False
    monthly_expenses: float = 0.0
    yearly_tax: float = 0.0

    @property
    def down_payment(self):
        return self.purchase_price * self.down_payment_percentage / 100

    @property
    def mortgage_principal(self):
        return self.purchase_price - self.down_payment

    def validate(self, bank_account=None, investments=(), loans=()):
        numeric = (
            self.purchase_price, self.down_payment_percentage,
            self.transaction_fees, self.mortgage_interest_rate,
            self.monthly_expenses, self.yearly_tax,
        )
        if not all(math.isfinite(value) for value in numeric):
            raise PropertyPurchaseError("Purchase amounts and rates must be finite")
        if self.purchase_price <= 0:
            raise PropertyPurchaseError("Purchase price must be positive")
        if not 0 <= self.down_payment_percentage <= 100:
            raise PropertyPurchaseError("Down payment percentage must be between 0 and 100")
        if self.transaction_fees < 0:
            raise PropertyPurchaseError("Transaction fees cannot be negative")
        if self.monthly_expenses < 0 or self.yearly_tax < 0:
            raise PropertyPurchaseError("Property operating costs cannot be negative")
        if self.mortgage_interest_rate < 0:
            raise PropertyPurchaseError("Mortgage interest rate cannot be negative")
        if self.mortgage_duration_years <= 0:
            raise PropertyPurchaseError("Mortgage duration must be positive")
        if not isinstance(self.purchase_month, int) or self.purchase_month < 0:
            raise PropertyPurchaseError("Purchase month must be a nonnegative integer")
        if not self.property_name or not self.mortgage_name:
            raise PropertyPurchaseError("Property and mortgage names are required")
        if any(getattr(item, "name", None) == self.property_name for item in investments):
            raise PropertyPurchaseError(f"Investment named '{self.property_name}' already exists")
        if any(getattr(item, "name", None) == self.mortgage_name for item in loans):
            raise PropertyPurchaseError(f"Loan named '{self.mortgage_name}' already exists")
        required = self.down_payment + self.transaction_fees
        if bank_account is not None and bank_account.subaccounts["Main"] < required:
            raise InsufficientFundsException(
                f"Insufficient funds for property purchase: ${required:,.2f} required"
            )
        return self


def execute_property_purchase(
        purchase, bank_account, investments, loans,
        annual_return_mean=None, annual_return_std_dev=None):
    """Validate and apply one purchase, returning its accounting event.

    Objects are constructed before cash is withdrawn, so validation and object
    construction cannot leave a partially applied transaction.
    """
    purchase.validate(bank_account, investments, loans)
    property_asset = Investment(
        purchase.property_name, "real_estate", purchase.purchase_price, 0,
        annual_return_mean=annual_return_mean,
        annual_return_std_dev=annual_return_std_dev,
    )
    mortgage = None
    if purchase.mortgage_principal > 0:
        mortgage = Loan(
            purchase.mortgage_name, purchase.mortgage_principal,
            purchase.mortgage_interest_rate, purchase.mortgage_duration_years,
            purchase.fixed_rate,
        )
    bank_account.withdraw(purchase.down_payment + purchase.transaction_fees)
    investments.append(property_asset)
    if mortgage is not None:
        loans.append(mortgage)
    return {
        "month": purchase.purchase_month,
        "property_name": purchase.property_name,
        "mortgage_name": purchase.mortgage_name if mortgage else None,
        "purchase_price": purchase.purchase_price,
        "down_payment": purchase.down_payment,
        "transaction_fees": purchase.transaction_fees,
        "mortgage_principal": purchase.mortgage_principal,
        "monthly_expenses": purchase.monthly_expenses,
        "yearly_tax": purchase.yearly_tax,
    }
