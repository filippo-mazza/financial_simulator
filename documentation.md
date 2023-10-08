# Personal Finance Simulator Documentation

Documentation written by ChatGPT 4.

## Table of Contents
- [Introduction](#introduction)
- [Classes](#classes)
  - [BankAccount](#bankaccount)
  - [Investment](#investment)
  - [Loan](#loan)
  - [IncomeSource](#incomesource)
  - [Expense](#expense)
- [Disclaimer](#disclaimer)

## Introduction
This personal finance simulator is a simple Python tool designed to project future net worth based on various financial parameters. It encompasses several classes to mimic real-world financial elements like bank accounts, investments, loans, incomes, and expenses.

## Classes

### BankAccount
The `BankAccount` class models a user's bank account, handling transactions like deposits and withdrawals.

#### Methods
- `deposit(amount: float)`: Add funds to the bank account.
- `withdraw(amount: float)`: Remove funds from the bank account.
- `get_balance()`: Retrieve the current balance of the bank account.

### Investment
The `Investment` class models an investment, considering elements like contributions, withdrawals, and gain calculations based on a defined annual rate.

#### Methods
- `add_contribution(amount: float)`: Add an investment contribution.
- `withdraw(amount: float, penalty_rate: float)`: Withdraw a specified amount considering a penalty rate.
- `calculate_gain()`: Calculate and return the gain from the investment.

### Loan
The `Loan` class models a loan, calculating the monthly payment based on the principal, annual interest rate, and loan period (in years).

#### Methods
- `calculate_monthly_payment()`: Compute the monthly loan payment.
- `make_payment()`: Record a loan payment.

### IncomeSource
The `IncomeSource` class represents a source of income, adjusting income based on type, variability, and potential for annual bonuses.

#### Methods
- `step()`: Return monthly net income considering taxation, variability, and bonuses.
- `reset()`: Reset the income source to its initial parameters.

### Expense
The `Expense` class models a regular or unexpected expense, enabling variability in the case of the latter.

#### Methods
- `get_monthly_expense()`: Retrieve the monthly expense amount, considering potential variability.

## Disclaimer
All classes and methods in this simulator are purely illustrative and do not constitute financial advice. Ensure to test, validate, and adjust as per your use-case scenarios, considering consultation with a professional financial advisor for accurate financial planning.

---

Note: Ensure to further document each class and method with specific parameter and return type details, and perhaps examples, to provide comprehensive documentation for potential users or collaborators on Git. 
