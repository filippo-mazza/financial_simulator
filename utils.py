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

def compute_net_worth_over_time(years, bank_account, investments, loans, income_sources, expenses):
    monthly_details = {"investments": [], "bank_account": []}
    monthly_net_worth = []
    loan_repayment_dates = {}
     

    year_income = 0
    year_expense = 0
    
    for year in range(years):
        additional_contributions = 0
        
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

                # Monthly contribution to investment
                if bank_account.subaccounts["Main"] > investment.monthly_contribution:
                    if investment.typology == 'stock':
                      bank_account.withdraw(investment.monthly_contribution, account_name="Main")
                      investment.add_contribution(investment.monthly_contribution)
                  

            # Store details
            monthly_details["investments"].append(total_investment_value(investments))
            monthly_details["bank_account"].append(bank_account.subaccounts["Main"])
            

            # Yearly additional to stocks
            if bank_account.subaccounts['Main'] > 10000 and additional_contributions<3: #todo monthly
              if year_expense<year_income*0.8:
                  stock_inv = list(filter(lambda x: x.typology=='stock',my_investments))[0]
                  x=bank_account.total_balance()*0.1
                  bank_account.withdraw(x, account_name="Main")
                  stock_inv.add_contribution(x)

            year_income = 0
            year_expense = 0
            
            # Compute net worth for the month
            net_worth = get_net_worth(investments, loans, bank_account)
            monthly_net_worth.append(net_worth)
            
    return monthly_net_worth, loan_repayment_dates, monthly_details


import matplotlib.pyplot as plt

def plot_net_worth(monthly_net_worth, loan_repayment_dates, monthly_details=None):
    plt.style.use('ggplot')  # Use the ggplot style for a cleaner appearance
    
    plt.figure(figsize=(12, 6))
    plt.plot(monthly_net_worth, '-o', markersize=3, label="Net Worth", color='blue')

    # If monthly_details is provided, plot the values
    if monthly_details is not None:
        for key, values in monthly_details.items():
            plt.plot(values, '-o', markersize=3, label=key.capitalize())
    
    for loan_name, date in loan_repayment_dates.items():
        plt.axvline(x=date, color='r', linestyle='--')
        plt.annotate(loan_name, (date, min(monthly_net_worth)), textcoords="offset points", xytext=(0,10), ha='center')

    plt.title('Net Worth and Financial Details Over Time')
    plt.xlabel('Months')
    plt.ylabel('Amount ($)')  # Added $ unit of measure to the y-axis
    plt.legend()
    plt.grid(True)
    plt.tight_layout()
    plt.show()

# Example:
# monthly_net_worth, monthly_details, loan_repayment_dates = compute_net_worth_over_time(10, bank_account, investments, loans, incomes, expenses)
# plot_net_worth(monthly_net_worth, loan_repayment_dates, monthly_details)



