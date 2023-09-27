import random

class Expense:
    def __init__(self, name, expense_type, monthly_expense):
        self.name = name
        self.expense_type = expense_type
        self.monthly_expense = monthly_expense
        self.original_monthly_expense = monthly_expense

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
            'miscellaneous': 2.0,   # Miscellaneous costs might slightly increase
            'unexpected': 2.0
        }

        # Ensure the provided expense type is valid
        if self.expense_type not in self.expense_growth:
            raise ValueError(f"Unknown expense type: {expense_type}")

        # Dictionary of common unexpected expenses for families
        self.original_unexpected_expenses = {
            'medical_bill': 900,
            'car_repair': 500,
            'house_repair': 1200,
            'school_trip': 180,
            'lost_phone': 600,
            'emergency_travel': 1600
        }

        self.reset()

    def step(self):
        """
        Returns the monthly expense.
        At the end of each year (every 12 steps), it adjusts the monthly expense based on its type.
        """
        self.months_passed += 1
        current_expense = 0

        if self.expense_type == 'unexpected':
            # Chance of an unexpected expense occurring. Here, it's set to 10% as an example.
            if random.random() < 0.06:
                selected_expense = random.choice(list(self.unexpected_expenses.keys()))
                current_expense = self.unexpected_expenses[selected_expense]
                print(selected_expense + "("+str(int(current_expense))+")")
                # no return as we need to adjust it still
        else:
          current_expense = self.monthly_expense


        # Check if it's the end of the year (assuming the function is called once a month)
        if self.months_passed % 12 == 0:
            self._adjust_yearly_expense()

        return current_expense

    def _adjust_yearly_expense(self):
        """
        Adjusts the monthly expense value at the end of each year based on its type.
        """
        inflation_rate = self.expense_growth[self.expense_type] / 100
        if self.expense_type == 'unexpected':

          self.unexpected_expenses = {k:expense_value * (1 + inflation_rate) for \
                                      k,expense_value in self.unexpected_expenses.items()}
        else:
          self.monthly_expense *= 1+inflation_rate

    def reset(self):
        """
        Reset the expense to its initial parameters.
        """
        self.monthly_expense = self.original_monthly_expense
        #self.original_unexpected_expenses = self.unexpected_expenses
        self.months_passed = 0  # Counter to track months
        self.unexpected_expenses = self.original_unexpected_expenses



    def __repr__(self):
        return f"Expense(name={self.name}, monthly={self.monthly_expense})"
