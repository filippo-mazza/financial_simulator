"""Validated financial assumptions shared by the model and UI."""

from dataclasses import dataclass, field
import math


INVESTMENT_TYPES = ("stock", "real_estate", "bonds", "safe")
INCOME_TYPES = (
    "salary", "rent", "dividend", "royalty", "freelance", "pension", "other",
)
DEFAULT_RETURN_MEANS = {
    "stock": 8.0, "real_estate": 4.0, "bonds": 2.5, "safe": 2.0,
}
DEFAULT_RETURN_STD_DEVS = {
    "stock": 20.0, "real_estate": 8.0, "bonds": 5.0, "safe": 1.0,
}
DEFAULT_STOCK_DIVIDEND_RATE = 0.0237
DEFAULT_CONTRIBUTION_FEE = 50.0
DEFAULT_INCOME_TAX_RATES = {
    "salary": 30.0, "rent": 15.0, "dividend": 20.0, "royalty": 15.0,
    "freelance": 30.0, "pension": 10.0, "other": 15.0,
}


def _return_means():
    return dict(DEFAULT_RETURN_MEANS)


def _return_std_devs():
    return dict(DEFAULT_RETURN_STD_DEVS)


def _income_tax_rates():
    return dict(DEFAULT_INCOME_TAX_RATES)


@dataclass
class FinancialAssumptions:
    """Scenario-level assumptions; rates are annual unless documented otherwise."""

    investment_return_means: dict[str, float] = field(default_factory=_return_means)
    investment_return_std_devs: dict[str, float] = field(default_factory=_return_std_devs)
    stock_dividend_rate: float = DEFAULT_STOCK_DIVIDEND_RATE
    contribution_fee: float = DEFAULT_CONTRIBUTION_FEE
    income_tax_rates: dict[str, float] = field(default_factory=_income_tax_rates)

    def __post_init__(self):
        self.investment_return_means = self._validated_mapping(
            "investment return means", self.investment_return_means,
            INVESTMENT_TYPES, -100, 100,
        )
        self.investment_return_std_devs = self._validated_mapping(
            "investment return standard deviations", self.investment_return_std_devs,
            INVESTMENT_TYPES, 0, 100,
        )
        self.income_tax_rates = self._validated_mapping(
            "income tax rates", self.income_tax_rates, INCOME_TYPES, 0, 100,
        )
        self.stock_dividend_rate = self._validated_number(
            "stock dividend rate", self.stock_dividend_rate, 0, 1,
        )
        self.contribution_fee = self._validated_number(
            "contribution fee", self.contribution_fee, 0, None,
        )

    @staticmethod
    def _validated_number(name, value, minimum, maximum):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be a number")
        value = float(value)
        if not math.isfinite(value) or value < minimum or (
                maximum is not None and value > maximum):
            limit = (
                f" between {minimum} and {maximum}"
                if maximum is not None else f" at least {minimum}"
            )
            raise ValueError(f"{name} must be{limit}")
        return value

    @classmethod
    def _validated_mapping(cls, name, values, expected_keys, minimum, maximum):
        if not isinstance(values, dict) or set(values) != set(expected_keys):
            raise ValueError(f"{name} must define exactly: {', '.join(expected_keys)}")
        return {
            key: cls._validated_number(f"{name}.{key}", values[key], minimum, maximum)
            for key in expected_keys
        }
