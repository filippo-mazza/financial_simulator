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


def _validation_message(field, value, constraint, action, prefix=""):
    field = f"{prefix}.{field}" if prefix else field
    return (
        f"{field}: received {value!r}; required {constraint}. "
        f"{action}"
    )


def _nested_prefix(prefix, name):
    return f"{prefix}.{name}" if prefix else name


def _raise_validation_errors(errors):
    if errors:
        raise PropertyPurchaseError(
            "Please correct the following purchase settings:\n- "
            + "\n- ".join(errors)
        )


def _is_finite_number(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


VARIABLE_INCOME_TYPES = frozenset({"freelance", "royalty", "other"})
COMPLETION_FAILURE_POLICIES = frozenset({"retry", "cancel"})


@dataclass(frozen=True)
class PurchaseCompletionDelayPolicy:
    """Waiting-time assumptions between eligibility and actual closing.

    Equal bounds model a fixed delay. Unequal bounds are sampled inclusively
    once, when the trajectory first becomes eligible. ``on_ineligible`` controls
    what happens if the closing checks no longer pass in the scheduled month.
    """

    minimum_months: int = 0
    maximum_months: int = 0
    on_ineligible: str = "retry"

    def validate(self, field_prefix=""):
        errors = []
        for field, value in (
            ("minimum_months", self.minimum_months),
            ("maximum_months", self.maximum_months),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                errors.append(_validation_message(
                    field, value, f"a nonnegative integer number of months",
                    "Enter 0 or a positive whole number of months.", field_prefix,
                ))
        if (isinstance(self.minimum_months, int)
                and not isinstance(self.minimum_months, bool)
                and isinstance(self.maximum_months, int)
                and not isinstance(self.maximum_months, bool)
                and self.minimum_months > self.maximum_months):
            errors.append(_validation_message(
                "minimum_months", self.minimum_months,
                f"minimum completion delay cannot exceed maximum_months "
                f"({self.maximum_months})",
                "Increase the maximum delay or lower the minimum delay.",
                field_prefix,
            ))
        if (not isinstance(self.on_ineligible, str)
                or self.on_ineligible not in COMPLETION_FAILURE_POLICIES):
            errors.append(_validation_message(
                "on_ineligible", self.on_ineligible, "'retry' or 'cancel'",
                "Choose whether to retry closing or cancel the purchase.",
                field_prefix,
            ))
        _raise_validation_errors(errors)
        return self

    def sample_months(self, random_source):
        """Sample without consuming randomness for fixed, including zero, delays."""
        self.validate()
        if self.minimum_months == self.maximum_months:
            return self.minimum_months
        return random_source.randint(self.minimum_months, self.maximum_months)


@dataclass(frozen=True)
class MortgageAffordabilityPolicy:
    """Editable planning assumptions for an optional mortgage eligibility gate.

    Variable-income inclusion is deliberately explicit rather than a lender
    default. A value of 0 excludes it; 100 includes the configured regular rate
    in full. Annual bonuses are always excluded because they are unguaranteed.
    The policy is a planning heuristic, not a prediction or approval guarantee.
    """

    max_debt_service_ratio_pct: float = 33.0
    qualifying_income_names: tuple[str, ...] = ()
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

    def validate(self, field_prefix=""):
        errors = []
        percentages = (
            ("max_debt_service_ratio_pct", self.max_debt_service_ratio_pct,
             "maximum debt-service ratio"),
            ("variable_income_inclusion_pct", self.variable_income_inclusion_pct,
             "variable income inclusion"),
        )
        for field, value, label in percentages:
            if not _is_finite_number(value) or not 0 <= value <= 100:
                errors.append(_validation_message(
                    field, value, "a percentage between 0 and 100",
                    "Enter a percentage from 0 to 100.", field_prefix,
                ))
        amounts = (
            ("base_monthly_living_buffer", self.base_monthly_living_buffer),
            ("monthly_buffer_per_adult", self.monthly_buffer_per_adult),
            ("monthly_buffer_per_dependent", self.monthly_buffer_per_dependent),
        )
        for field, value in amounts:
            if not _is_finite_number(value) or value < 0:
                errors.append(_validation_message(
                    field, value, "a finite amount of at least 0",
                    "Enter 0 or a positive monthly amount.", field_prefix,
                ))
        for field, value in (
            ("adult_count", self.adult_count),
            ("dependent_count", self.dependent_count),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                errors.append(_validation_message(
                    field, value, "a nonnegative integer",
                    "Enter 0 or a positive whole-number household count.",
                    field_prefix,
                ))
        if (not isinstance(self.qualifying_income_names, (tuple, list))
                or not all(isinstance(name, str) and name for name in self.qualifying_income_names)):
            errors.append(_validation_message(
                "qualifying_income_names", self.qualifying_income_names,
                "a list of non-empty income-source names",
                "Select one or more configured income sources.", field_prefix,
            ))
        _raise_validation_errors(errors)
        return self


@dataclass(frozen=True)
class AutomaticPurchasePolicy:
    """One purchase that closes when a trajectory has enough Main cash.

    ``minimum_cash_reserve`` is the cash that must remain immediately after
    closing, in addition to the purchase's down payment and fees. It is a
    point-in-time affordability test, not a protected subaccount; later modeled
    cash flows may use it. The embedded purchase is a template. First eligibility
    either closes immediately or schedules completion through ``completion_delay``.
    """

    purchase: "PropertyPurchase"
    minimum_available_cash: float
    minimum_cash_reserve: float
    mortgage_affordability: MortgageAffordabilityPolicy | None = None
    completion_delay: PurchaseCompletionDelayPolicy | None = None

    @property
    def minimum_required_cash(self):
        """Cash needed to satisfy both the threshold and closing reserve."""
        closing_cash = (
            self.purchase.down_payment
            + self.purchase.transaction_fees
            + self.minimum_cash_reserve
        )
        return max(self.minimum_available_cash, closing_cash)

    def validate(self, field_prefix=""):
        errors = []
        for field, value in (
            ("minimum_available_cash", self.minimum_available_cash),
            ("minimum_cash_reserve", self.minimum_cash_reserve),
        ):
            if not _is_finite_number(value) or value < 0:
                errors.append(_validation_message(
                    field, value, "a finite amount of at least 0",
                    "Enter 0 or a positive cash amount.", field_prefix,
                ))
        try:
            self.purchase.validate(
                None, field_prefix=_nested_prefix(field_prefix, "purchase"),
            )
        except PropertyPurchaseError as error:
            errors.extend(str(error).splitlines()[1:])
        if self.mortgage_affordability is not None:
            try:
                self.mortgage_affordability.validate(
                    field_prefix=_nested_prefix(
                        field_prefix, "mortgage_affordability",
                    ),
                )
            except PropertyPurchaseError as error:
                errors.extend(str(error).splitlines()[1:])
        if self.completion_delay is not None:
            try:
                self.completion_delay.validate(
                    field_prefix=_nested_prefix(field_prefix, "completion_delay"),
                )
            except PropertyPurchaseError as error:
                errors.extend(str(error).splitlines()[1:])
        _raise_validation_errors(errors)
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
        # Annual bonuses are deliberately excluded. They are stochastic and
        # unguaranteed in the engine, so counting them would make this planning
        # gate less conservative and could overstate mortgage affordability.
        qualifying_income += net_regular
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
    issues = []
    if not selected_names:
        issues.append(
            "No qualifying income sources were selected. Select at least one "
            "configured income source in the mortgage affordability settings."
        )
    elif qualifying_income <= 0:
        issues.append(
            f"Selected sources provide ${qualifying_income:,.0f}/month in "
            "qualifying income; choose an income source with a positive "
            "after-tax monthly amount or adjust variable-income inclusion."
        )
    if selected_names and not ratio_passes and debt_service_ratio_pct is not None:
        issues.append(
            f"Total monthly debt service ${total_debt_payment:,.0f} is "
            f"{debt_service_ratio_pct:.1f}% of qualifying income, above the "
            f"configured {policy.max_debt_service_ratio_pct:.1f}% limit. "
            "Choose additional qualifying income, reduce debt, or raise the "
            "configured ratio limit."
        )
    if selected_names and not buffer_passes:
        issues.append(
            f"Qualifying income after debt service ${income_after_debt:,.0f} is "
            f"below the configured household buffer "
            f"${policy.monthly_living_buffer:,.0f}. Choose additional qualifying "
            "income, reduce debt, or lower the household buffer."
        )
    if issues:
        reason = " ".join(issues)
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
        "annual_bonus_treatment": "excluded",
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
    annual_maintenance_percentage: float = 0.0

    @property
    def down_payment(self):
        return self.purchase_price * self.down_payment_percentage / 100

    @property
    def mortgage_principal(self):
        return self.purchase_price - self.down_payment

    def validate(self, bank_account=None, investments=(), loans=(), field_prefix=""):
        errors = []
        numeric = (
            ("purchase_price", self.purchase_price, "a finite amount greater than 0",
             "Enter the property's positive purchase price."),
            ("down_payment_percentage", self.down_payment_percentage,
             "a percentage between 0 and 100",
             "Enter a down-payment percentage from 0 to 100."),
            ("transaction_fees", self.transaction_fees, "a finite amount of at least 0",
             "Enter 0 or a positive transaction-fee amount."),
            ("mortgage_interest_rate", self.mortgage_interest_rate,
             "a finite annual percentage of at least 0",
             "Enter 0 or a positive mortgage rate."),
            ("monthly_expenses", self.monthly_expenses,
             "a finite monthly amount of at least 0",
             "Enter 0 or a positive monthly amount."),
            ("yearly_tax", self.yearly_tax, "a finite annual amount of at least 0",
             "Enter 0 or a positive annual tax amount."),
            ("annual_maintenance_percentage", self.annual_maintenance_percentage,
             "a percentage between 0 and 100",
             "Enter a maintenance percentage from 0 to 100."),
        )
        for field, value, constraint, action in numeric:
            valid = _is_finite_number(value)
            if valid:
                if field == "purchase_price":
                    valid = value > 0
                elif field in ("down_payment_percentage",
                               "annual_maintenance_percentage"):
                    valid = 0 <= value <= 100
                else:
                    valid = value >= 0
            if not valid:
                errors.append(_validation_message(
                    field, value, constraint, action, field_prefix,
                ))
        if (not isinstance(self.mortgage_duration_years, int)
                or isinstance(self.mortgage_duration_years, bool)
                or self.mortgage_duration_years <= 0):
            errors.append(_validation_message(
                "mortgage_duration_years", self.mortgage_duration_years,
                "a positive integer number of years",
                "Enter a whole number of mortgage years greater than 0.",
                field_prefix,
            ))
        if (not isinstance(self.purchase_month, int)
                or isinstance(self.purchase_month, bool)
                or self.purchase_month < 0):
            errors.append(_validation_message(
                "purchase_month", self.purchase_month,
                "a nonnegative integer month index",
                "Enter 0 or a positive whole-number month index.",
                field_prefix,
            ))
        for field, value in (
            ("property_name", self.property_name),
            ("mortgage_name", self.mortgage_name),
        ):
            if not isinstance(value, str) or not value.strip():
                errors.append(_validation_message(
                    field, value, "a non-empty name",
                    "Enter a name for this item.", field_prefix,
                ))
        if any(getattr(item, "name", None) == self.property_name for item in investments):
            errors.append(_validation_message(
                "property_name", self.property_name,
                "a name not already used by an investment",
                "Choose a unique property name.", field_prefix,
            ))
        if any(getattr(item, "name", None) == self.mortgage_name for item in loans):
            errors.append(_validation_message(
                "mortgage_name", self.mortgage_name,
                "a name not already used by a loan",
                "Choose a unique mortgage name.", field_prefix,
            ))
        _raise_validation_errors(errors)
        required = self.down_payment + self.transaction_fees
        if bank_account is not None and bank_account.subaccounts["Main"] < required:
            raise InsufficientFundsException(
                f"Insufficient funds for property purchase: Main cash "
                f"${bank_account.subaccounts['Main']:,.2f} is below the "
                f"${required:,.2f} required for the down payment and fees. "
                "Add cash or lower the down payment/transaction fees."
            )
        return self

    @property
    def annual_maintenance_amount(self):
        """Annual upkeep allowance based on the original purchase price."""
        return self.purchase_price * self.annual_maintenance_percentage / 100

    @property
    def effective_monthly_expenses(self):
        """Fixed monthly costs plus the initial-price maintenance allowance."""
        return self.monthly_expenses + self.annual_maintenance_amount / 12


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
    property_asset.ownership_percentage = 100.0
    property_asset.linked_loan_name = purchase.mortgage_name if mortgage else None
    property_asset.rental_income_name = None
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
        "monthly_expenses": purchase.effective_monthly_expenses,
        "yearly_tax": purchase.yearly_tax,
        "annual_maintenance_amount": purchase.annual_maintenance_amount,
    }
