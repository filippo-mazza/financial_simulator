def total_investment_value(investments):
    """Returns the total current value of a list of investments."""
    return sum(investment.current_value for investment in investments)

# Example:
# investments_list = [Investment(name="Stocks", initial_cost=1000, monthly_contribution=50, typology="stock"),
#                     Investment(name="Bonds", initial_cost=500, monthly_contribution=25, typology="bond")]
# total_value = total_investment_value(investments_list)
# print(total_value)

import numpy as np

def get_net_worth(investments,loans,bank_account):
    # Compute the current net worth
    total_investments = sum(invest.current_value for invest in investments)
    total_loans = sum(loan.remaining_amount for loan in loans)
    return bank_account.total_balance() + total_investments - total_loans

import random


def compute_net_worth_over_time(years, bank_account, investments, loans, income_sources,
                                expenses, seed=None):
    """Simulate net worth month by month.

    When ``seed`` is provided, all simulations using the standard-library random
    module are reproducible. The caller's random state is restored afterwards so
    running a simulation does not alter unrelated application randomness.
    """
    random_state = random.getstate() if seed is not None else None
    if seed is not None:
        random.seed(seed)

    monthly_details = {"investments": [], "bank_account": []}
    monthly_net_worth = []
    loan_repayment_dates = {}
     

    try:
      for year in range(years):
        additional_contributions = 0
        year_income = 0
        year_expense = 0
        
        for month in range(12):
            current_month = year * 12 + month  # Counting months from the start
            
            # Income
            for source in income_sources:
                monthly_income = source.step()
                year_income+=monthly_income
                bank_account.deposit(monthly_income, account_name="Main")
            
            # Expenses
            for expense in expenses:
                monthly_expense = expense.step()
                year_expense+=monthly_expense
                bank_account.withdraw(monthly_expense, account_name="Main")
            
            # Loan Repayments
            for loan in loans:
                monthly_repayment = loan.step()
                if monthly_repayment is not None: # not already paid
                  bank_account.withdraw(monthly_repayment, account_name="Main")
                if loan.is_repaid and loan.name not in loan_repayment_dates:
                    loan_repayment_dates[loan.name] = current_month
            
            # Investments
            total_monthly_investments = 0
            for investment in investments:
                gain = investment.step()
                if gain is not None:
                    total_monthly_investments += gain
                    bank_account.deposit(gain, account_name="Main")

                # Monthly contribution to every investment type. Zero skips
                # the transaction entirely, including any fixed fee.
                if investment.monthly_contribution > 0:
                    if bank_account.subaccounts["Main"] > investment.monthly_contribution:
                        bank_account.withdraw(investment.monthly_contribution, account_name="Main")
                        investment.add_contribution(investment.monthly_contribution)
                  

            # Apply account fees at the end of each simulated year.
            if month == 11:
                bank_account.apply_yearly_fees()

            # Store details
            monthly_details["investments"].append(total_investment_value(investments))
            monthly_details["bank_account"].append(bank_account.subaccounts["Main"])
            

            # Yearly additional to stocks
            if bank_account.subaccounts['Main'] > 10000 and additional_contributions < 3:  # todo monthly
                if year_expense < year_income * 0.8:
                    stock_investments = [x for x in investments if x.typology == 'stock']
                    if stock_investments:
                        stock_inv = stock_investments[0]
                        x = bank_account.total_balance() * 0.1
                        bank_account.withdraw(x, account_name="Main")
                        stock_inv.add_contribution(x)
                        additional_contributions += 1

            # Compute net worth for the month
            net_worth = get_net_worth(investments, loans, bank_account)
            monthly_net_worth.append(net_worth)
    finally:
        if random_state is not None:
            random.setstate(random_state)
            
    return monthly_net_worth, loan_repayment_dates, monthly_details



