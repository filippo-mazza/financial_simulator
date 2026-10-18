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
        return self

    def purchase_for_month(self, month):
        return replace(self.purchase, purchase_month=month)


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
