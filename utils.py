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
from bank_account import InsufficientFundsException


def _withdraw_for_simulation(bank_account, amount, month, reason):
    """Withdraw with enough context to explain an insolvent scenario."""
    balance = bank_account.subaccounts["Main"]
    try:
        bank_account.withdraw(amount, account_name="Main")
    except InsufficientFundsException as error:
        raise InsufficientFundsException(
            f"Simulation stopped in month {month + 1}: unable to pay {reason} "
            f"of \\${amount:,.2f} with \\${balance:,.2f} available."
        ) from error


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

    investment_names = [
        getattr(item, "name", f"Investment {index + 1}")
        for index, item in enumerate(investments)
    ]
    income_names = [
        getattr(item, "name", f"Income {index + 1}")
        for index, item in enumerate(income_sources)
    ]
    expense_names = [
        getattr(item, "name", f"Expense {index + 1}")
        for index, item in enumerate(expenses)
    ]
    loan_names = [
        getattr(item, "name", f"Loan {index + 1}")
        for index, item in enumerate(loans)
    ]
    monthly_details = {
        "investments": [],
        "bank_account": [],
        "investment_values": {name: [] for name in investment_names},
        "income_rates": {name: [] for name in income_names},
        "income_values": {name: [] for name in income_names},
        "expense_values": {name: [] for name in expense_names},
        "loan_balances": {name: [] for name in loan_names},
        "loan_payments": {name: [] for name in loan_names},
    }
    monthly_net_worth = []
    loan_repayment_dates = {}
     

    try:
      for year in range(years):
        additional_contributions = 0
        year_income = 0
        year_expense = 0
        
        for month in range(12):
            current_month = year * 12 + month  # Counting months from the start
            month_income_rates = {}
            month_income_values = {}
            month_expense_values = {}
            month_loan_payments = {}
            
            # Income
            for source, source_name in zip(income_sources, income_names):
                # Capture the regular gross monthly rate used for this month.
                # ``step`` applies annual growth after month 12, so recording it
                # first makes the new rate correctly appear from month 13.
                month_income_rates[source_name] = (
                    month_income_rates.get(source_name, 0)
                    + getattr(source, "monthly_income", getattr(source, "amount", 0))
                )
                monthly_income = source.step()
                year_income+=monthly_income
                bank_account.deposit(monthly_income, account_name="Main")
                month_income_values[source_name] = (
                    month_income_values.get(source_name, 0) + monthly_income
                )
            
            # Expenses
            for expense, expense_name in zip(expenses, expense_names):
                monthly_expense = expense.step()
                year_expense+=monthly_expense
                month_expense_values[expense_name] = (
                    month_expense_values.get(expense_name, 0) + monthly_expense
                )
                _withdraw_for_simulation(
                    bank_account, monthly_expense, current_month,
                    f"expense '{expense_name}'",
                )
            
            # Loan Repayments
            for loan, loan_name in zip(loans, loan_names):
                monthly_repayment = loan.step()
                month_loan_payments[loan_name] = (
                    month_loan_payments.get(loan_name, 0) + (monthly_repayment or 0)
                )
                if monthly_repayment is not None: # not already paid
                  _withdraw_for_simulation(
                      bank_account, monthly_repayment, current_month,
                      f"loan repayment '{loan_name}'",
                  )
                if loan.is_repaid and loan_name not in loan_repayment_dates:
                    loan_repayment_dates[loan_name] = current_month
            
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
            investment_values = {}
            for investment, investment_name in zip(investments, investment_names):
                investment_values[investment_name] = (
                    investment_values.get(investment_name, 0) + investment.current_value
                )
            loan_balances = {}
            for loan, loan_name in zip(loans, loan_names):
                loan_balances[loan_name] = (
                    loan_balances.get(loan_name, 0) + loan.remaining_amount
                )
            for name, values in monthly_details["investment_values"].items():
                values.append(investment_values.get(name, 0))
            for name, values in monthly_details["income_rates"].items():
                values.append(month_income_rates.get(name, 0))
            for name, values in monthly_details["income_values"].items():
                values.append(month_income_values.get(name, 0))
            for name, values in monthly_details["expense_values"].items():
                values.append(month_expense_values.get(name, 0))
            for name, values in monthly_details["loan_balances"].items():
                values.append(loan_balances.get(name, 0))
            for name, values in monthly_details["loan_payments"].items():
                values.append(month_loan_payments.get(name, 0))
            

            # Yearly additional to stocks
            if bank_account.subaccounts['Main'] > 10000 and additional_contributions < 3:  # todo monthly
                if year_expense < year_income * 0.8:
                    stock_investments = [x for x in investments if x.typology == 'stock']
                    if stock_investments:
                        stock_inv = stock_investments[0]
                        x = bank_account.total_balance() * 0.1
                        _withdraw_for_simulation(
                            bank_account, x, current_month,
                            f"additional contribution to '{getattr(stock_inv, 'name', 'unnamed')}'",
                        )
                        stock_inv.add_contribution(x)
                        additional_contributions += 1

            # Compute net worth for the month
            net_worth = get_net_worth(investments, loans, bank_account)
            monthly_net_worth.append(net_worth)
    finally:
        if random_state is not None:
            random.setstate(random_state)
            
    return monthly_net_worth, loan_repayment_dates, monthly_details



