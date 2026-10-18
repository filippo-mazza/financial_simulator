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
from expense import Expense, YearlyExpense
from bank_account import InsufficientFundsException
from property_purchase import execute_property_purchase, AutomaticPurchasePolicy


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
                                expenses, seed=None, property_purchase=None,
                                assumptions=None, selected_rent_expense=None):
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
        "automatic_contributions": {name: [] for name in investment_names},
        "automatic_contribution_fees": {name: [] for name in investment_names},
        "income_rates": {name: [] for name in income_names},
        "income_values": {name: [] for name in income_names},
        "expense_values": {name: [] for name in expense_names},
        "loan_balances": {name: [] for name in loan_names},
        "loan_payments": {name: [] for name in loan_names},
    }
    if property_purchase is not None:
        monthly_details["purchase_fees"] = []
        monthly_details["purchase_events"] = []
    automatic_purchase = isinstance(property_purchase, AutomaticPurchasePolicy)
    if automatic_purchase:
        monthly_details["purchase_outcome"] = {
            "purchased": False,
            "purchase_month": None,
            "purchase_price": None,
            "down_payment": None,
            "transaction_fees": None,
            "mortgage_principal": None,
        }
    monthly_net_worth = []
    loan_repayment_dates = {}
    scheduled_purchase = None
    if property_purchase is not None:
        if automatic_purchase:
            property_purchase.validate()
            scheduled_purchase = None
        else:
            scheduled_purchase = property_purchase
        if (not automatic_purchase
                and property_purchase.purchase_month >= years * 12):
            raise ValueError("Purchase month must fall within the simulation")
        # Validate all non-cash constraints before the first scheduled
        # transaction; affordability is checked again at the closing month.
        if not automatic_purchase:
            property_purchase.validate(None, investments, loans)
     

    try:
      for year in range(years):
        automatic_contribution_counts = [0] * len(investments)
        year_income = 0
        year_expense = 0
        
        for month in range(12):
            current_month = year * 12 + month  # Counting months from the start
            month_income_rates = {}
            month_income_values = {}
            month_expense_values = {}
            month_loan_payments = {}
            month_automatic_contributions = {}
            month_automatic_contribution_fees = {}
            month_purchase_fee = 0
            
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
            
            purchase_due = (
                scheduled_purchase is not None
                and scheduled_purchase.purchase_month == current_month
            )
            if purchase_due and selected_rent_expense is not None:
                matches = [item for item in expenses if item.name == selected_rent_expense]
                if len(matches) != 1:
                    raise ValueError("selected_rent_expense must identify exactly one existing expense")
                matches[0].end()
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

            # Optional legacy-style cash sweep, configured independently for
            # each eligible investment. Disabled is the default.
            for index, (investment, investment_name) in enumerate(
                    zip(investments, investment_names)):
                enabled = getattr(investment, "auto_invest_enabled", False)
                min_cash = getattr(investment, "auto_invest_min_cash", 10000)
                percentage = getattr(investment, "auto_invest_percentage", 10)
                max_per_year = getattr(investment, "auto_invest_max_per_year", 3)
                eligible = getattr(investment, "typology", None) in ("stock", "safe")
                if not (enabled and eligible):
                    continue
                if automatic_contribution_counts[index] >= max_per_year:
                    continue
                if bank_account.subaccounts["Main"] <= min_cash:
                    continue
                if year_expense >= year_income * 0.8:
                    continue

                gross_contribution = bank_account.total_balance() * percentage / 100
                _withdraw_for_simulation(
                    bank_account, gross_contribution, current_month,
                    f"automatic contribution to '{investment_name}'",
                )
                value_before_contribution = investment.current_value
                investment.add_contribution(gross_contribution)
                fee = min(
                    getattr(
                        investment, "contribution_fee",
                        getattr(investment, "CONTRIBUTION_FEE", 50),
                    ),
                    gross_contribution,
                )
                # A cached pre-feature class always subtracts $50, which can
                # make a small contribution reduce the investment. Normalize
                # its mutation to the current capped-fee semantics.
                investment.current_value = (
                    value_before_contribution + gross_contribution - fee
                )
                month_automatic_contributions[investment_name] = (
                    month_automatic_contributions.get(investment_name, 0)
                    + gross_contribution
                )
                month_automatic_contribution_fees[investment_name] = (
                    month_automatic_contribution_fees.get(investment_name, 0) + fee
                )
                automatic_contribution_counts[index] += 1

            # Evaluate automatic affordability at the actual closing point,
            # after this month's obligations and cash sweeps. Waiting is a
            # normal outcome when the conditions are not met.
            if automatic_purchase and not monthly_details["purchase_outcome"]["purchased"]:
                cash = bank_account.subaccounts["Main"]
                refundable_rent = (
                    month_expense_values.get(selected_rent_expense, 0)
                    if selected_rent_expense is not None else 0
                )
                # Selected rent is reversed if closing occurs this month, so
                # include it when deciding whether the closing is affordable.
                closing_cash = cash + refundable_rent
                if closing_cash >= property_purchase.minimum_required_cash:
                    scheduled_purchase = property_purchase.purchase_for_month(current_month)
                    scheduled_purchase.validate(None, investments, loans)
                    purchase_due = True

            if automatic_purchase and purchase_due and selected_rent_expense is not None:
                matches = [
                    item for item in expenses if item.name == selected_rent_expense
                ]
                if len(matches) != 1:
                    raise ValueError(
                        "selected_rent_expense must identify exactly one existing expense"
                    )
                selected_rent = matches[0]
                rent_paid = month_expense_values.get(selected_rent_expense, 0)
                if rent_paid:
                    # Closing makes the selected rent inactive for this month,
                    # so reverse the payment processed before the end-of-month
                    # automatic eligibility decision.
                    bank_account.deposit(rent_paid, account_name="Main")
                    year_expense -= rent_paid
                    month_expense_values[selected_rent_expense] = 0
                selected_rent.end()

            # A purchase closes after all scheduled activity for the month.
            # Appending its mortgage here means the first repayment is due next
            # month, while the new property is included in this month's snapshot.
            if purchase_due:
                event = execute_property_purchase(
                    scheduled_purchase, bank_account, investments, loans,
                    annual_return_mean=(
                        assumptions.investment_return_means["real_estate"]
                        if assumptions is not None else None
                    ),
                    annual_return_std_dev=(
                        assumptions.investment_return_std_devs["real_estate"]
                        if assumptions is not None else None
                    ),
                )
                month_purchase_fee = event["transaction_fees"]
                monthly_details["purchase_events"].append(event)
                if automatic_purchase:
                    monthly_details["purchase_outcome"].update(
                        purchased=True,
                        purchase_month=current_month,
                        purchase_price=event["purchase_price"],
                        down_payment=event["down_payment"],
                        transaction_fees=event["transaction_fees"],
                        mortgage_principal=event["mortgage_principal"],
                    )
                investment_names.append(event["property_name"])
                monthly_details["investment_values"][event["property_name"]] = (
                    [0] * current_month
                )
                if event["mortgage_name"] is not None:
                    loan_names.append(event["mortgage_name"])
                    monthly_details["loan_balances"][event["mortgage_name"]] = (
                        [0] * current_month
                    )
                    monthly_details["loan_payments"][event["mortgage_name"]] = (
                        [0] * current_month
                    )
                for linked in (
                    Expense(f"{event['property_name']} — property expenses (linked)", "housing", event["monthly_expenses"])
                    if event["monthly_expenses"] > 0 else None,
                    YearlyExpense(f"{event['property_name']} — property tax (linked)", event["yearly_tax"])
                    if event["yearly_tax"] > 0 else None,
                ):
                    if linked is not None:
                        if linked.name in expense_names:
                            raise ValueError(f"Linked property expense name '{linked.name}' already exists")
                        expenses.append(linked)
                        expense_names.append(linked.name)
                        monthly_details["expense_values"][linked.name] = [0] * current_month

            # Store ending details after every transaction for the month, so
            # cash and investments reconcile with the net-worth snapshot.
            monthly_details["investments"].append(total_investment_value(investments))
            monthly_details["bank_account"].append(bank_account.subaccounts["Main"])
            if property_purchase is not None:
                monthly_details["purchase_fees"].append(month_purchase_fee)
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
            for name, values in monthly_details["automatic_contributions"].items():
                values.append(month_automatic_contributions.get(name, 0))
            for name, values in monthly_details["automatic_contribution_fees"].items():
                values.append(month_automatic_contribution_fees.get(name, 0))
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

            # Compute net worth for the month
            net_worth = get_net_worth(investments, loans, bank_account)
            monthly_net_worth.append(net_worth)
    finally:
        if random_state is not None:
            random.setstate(random_state)
            
    return monthly_net_worth, loan_repayment_dates, monthly_details



