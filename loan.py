import math

class Loan:
    def __init__(self, name, amount, interest_rate=2, duration_years=25, 
                 is_fixed_rate=False, already_paid=0):
        self.name = name
        self.initial_amount = amount
        self.remaining_amount = amount - already_paid  # Deduct the already paid amount
        self.interest_rate = interest_rate / 100  # Convert to a fraction for calculations
        self.duration_years = duration_years
        self.is_fixed_rate = is_fixed_rate
        self.fixed_interest = self.initial_amount * self.interest_rate / 12 if self.is_fixed_rate else 0
        self.monthly_repayment = self.calculate_monthly_repayment()
        self.is_repaid = False

    def calculate_monthly_repayment(self):
        if self.is_fixed_rate:
            interest = self.fixed_interest
            principal = (self.initial_amount / (self.duration_years * 12)) + interest
            return principal
        else:
            # We're using the formula for an annuity to calculate fixed monthly repayments
            return self.remaining_amount * (self.interest_rate / 12) / (1 - math.pow(1 + self.interest_rate / 12, -self.duration_years * 12))

    def step(self):
        if self.is_repaid:
            return None

        # Calculate monthly interest and principal repayment
        if self.is_fixed_rate:
            interest = self.fixed_interest
        else:
            interest = self.remaining_amount * self.interest_rate / 12
        principal = self.monthly_repayment - interest

        # Update remaining amount
        self.remaining_amount -= principal

        # Check if loan is repaid
        if self.remaining_amount <= 0:
            self.is_repaid = True
            self.remaining_amount = 0
            return self.monthly_repayment

        return self.monthly_repayment

    def reset(self):
        self.remaining_amount = self.initial_amount
        self.is_repaid = False
        self.monthly_repayment = self.calculate_monthly_repayment()

    def __repr__(self):
        #, months_left={self.months_left}
        return f"<Loan(name='{self.name}', remaining_amount={self.remaining_amount:.2f})>"

