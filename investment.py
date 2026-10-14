import random
import matplotlib.pyplot as plt
from financial_assumptions import (
    DEFAULT_CONTRIBUTION_FEE, DEFAULT_RETURN_MEANS, DEFAULT_RETURN_STD_DEVS,
    DEFAULT_STOCK_DIVIDEND_RATE,
)

class Investment:
    # Define typology details with mean and standard deviation for annual gains
    TOPOLOGY_DETAILS = {
        name: {"mean": DEFAULT_RETURN_MEANS[name], "std_dev": DEFAULT_RETURN_STD_DEVS[name]}
        for name in DEFAULT_RETURN_MEANS
    }
    DIVIDEND = DEFAULT_STOCK_DIVIDEND_RATE
    CONTRIBUTION_FEE = DEFAULT_CONTRIBUTION_FEE
    AUTO_INVEST_TYPES = {'stock', 'safe'}

    def __init__(self, name: str, typology: str, initial_cost: float,
                 monthly_contribution: float, current_value: float = None,
                 auto_invest_enabled: bool = False,
                 auto_invest_min_cash: float = 10000,
                 auto_invest_percentage: float = 10,
                 auto_invest_max_per_year: int = 3,
                 annual_return_mean: float = None,
                 annual_return_std_dev: float = None,
                 dividend_rate: float = None,
                 contribution_fee: float = None):
        if typology not in self.TOPOLOGY_DETAILS:
            raise ValueError(f"Invalid typology. Must be one of {list(self.TOPOLOGY_DETAILS.keys())}")
        if auto_invest_enabled and typology not in self.AUTO_INVEST_TYPES:
            raise ValueError("Automatic contributions are only supported for stock and safe investments")
        if auto_invest_min_cash < 0:
            raise ValueError("Automatic contribution minimum cash cannot be negative")
        if not 0 < auto_invest_percentage <= 100:
            raise ValueError("Automatic contribution percentage must be between 0 and 100")
        if auto_invest_max_per_year < 1:
            raise ValueError("Automatic contribution maximum per year must be positive")

        self.name = name
        self.typology = typology
        self.initial_cost = initial_cost
        self.monthly_contribution = monthly_contribution
        self.auto_invest_enabled = auto_invest_enabled
        self.auto_invest_min_cash = auto_invest_min_cash
        self.auto_invest_percentage = auto_invest_percentage
        self.auto_invest_max_per_year = auto_invest_max_per_year
        defaults = self.TOPOLOGY_DETAILS[typology]
        self.annual_return_mean = (
            defaults['mean'] if annual_return_mean is None else annual_return_mean
        )
        self.annual_return_std_dev = (
            defaults['std_dev'] if annual_return_std_dev is None else annual_return_std_dev
        )
        self.dividend_rate = self.DIVIDEND if dividend_rate is None else dividend_rate
        self.contribution_fee = (
            self.CONTRIBUTION_FEE if contribution_fee is None else contribution_fee
        )
        self.current_value = current_value or initial_cost
        self.compounded_annual_gain = 1.0 + self._random_annual_gain_percentage() / 100
        self.months_since_last_compound = 0
        self.months_since_beginning = 0
        self.historical_values = []

    def _random_annual_gain_percentage(self) -> float:
        """
        Return a Gaussian distributed annual gain percentage based on the typology.
        """
        return random.gauss(self.annual_return_mean, self.annual_return_std_dev)


    def step(self):
        """
        Advance the investment by one month.
        """
        self.months_since_beginning+=1

        monthly_factor = (self.compounded_annual_gain) ** (1/12)  # pro-rata based on the last compounded yearly value
        self.current_value = self.current_value * monthly_factor

        self.months_since_last_compound += 1
        if self.months_since_last_compound == 12:  # Once a year, update the annual gain percentage
            self.compounded_annual_gain = 1.0 + self._random_annual_gain_percentage() / 100
            self.months_since_last_compound = 0

        self.historical_values.append(self.current_value)
        # dividend
        if self.typology=='stock' and self.months_since_beginning % 3 ==0:
          return self.current_value*self.dividend_rate/12*3 # annual->month  ####TODO: taxation

    def add_contribution(self, amount):
        """
        Add a gross contribution and return the fee deducted from that amount.
        """
        if amount <= 0:
          return 0
        fee = 0
        if self.typology in ['stock', 'bonds', 'safe']:
          fee = min(self.contribution_fee, amount)
        self.current_value += amount - fee
        return fee

    def reset(self):
        """
        Reset the investment to its initial parameters.
        """
        self.current_value = self.initial_cost
        self.compounded_annual_gain = 1.0 + self._random_annual_gain_percentage() / 100
        self.months_since_last_compound = 0
        self.months_since_beginning = 0
        self.historical_values = []

    def __repr__(self):
        return f"Investment(name={self.name}, typology={self.typology}, initial_cost={self.initial_cost}, monthly_contribution={self.monthly_contribution}, current_value={self.current_value:.2f})"
