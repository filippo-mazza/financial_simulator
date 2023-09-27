"""
Please import the needed files, I had no time to make a package yet
"""

my_loans = [Loan(name='house loan',amount=100000,
                 duration_years=25)
]


my_investments = [
    Investment(name='my portfolio',initial_cost=1000,typology='stock',monthly_contribution=100),
    Investment(name='Safety fund',initial_cost=500,typology='safe', monthly_contribution=100),
    ]

my_expenses = [
    Expense('Kid School', expense_type='education',monthly_expense=200),
    Expense('Housing', expense_type='housing',monthly_expense=800),
    Expense('Gas, electricity, water, ...', expense_type='utilities',
            monthly_expense=200+100+50),
    Expense('Phone, internet', expense_type='utilities',
            monthly_expense=80),
    Expense('Groceries', expense_type='groceries',monthly_expense=500),
    Expense('Medical', expense_type='healthcare',monthly_expense=100),
    Expense('Public transport', expense_type='transportation',monthly_expense=50),
    Expense('Misc no extr', expense_type='miscellaneous',monthly_expense=400),
    Expense('Extra', expense_type='miscellaneous',monthly_expense=100),
    Expense(name='Unexpected',expense_type='unexpected',monthly_expense=0),
    Expense('Going out', expense_type='entertainment',monthly_expense=150)
]

income_sources = [
    IncomeSource(name='Salary', income_type='salary',
                 monthly_income=12000/12, bonus_pct=2),
    IncomeSource(name='Rent',income_type='rent',
                 monthly_income=100)
]



bank_account = BankAccount(initial_balance=1000, yearly_fee=110)

[i.reset() for i in my_investments]

[i.reset() for i in my_loans]

[i.reset() for i in income_sources]


# Single use:
net_worth_values, repay_dates, monthly_detail = compute_net_worth_over_time(5, bank_account,
                                                            my_investments, my_loans, income_sources, my_expenses)

"""
# Use with Confidence Intervals:

results = []
for i in range(15):
  print(i)
  bank_account = BankAccount(initial_balance=21000, yearly_fee=110)


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
