"""Streamlit interface for the financial simulator.

The model-building functions are intentionally independent of Streamlit so they
can be exercised in unit tests and reused by another front end later.
"""

from dataclasses import dataclass, field
import random

from bank_account import BankAccount, InsufficientFundsException
from expense import Expense
from income_source import IncomeSource
from investment import Investment
from loan import Loan
from utils import compute_net_worth_over_time


@dataclass
class ExpenseConfig:
    name: str
    expense_type: str
    monthly_amount: float


@dataclass
class DependentConfig:
    name: str
    monthly_cost: float


@dataclass
class SimulationConfig:
    years: int = 10
    seed: int = 42
    initial_cash: float = 5000.0
    yearly_fee: float = 110.0
    monthly_income: float = 3000.0
    income_type: str = "salary"
    tax_rate: float = 0.0
    bonus_pct: float = 0.0
    monthly_expense: float = 1500.0
    expense_type: str = "housing"
    investment_type: str = "stock"
    investment_initial: float = 1000.0
    investment_contribution: float = 100.0
    loan_amount: float = 10000.0
    loan_rate: float = 2.0
    loan_duration: int = 25
    fixed_rate: bool = False
    loan_already_paid: float = 0.0
    additional_expenses: list[ExpenseConfig] = field(default_factory=list)
    dependents: list[DependentConfig] = field(default_factory=list)


def build_components(config: SimulationConfig):
    """Create fresh model objects from UI/configuration values."""
    income = IncomeSource(
        "Primary income", config.income_type, config.monthly_income,
        specific_taxation=config.tax_rate or None,
        bonus_pct=config.bonus_pct or None,
    )
    expenses = [Expense("Living expenses", config.expense_type, config.monthly_expense)]
    expenses.extend(
        Expense(item.name, item.expense_type, item.monthly_amount)
        for item in config.additional_expenses
    )
    # A dependent is a recurring cost in the model. Keeping this explicit makes
    # it easy to evolve into age-based childcare/education rules later.
    expenses.extend(
        Expense(f"{dependent.name} (child)", "education", dependent.monthly_cost)
        for dependent in config.dependents
    )
    investment = Investment(
        "Portfolio", config.investment_type, config.investment_initial,
        config.investment_contribution,
    )
    loan = Loan(
        "Loan", config.loan_amount, config.loan_rate, config.loan_duration,
        config.fixed_rate, config.loan_already_paid,
    )
    account = BankAccount(config.initial_cash, config.yearly_fee)
    return account, [investment], [loan], [income], expenses


def run_simulation(config: SimulationConfig):
    """Run one isolated simulation and return its three result collections."""
    random_state = random.getstate()
    random.seed(config.seed)
    try:
        components = build_components(config)
        return compute_net_worth_over_time(config.years, *components, seed=config.seed)
    finally:
        random.setstate(random_state)


def _money(value):
    return f"${value:,.0f}"


def main():
    import matplotlib.pyplot as plt
    import streamlit as st

    st.set_page_config(page_title="Financial Simulator", page_icon="📈", layout="wide")
    st.markdown("""
    <style>
    .block-container { max-width: 1200px; padding-top: 2rem; }
    [data-testid="stMetricValue"] { color: #35c99a; }
    </style>
    """, unsafe_allow_html=True)
    st.title("📈 Financial Simulator")
    st.caption("Explore a simple, stochastic projection of cash, investments, income, expenses, and debt.")

    st.session_state.setdefault("additional_expenses", [])
    st.session_state.setdefault("dependents", [])

    def add_expense():
        st.session_state.additional_expenses.append({
            "name": "New expense", "expense_type": "miscellaneous", "monthly_amount": 100.0,
        })

    def add_dependent():
        number = len(st.session_state.dependents) + 1
        st.session_state.dependents.append({"name": f"Child {number}", "monthly_cost": 300.0})

    def remove_item(collection, index):
        collection.pop(index)

    with st.sidebar:
        st.header("Scenario")
        years = st.slider("Simulation horizon (years)", 1, 50, 10)
        seed = st.number_input("Random seed", 0, 1_000_000, 42)
        st.subheader("Cash")
        initial_cash = st.number_input("Starting cash", min_value=0.0, value=5000.0, step=500.0)
        yearly_fee = st.number_input("Annual bank fee", min_value=0.0, value=110.0, step=10.0)
        st.subheader("Income")
        income_type = st.selectbox("Income type", sorted(IncomeSource("x", "salary", 1).income_growth))
        monthly_income = st.number_input("Monthly gross income", min_value=0.0, value=3000.0, step=100.0)
        tax_rate = st.number_input("Custom tax rate (0 = model default)", 0.0, 100.0, 0.0, step=1.0)
        bonus_pct = st.number_input("Annual bonus cap (%)", 0.0, 100.0, 0.0, step=1.0)
        st.subheader("Expenses")
        expense_type = st.selectbox("Expense type", sorted(Expense("x", "housing", 1).expense_growth))
        monthly_expense = st.number_input("Monthly expense", min_value=0.0, value=1500.0, step=100.0)
        st.subheader("Investment")
        investment_types = sorted(Investment.TOPOLOGY_DETAILS)
        investment_type = st.selectbox("Investment type", investment_types,
                                       index=investment_types.index("stock"))
        investment_initial = st.number_input("Initial investment", min_value=0.0, value=1000.0, step=100.0)
        investment_contribution = st.number_input("Monthly contribution", min_value=0.0, value=100.0, step=50.0)
        st.subheader("Loan")
        loan_amount = st.number_input("Loan amount", min_value=0.0, value=10000.0, step=1000.0)
        loan_rate = st.number_input("Interest rate (%)", min_value=0.0, value=2.0, step=0.1)
        loan_duration = st.number_input("Duration (years)", min_value=1, max_value=50, value=25)
        fixed_rate = st.checkbox("Fixed-rate repayment")
        loan_already_paid = st.number_input("Already paid", min_value=0.0, value=0.0, step=100.0)

        st.subheader("Current situation")
        st.caption("Add the people and recurring costs that shape this scenario.")
        if st.button("＋ Add expense", use_container_width=True):
            add_expense()
        for index, item in enumerate(st.session_state.additional_expenses):
            with st.container(border=True):
                item["name"] = st.text_input("Name", item["name"], key=f"expense_name_{index}")
                item["expense_type"] = st.selectbox(
                    "Type", sorted(Expense("x", "housing", 1).expense_growth),
                    index=sorted(Expense("x", "housing", 1).expense_growth).index(item["expense_type"]),
                    key=f"expense_type_{index}",
                )
                item["monthly_amount"] = st.number_input(
                    "Monthly amount", min_value=0.0, value=float(item["monthly_amount"]),
                    step=25.0, key=f"expense_amount_{index}",
                )
                if st.button("Remove expense", key=f"remove_expense_{index}"):
                    remove_item(st.session_state.additional_expenses, index)
                    st.rerun()

        if st.button("＋ Add daughter / child", use_container_width=True):
            add_dependent()
        for index, item in enumerate(st.session_state.dependents):
            with st.container(border=True):
                item["name"] = st.text_input("Name", item["name"], key=f"dependent_name_{index}")
                item["monthly_cost"] = st.number_input(
                    "Monthly child cost", min_value=0.0, value=float(item["monthly_cost"]),
                    step=25.0, key=f"dependent_cost_{index}",
                )
                if st.button("Remove child", key=f"remove_dependent_{index}"):
                    remove_item(st.session_state.dependents, index)
                    st.rerun()

        if st.session_state.additional_expenses or st.session_state.dependents:
            st.caption(
                f"{len(st.session_state.additional_expenses)} additional expense(s) · "
                f"{len(st.session_state.dependents)} dependent(s)"
            )

    config = SimulationConfig(
        years=years, seed=int(seed), initial_cash=initial_cash, yearly_fee=yearly_fee,
        monthly_income=monthly_income, income_type=income_type, tax_rate=tax_rate,
        bonus_pct=bonus_pct, monthly_expense=monthly_expense, expense_type=expense_type,
        investment_type=investment_type, investment_initial=investment_initial,
        investment_contribution=investment_contribution, loan_amount=loan_amount,
        loan_rate=loan_rate, loan_duration=loan_duration, fixed_rate=fixed_rate,
        loan_already_paid=loan_already_paid,
        additional_expenses=[ExpenseConfig(**item) for item in st.session_state.additional_expenses],
        dependents=[DependentConfig(**item) for item in st.session_state.dependents],
    )
    try:
        net_worth, repayment_dates, details = run_simulation(config)
    except (ValueError, TypeError, KeyError, InsufficientFundsException) as error:
        st.error(f"This scenario cannot be simulated: {error}")
        return

    final_net_worth = net_worth[-1]
    starting_net_worth = net_worth[0]
    columns = st.columns(4)
    columns[0].metric("Final net worth", _money(final_net_worth), f"{final_net_worth - starting_net_worth:+,.0f}")
    columns[1].metric("Final cash", _money(details["bank_account"][-1]))
    columns[2].metric("Final investments", _money(details["investments"][-1]))
    columns[3].metric("Loans repaid", len(repayment_dates))

    figure, axis = plt.subplots(figsize=(12, 5))
    axis.plot(net_worth, label="Net worth", linewidth=2.5, color="#35c99a")
    axis.plot(details["bank_account"], label="Cash", alpha=0.8, color="#5b8def")
    axis.plot(details["investments"], label="Investments", alpha=0.8, color="#f4b942")
    for month, name in ((month, name) for name, month in repayment_dates.items()):
        axis.axvline(month, linestyle="--", alpha=0.4, color="#e06c75")
        axis.text(month, final_net_worth, name, rotation=90, fontsize=8)
    axis.set(xlabel="Month", ylabel="Amount", title="Projected financial position")
    axis.grid(alpha=0.2)
    axis.legend(loc="upper left")
    st.pyplot(figure, use_container_width=True)
    st.caption("Illustrative projection only — this is not financial advice. Results vary with the random seed.")


if __name__ == "__main__":
    main()
