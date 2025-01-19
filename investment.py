import random
import matplotlib.pyplot as plt

class Investment:


    def __init__(self, config,
                 name: str, typology: str, initial_cost: float, monthly_contribution: float, 
                 current_value: float = None):
        self.config = config
        if typology not in self.config['investment']['typology']:
          types = self.config['investment']['typology']
          raise ValueError(f'Invalid typology. Must be one of {list(types.keys())}')

        self.name = name
        self.typology = typology
        self.typology_details = self.config['investment']['typology'][self.typology]
        self.initial_cost = initial_cost
        self.monthly_contribution = monthly_contribution
        self.current_value = current_value or initial_cost
        self.compounded_annual_gain = 1.0 + self._random_annual_gain_percentage() / 100
        self.months_since_last_compound = 0
        self.months_since_beginning = 0
        self.historical_values = []

    def _random_annual_gain_percentage(self) -> float:
        """
        Return a Gaussian distributed annual gain percentage based on the typology.
        """
        return random.gauss(self.typology_details['mean'], self.typology_details['std_dev'])


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
          return self.current_value*self.config['investment']['dividend_yield']/12*3 # annual->month  ####TODO: taxation

    def add_contribution(self, amount):
        """
        Adds a specified amount to the investment.
        """
        if self.typology in ['stock','bonds','safe']:
          amount = amount - 50 # fees
        else:

          raise TypeError('Can\'t add to non stock-like accounts: ' + str(self))
        self.current_value += amount

    def reset(self):
        """
        Reset the investment to its initial parameters.
        """
        self.current_value = self.initial_cost
        self.compounded_annual_gain = 1.0 + self._random_annual_gain_percentage() / 100
        self.months_since_last_compound = 0

    def __repr__(self):
        return f"Investment(name={self.name}, typology={self.typology}, initial_cost={self.initial_cost}, monthly_contribution={self.monthly_contribution}, current_value={self.current_value:.2f})"
