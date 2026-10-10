"""
Please import the needed files, I had no time to make a package yet
"""

from bank_account import BankAccount
from expense import Expense
from income_source import IncomeSource
from investment import Investment
from loan import Loan
from utils import compute_net_worth_over_time

my_loans = [Loan(name='house loan',amount=10000,
                 duration_years=25)
]


my_investments = [
    Investment(name='my portfolio',initial_cost=1000,typology='stock',monthly_contribution=100),
    Investment(name='Safety fund',initial_cost=500,typology='safe', monthly_contribution=100),
    ]

my_expenses = [
    Expense('Kid School', expense_type='education',monthly_expense=100),
    Expense('Housing', expense_type='housing',monthly_expense=600),
    Expense('Gas, electricity, water, ...', expense_type='utilities',
            monthly_expense=100+50),
    Expense('Phone, internet', expense_type='utilities',
            monthly_expense=50),
    Expense('Groceries', expense_type='groceries',monthly_expense=300),
    Expense('Medical', expense_type='healthcare',monthly_expense=50),
    Expense('Public transport', expense_type='transportation',monthly_expense=50),
    Expense('Misc no extr', expense_type='miscellaneous',monthly_expense=150),
    Expense('Extra', expense_type='miscellaneous',monthly_expense=50),
    Expense(name='Unexpected',expense_type='unexpected',monthly_expense=0),
    Expense('Going out', expense_type='entertainment',monthly_expense=100)
]

income_sources = [
    IncomeSource(name='Salary', income_type='salary',
                 monthly_income=36000/12, bonus_pct=2),
    IncomeSource(name='Rent',income_type='rent',
                 monthly_income=100)
]



# Use a modest cash reserve while keeping the illustrative scenario solvent.
bank_account = BankAccount(initial_balance=5000, yearly_fee=110)

[i.reset() for i in my_investments]

[i.reset() for i in my_loans]

[i.reset() for i in income_sources]


def run_example(years=5, seed=42):
    """Run the documented example and return the simulation results."""
    return compute_net_worth_over_time(
        years,
        bank_account,
        my_investments,
        my_loans,
        income_sources,
        my_expenses,
        seed=seed,
    )


if __name__ == "__main__":
    net_worth_values, repay_dates, monthly_detail = run_example()
    print(f"Simulation complete: {len(net_worth_values)} months")
    print(f"Final net worth: {net_worth_values[-1]:.2f}")
    print(f"Loans repaid: {len(repay_dates)}")

"""
# Use with Confidence Intervals:

results = []
for i in range(15):
  print(i)
  bank_account = BankAccount(initial_balance=2000, yearly_fee=110)


  # it can actually get lower as well! Oscillates due to compressions in the stock
  # market...
  [i.reset() for i in my_investments]

  [i.reset() for i in my_loans]

  [i.reset() for i in income_sources]
  [i.reset() for i in my_expenses]

  net_worth_values, repay_dates, monthly_detail = compute_net_worth_over_time(12, bank_account,
                                                              my_investments, my_loans, income_sources, my_expenses)

  results.append(monthly_detail)
  
plot_with_confidence_intervals(
    data = [x['bank_account'] for x in results], rolling_window=6,
    title='Bank account evolution with C.I.'
)
"""
