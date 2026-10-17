#!/usr/bin/env python3
"""Run bounded deterministic financial checks and emit a compact JSON report."""

import json
import math
import os
from pathlib import Path
import sys
from dataclasses import asdict

sys.path.insert(0, os.environ.get(
    "FINANCIAL_REPOSITORY_PATH", str(Path(__file__).resolve().parent.parent)
))

from app import IncomeConfig, InvestmentConfig, SimulationConfig, run_simulation
from expense import Expense
from financial_assumptions import FinancialAssumptions
from income_source import IncomeSource
from loan import Loan


def check(name, actual, expected, tolerance=1e-7):
    passed = math.isclose(actual, expected, rel_tol=tolerance, abs_tol=tolerance)
    return {"name": name, "status": "PASS" if passed else "FAIL",
            "actual": round(actual, 6), "expected": round(expected, 6)}


def build_report():
    assumptions = asdict(FinancialAssumptions())
    assumptions["investment_return_means"] = dict.fromkeys(assumptions["investment_return_means"], 0)
    assumptions["investment_return_std_devs"] = dict.fromkeys(assumptions["investment_return_std_devs"], 0)
    assumptions["stock_dividend_rate"] = 0
    assumptions["contribution_fee"] = 50
    config = SimulationConfig(
        years=1, seed=7, initial_cash=10000, yearly_fee=0,
        incomes=[IncomeConfig("Synthetic salary", "salary", 1000, tax_rate=20)],
        expenses=[], loans=[], investments=[InvestmentConfig("Synthetic fund", "stock", 0, 100)],
        assumptions=FinancialAssumptions(**assumptions),
    )
    first = run_simulation(config)
    second = run_simulation(config)
    net, _, details = first
    checks = [
        {"name": "seeded reproducibility", "status": "PASS" if first == second else "FAIL"},
        check("first-month net income after tax", details["income_values"]["Synthetic salary"][0], 800),
        check("first-month investment contribution net of fee", details["investment_values"]["Synthetic fund"][0], 50),
        check("net-worth reconciliation", net[-1], details["bank_account"][-1] + details["investments"][-1]),
    ]
    loan = Loan("Synthetic mortgage", 1200, interest_rate=0, duration_years=1)
    payments = [loan.step() for _ in range(12)]
    checks += [check("mortgage amortization balance", loan.remaining_amount, 0),
               check("mortgage amortization payments", sum(payments), 1200)]
    expense = Expense("Synthetic housing", "housing", 100)
    for _ in range(12): expense.step()
    checks.append(check("annual expense inflation", expense.monthly_expense, 101.5))
    income = IncomeSource("Synthetic rent", "rent", 100, specific_taxation=0)
    for _ in range(12): income.step()
    checks.append(check("annual income growth", income.monthly_income, 101.2))
    return {"schema_version": 1, "status": "PASS" if all(c["status"] == "PASS" for c in checks) else "FAIL",
            "checks": checks, "limitations": ["Uses only financial behavior currently represented by the engine."]}


def main():
    report = build_report()
    json.dump(report, sys.stdout, sort_keys=True, separators=(",", ":"))
    print()
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__": raise SystemExit(main())
