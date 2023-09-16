import random
class IncomeSource:
    def __init__(self, name, income_type, monthly_income,
                 specific_taxation=None, bonus_pct = None):
        self.name = name
        self.income_type = income_type
        self.monthly_income = monthly_income
        self.original_monthly_income = monthly_income
        self.months_passed = 0  # Counter to track months
        self.yearly_income = self.monthly_income*12

        if specific_taxation is not None and specific_taxation < 5:
          raise ValueError('Taxation must be between 5 and 100')

        self.specific_taxation = specific_taxation
        self.bonus_pct = bonus_pct

        # Define typical yearly growth percentages for different income types
        self.income_growth = {
            'salary': 2.0,
            'rent': 1.2,
            'dividend': 1.3,
            'royalty': 1.5,
            'freelance': 3.0,
            'pension': 1.0
            # ... Add other types as needed.
        }

        # Define taxation percentages for different income types
        # Assuming these are average values and can be adjusted
        self.income_taxation = {
            'salary': 30.0,      # Assuming 25% average taxation on salary
            'rent': 15.0,        # Rental income tax might be lower
            'dividend': 20.0,    # Taxation on dividends
            'royalty': 15.0,     # Royalties might have varied tax structures
            'freelance': 30.0,   # Freelancers might pay higher taxes depending on expenses and deductions
            'pension': 10.0      # Lower taxes on pensions
            # ... Add other types as needed.
        }

        # Add a dictionary to define chances of having no or partial income for freelancers
        # - 'none' indicates no income for the month
        # - 'partial' indicates a percentage (e.g., 50% indicates half the usual income)
        self.freelance_income_variability = {
            'none': 0.05,  # 5% chance of having no income for the month
            'partial': 0.2  # 20% chance of having half the usual income
        }

        # Ensure the provided income type is valid
        if self.income_type not in self.income_growth:
            raise ValueError(f"Unknown income type: {income_type}")

    def _random_annual_bonus_percentage(self) -> float:
        """
        Return a Gaussian distributed annual gain percentage based on the typology.
        """
        yearly_bonus = random.gauss(self.bonus_pct*0.7, 1) #not always a good bonus
        return max(0,min(self.bonus_pct,yearly_bonus)) # no more than max, no less than 0


    def step(self):
        """
        Returns the monthly income after tax.
        At the end of each year (every 12 steps), it adjusts the monthly income based on its type.
        """
        self.months_passed += 1
        current_income = self.monthly_income

        # Special variability for freelancers
        if self.income_type == 'freelance':
            if random.random() < self.freelance_income_variability['none']:
                current_income = 0  # No income this month
            elif random.random() < self.freelance_income_variability['partial']:
                current_income *= 0.5  # Half the usual income this month

        if self.months_passed>11 and self.months_passed%12==0 and self.bonus_pct:
            bonus = self.yearly_income*self._random_annual_bonus_percentage()/100
            current_income += bonus

        tax = self.specific_taxation if self.specific_taxation is not None else self.income_taxation[self.income_type]
        net_income = current_income * (1 - tax / 100)


        # Check if it's the end of the year (assuming the function is called once a month)
        if self.months_passed > 11 and self.months_passed % 12 == 0:
            self._adjust_yearly_income()

        return net_income

    def _adjust_yearly_income(self):
        """
        Adjusts the monthly income value at the end of each year based on its type.
        """
        growth_rate = self.income_growth[self.income_type] / 100
        self.monthly_income += self.monthly_income * growth_rate
        self.yearly_income = self.monthly_income*12

    def reset(self):
        """
        Reset the income source to its initial parameters.
        """
        self.monthly_income = self.original_monthly_income
        self.months_passed = 0


    def __repr__(self):
        return f"Salary(name={self.name}, monthly_income={self.monthly_income})"


