import random
import matplotlib.pyplot as plt

class Investment:
    # Define typology details with mean and standard deviation for annual gains
    TOPOLOGY_DETAILS = {
        'stock': {'mean': 8.0, 'std_dev': 20.0},         # Stock: mean of 8% annual gain, std deviation of 20%
        'real_estate': {'mean': 4.0, 'std_dev': 8.0},   # Real Estate: mean of 4% annual gain, std deviation of 8%
        'bonds': {'mean': 2.5, 'std_dev': 5.0},         # Bonds: mean of 2.5% annual gain, std deviation of 5%
        'safe': {'mean': 2, 'std_dev': 1.0}
        # ... add other typologies as needed
    }
    DIVIDEND = 0.0237 # typical, pct
    CONTRIBUTION_FEE = 50
    AUTO_INVEST_TYPES = {'stock', 'safe'}

    def __init__(self, name: str, typology: str, initial_cost: float,
                 monthly_contribution: float, current_value: float = None,
                 auto_invest_enabled: bool = False,
                 auto_invest_min_cash: float = 10000,
                 auto_invest_percentage: float = 10,
                 auto_invest_max_per_year: int = 3):
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
        self.current_value = current_value or initial_cost
        self.compounded_annual_gain = 1.0 + self._random_annual_gain_percentage() / 100
        self.months_since_last_compound = 0
        self.months_since_beginning = 0
        self.historical_values = []

    def _random_annual_gain_percentage(self) -> float:
        """
        Return a Gaussian distributed annual gain percentage based on the typology.
        """
        typology_details = self.TOPOLOGY_DETAILS[self.typology]
        return random.gauss(typology_details['mean'], typology_details['std_dev'])


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
          return self.current_value*self.DIVIDEND/12*3 # annual->month  ####TODO: taxation

    def add_contribution(self, amount):
        """
        Add a gross contribution and return the fee deducted from that amount.
        """
        if amount <= 0:
          return 0
        fee = 0
        if self.typology in ['stock', 'bonds', 'safe']:
          fee = min(self.CONTRIBUTION_FEE, amount)
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
