import random

class Expense:
    def __init__(self, name, expense_type, monthly_expense):
        self.name = name
        self.expense_type = expense_type
        self.monthly_expense = monthly_expense
        self.original_monthly_expense = monthly_expense
        self.months_passed = 0  # Counter to track months
        
        # Define typical yearly growth percentages for different expense types
        # These values are illustrative and might need adjustments based on more specific data
        self.expense_growth = {
            'housing': 1.5,        # Slight increments might be observed due to rent or tax adjustments
            'utilities': 2.0,      # Utility prices might slightly increase over time
            'groceries': 2.0,      # General inflation might affect grocery prices
            'transportation': 2.5, # Fuel and public transport ticket prices might vary
            'healthcare': 3.0,     # Healthcare costs can increase, especially if there's no universal healthcare
            'education': 2.0,      # School fees and costs might increase
            'entertainment': 2.5,  # The cost of leisure activities can vary
            'clothing': 2.0,       # Prices for clothing might increase slightly over time
            'vacations': 2.5,      # Vacation costs can vary based on destination and travel mode
            'miscellaneous': 2.0   # Miscellaneous costs might slightly increase
        }

        # Ensure the provided expense type is valid
        if self.expense_type not in self.expense_growth:
            raise ValueError(f"Unknown expense type: {expense_type}")

    def step(self):
        """
        Returns the monthly expense.
        At the end of each year (every 12 steps), it adjusts the monthly expense based on its type.
        """
        self.months_passed += 1
        current_expense = self.monthly_expense
        
        # Check if it's the end of the year (assuming the function is called once a month)
        if self.months_passed % 12 == 0:
            self._adjust_yearly_expense()
        
        return current_expense

    def _adjust_yearly_expense(self):
        """
        Adjusts the monthly expense value at the end of each year based on its type.
        """
        growth_rate = self.expense_growth[self.expense_type] / 100
        self.monthly_expense += self.monthly_expense * growth_rate

    def reset(self):
        """
        Reset the expense to its initial parameters.
        """
        self.monthly_expense = self.original_monthly_expense
        self.months_passed = 0
