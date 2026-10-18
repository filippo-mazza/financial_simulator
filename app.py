"""Streamlit interface for the financial simulator.

The model-building functions are intentionally independent of Streamlit so they
can be exercised in unit tests and reused by another front end later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import random
import uuid

from bank_account import BankAccount, InsufficientFundsException
from expense import AgeBasedExpense, Expense
from income_source import IncomeSource
from investment import Investment
from loan import Loan
from utils import compute_net_worth_over_time


MAX_SIMULATION_COUNT = 25
LOW_SAMPLE_THRESHOLD = 10
LOW_SAMPLE_WARNING = (
    "This is a small sample. Results are estimates based on the selected run count "
    "and are not reliable for tail probabilities or confidence claims."
)
SAMPLE_SIZE_HELP = (
    "The current maximum of 25 runs is a performance and export limit, not a "
    "guarantee of statistical reliability. A larger tail-risk option would need "
    "explicit runtime, capacity, and export-size decisions."
)


def validate_simulation_count(count: int) -> int:
    """Validate the intentionally bounded number of Monte Carlo runs."""
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise ValueError("simulation count must be a positive integer")
    if count > MAX_SIMULATION_COUNT:
        raise ValueError(f"simulation count cannot exceed {MAX_SIMULATION_COUNT}")
    return count


def run_simulations(config: SimulationConfig, count: int = 1):
    """Run a bounded batch, retaining each trajectory for future analysis."""
    validate_simulation_count(count)
    return [run_simulation(SimulationConfig(**{**config.__dict__, "seed": config.seed + index}))
            for index in range(count)]


def sample_size_message(count: int) -> str:
    """Return the interpretation guidance shown next to batch results."""
    validate_simulation_count(count)
    return LOW_SAMPLE_WARNING if count < LOW_SAMPLE_THRESHOLD else (
        f"Results are estimates based on {count} simulation runs; they are not "
        "confidence intervals or guaranteed tail probabilities."
    )


@dataclass
class ExpenseConfig:
    name: str
    expense_type: str
    monthly_amount: float


@dataclass
class DependentConfig:
    name: str
    monthly_cost: float
    age: int = 0
    school_start_age: int | None = 6
    school_monthly_cost: float = 250.0


@dataclass
class InvestmentConfig:
    name: str
    investment_type: str
    initial_amount: float
    monthly_contribution: float


@dataclass
class IncomeConfig:
    name: str
    income_type: str
    monthly_amount: float
    tax_rate: float = 0.0
    bonus_pct: float = 0.0


@dataclass
class LoanConfig:
    name: str
    amount: float
    interest_rate: float
    duration_years: int
    fixed_rate: bool = False
    already_paid: float = 0.0


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
    # None retains the backwards-compatible single-investment defaults;
    # an explicit empty list means the scenario has no investments.
    investments: list[InvestmentConfig] | None = None
    incomes: list[IncomeConfig] | None = None
    expenses: list[ExpenseConfig] | None = None
    loans: list[LoanConfig] | None = None


def build_components(config: SimulationConfig):
    """Create fresh model objects from UI/configuration values."""
    if config.incomes is None:
        income_specs = [IncomeConfig("Primary income", config.income_type,
                                     config.monthly_income, config.tax_rate, config.bonus_pct)]
    else:
        income_specs = config.incomes
    incomes = [IncomeSource(
        item.name, item.income_type, item.monthly_amount,
        specific_taxation=item.tax_rate or None, bonus_pct=item.bonus_pct or None,
    ) for item in income_specs]

    if config.expenses is None:
        expense_specs = [ExpenseConfig("Living expenses", config.expense_type,
                                       config.monthly_expense)]
    else:
        expense_specs = config.expenses
    expense_specs = [*expense_specs, *config.additional_expenses]
    expenses = [Expense(item.name, item.expense_type, item.monthly_amount)
                for item in expense_specs]
    # A dependent is a recurring cost in the model. Keeping this explicit makes
    # it easy to evolve into age-based childcare/education rules later.
    expenses.extend(
        AgeBasedExpense(
            f"{dependent.name} (child)", dependent.monthly_cost, dependent.age,
            {dependent.school_start_age: dependent.school_monthly_cost}
            if dependent.school_start_age is not None else {},
        )
        for dependent in config.dependents
    )
    if config.investments is None:
        investment_specs = [InvestmentConfig(
            "Portfolio", config.investment_type, config.investment_initial,
            config.investment_contribution,
        )]
    else:
        investment_specs = config.investments
    investments = [Investment(
        item.name, item.investment_type, item.initial_amount, item.monthly_contribution,
    ) for item in investment_specs]
    if config.loans is None:
        loan_specs = [LoanConfig("Loan", config.loan_amount, config.loan_rate,
                                 config.loan_duration, config.fixed_rate,
                                 config.loan_already_paid)]
    else:
        loan_specs = config.loans
    loans = [Loan(item.name, item.amount, item.interest_rate, item.duration_years,
                  item.fixed_rate, item.already_paid) for item in loan_specs]
    account = BankAccount(config.initial_cash, config.yearly_fee)
    return account, investments, loans, incomes, expenses


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
    st.session_state.setdefault("investments", [{
        "name": "Portfolio", "investment_type": "stock",
        "initial_amount": 1000.0, "monthly_contribution": 100.0,
    }])
    st.session_state.setdefault("incomes", [{
        "name": "Salary", "income_type": "salary", "monthly_amount": 3000.0,
        "tax_rate": 0.0, "bonus_pct": 0.0,
    }])
    st.session_state.setdefault("expenses", [{
        "name": "Living expenses", "expense_type": "housing", "monthly_amount": 1500.0,
    }])
    st.session_state.setdefault("loans", [{
        "name": "Home loan", "amount": 10000.0, "interest_rate": 2.0,
        "duration_years": 25, "fixed_rate": False, "already_paid": 0.0,
    }])
    for collection_name in ("investments", "incomes", "expenses", "loans", "dependents"):
        for item in st.session_state[collection_name]:
            item.setdefault("_id", uuid.uuid4().hex)

    def item_key(item):
        return item["_id"]

    def config_values(item):
        return {key: value for key, value in item.items() if key != "_id"}

    def card_is_open(collection, item):
        active = st.session_state.get("active_card")
        card_id = f"{collection}:{item_key(item)}"
        if st.button(
            f"{'▾' if active == card_id else '▸'} {item['name']}",
            key=f"toggle_{collection}_{item_key(item)}",
            use_container_width=True,
        ):
            st.session_state.active_card = None if active == card_id else card_id
            st.rerun()
        return st.session_state.get("active_card") == card_id

    def add_expense():
        st.session_state.expenses.append({
            "name": f"Expense {len(st.session_state.expenses) + 1}",
            "expense_type": "miscellaneous", "monthly_amount": 100.0,
            "_id": uuid.uuid4().hex,
        })

    def add_dependent():
        number = len(st.session_state.dependents) + 1
        st.session_state.dependents.append({
            "name": f"Child {number}", "monthly_cost": 300.0, "age": 0,
            "school_start_age": 6, "school_monthly_cost": 250.0,
            "_id": uuid.uuid4().hex,
        })

    def add_investment(investment_type):
        st.session_state.investments.append({
            "name": f"Investment {len(st.session_state.investments) + 1}",
            "investment_type": investment_type, "initial_amount": 1000.0,
            "monthly_contribution": 100.0,
            "_id": uuid.uuid4().hex,
        })

    def add_income(income_type):
        st.session_state.incomes.append({
            "name": f"Income {len(st.session_state.incomes) + 1}",
            "income_type": income_type, "monthly_amount": 2000.0,
            "tax_rate": 0.0, "bonus_pct": 0.0,
            "_id": uuid.uuid4().hex,
        })

    def add_loan():
        st.session_state.loans.append({
            "name": f"Loan {len(st.session_state.loans) + 1}", "amount": 10000.0,
            "interest_rate": 2.0, "duration_years": 25, "fixed_rate": False,
            "already_paid": 0.0,
            "_id": uuid.uuid4().hex,
        })

    def remove_item(collection, index):
        collection.pop(index)

    with st.sidebar:
        st.header("Scenario")
        years = st.slider("Simulation horizon (years)", 1, 50, 10)
        seed = st.number_input("Random seed", 0, 1_000_000, 42)
        simulation_count = st.slider(
            "Monte Carlo runs", 1, MAX_SIMULATION_COUNT, 1,
            help=SAMPLE_SIZE_HELP,
        )
        st.subheader("Cash")
        initial_cash = st.number_input("Starting cash", min_value=0.0, value=5000.0, step=500.0)
        yearly_fee = st.number_input("Annual bank fee", min_value=0.0, value=110.0, step=10.0)
        st.subheader("Investments")
        investment_types = sorted(Investment.TOPOLOGY_DETAILS)
        add_type = st.selectbox("Investment type to add", investment_types,
                                index=investment_types.index("stock"),
                                key="investment_type_to_add")
        if st.button("＋ Add investment", use_container_width=True):
            add_investment(add_type)
        for index, item in enumerate(st.session_state.investments):
            with st.container(border=True):
                if not card_is_open("investment", item):
                    st.caption(f"{item['investment_type']} · initial ${item['initial_amount']:,.0f} · monthly ${item['monthly_contribution']:,.0f}")
                    continue
                item["name"] = st.text_input("Name", item["name"], key=f"investment_name_{item_key(item)}")
                item["investment_type"] = st.selectbox(
                    "Type", investment_types,
                    index=investment_types.index(item["investment_type"]),
                    key=f"investment_type_{item_key(item)}",
                )
                item["initial_amount"] = st.number_input(
                    "Initial amount", min_value=0.0, value=float(item["initial_amount"]),
                    step=100.0, key=f"investment_initial_{item_key(item)}",
                )
                item["monthly_contribution"] = st.number_input(
                    "Monthly contribution", min_value=0.0,
                    value=float(item["monthly_contribution"]), step=50.0,
                    key=f"investment_contribution_{item_key(item)}",
                )
                if st.button("Remove investment", key=f"remove_investment_{item_key(item)}"):
                    remove_item(st.session_state.investments, index)
                    st.rerun()
        st.subheader("Income sources")
        income_types = sorted(IncomeSource("x", "salary", 1).income_growth)
        income_to_add = st.selectbox("Income type to add", income_types, key="income_type_to_add")
        if st.button("＋ Add income", use_container_width=True):
            add_income(income_to_add)
        for index, item in enumerate(st.session_state.incomes):
            with st.container(border=True):
                if not card_is_open("income", item):
                    st.caption(f"{item['income_type']} · ${item['monthly_amount']:,.0f}/month")
                    continue
                item["name"] = st.text_input("Name", item["name"], key=f"income_name_{item_key(item)}")
                item["income_type"] = st.selectbox("Type", income_types,
                    index=income_types.index(item["income_type"]), key=f"income_type_{item_key(item)}")
                item["monthly_amount"] = st.number_input("Monthly amount", min_value=0.0,
                    value=float(item["monthly_amount"]), step=100.0, key=f"income_amount_{item_key(item)}")
                item["tax_rate"] = st.number_input("Custom tax rate", 0.0, 100.0,
                    value=float(item["tax_rate"]), key=f"income_tax_{item_key(item)}")
                item["bonus_pct"] = st.number_input("Bonus cap (%)", 0.0, 100.0,
                    value=float(item["bonus_pct"]), key=f"income_bonus_{item_key(item)}")
                if st.button("Remove income", key=f"remove_income_{item_key(item)}"):
                    remove_item(st.session_state.incomes, index)
                    st.rerun()

        st.subheader("Expenses")
        expense_types = sorted(Expense("x", "housing", 1).expense_growth)
        expense_to_add = st.selectbox("Expense type to add", expense_types, key="expense_type_to_add")
        if st.button("＋ Add expense", use_container_width=True):
            add_expense()
        for index, item in enumerate(st.session_state.expenses):
            with st.container(border=True):
                if not card_is_open("expense", item):
                    st.caption(f"{item['expense_type']} · ${item['monthly_amount']:,.0f}/month")
                    continue
                item["name"] = st.text_input("Name", item["name"], key=f"expense_name_{item_key(item)}")
                item["expense_type"] = st.selectbox("Type", expense_types,
                    index=expense_types.index(item["expense_type"]), key=f"expense_type_{item_key(item)}")
                item["monthly_amount"] = st.number_input("Monthly amount", min_value=0.0,
                    value=float(item["monthly_amount"]), step=25.0, key=f"expense_amount_{item_key(item)}")
                if st.button("Remove expense", key=f"remove_expense_{item_key(item)}"):
                    remove_item(st.session_state.expenses, index)
                    st.rerun()

        st.subheader("Loans")
        if st.button("＋ Add loan", use_container_width=True):
            add_loan()
        for index, item in enumerate(st.session_state.loans):
            with st.container(border=True):
                if not card_is_open("loan", item):
                    st.caption(f"${item['amount']:,.0f} · {item['interest_rate']:.2f}% · {item['duration_years']} years")
                    continue
                item["name"] = st.text_input("Name", item["name"], key=f"loan_name_{item_key(item)}")
                item["amount"] = st.number_input("Amount", min_value=0.0,
                    value=float(item["amount"]), step=1000.0, key=f"loan_amount_{item_key(item)}")
                item["interest_rate"] = st.number_input("Interest rate (%)", min_value=0.0,
                    value=float(item["interest_rate"]), step=0.1, key=f"loan_rate_{item_key(item)}")
                item["duration_years"] = st.number_input("Duration (years)", min_value=1,
                    value=int(item["duration_years"]), key=f"loan_duration_{item_key(item)}")
                item["fixed_rate"] = st.checkbox("Fixed rate", value=item["fixed_rate"], key=f"loan_fixed_{item_key(item)}")
                item["already_paid"] = st.number_input("Already paid", min_value=0.0,
                    value=float(item["already_paid"]), step=100.0, key=f"loan_paid_{item_key(item)}")
                if st.button("Remove loan", key=f"remove_loan_{item_key(item)}"):
                    remove_item(st.session_state.loans, index)
                    st.rerun()

        st.subheader("Current situation")
        st.caption("Add the people and recurring costs that shape this scenario.")
        if st.button("＋ Add daughter / child", use_container_width=True):
            add_dependent()
        for index, item in enumerate(st.session_state.dependents):
            with st.container(border=True):
                if not card_is_open("dependent", item):
                    st.caption(f"age {item.get('age', 0)} · ${item['monthly_cost']:,.0f}/month")
                    continue
                item["name"] = st.text_input("Name", item["name"], key=f"dependent_name_{item_key(item)}")
                item["age"] = st.number_input("Current age", min_value=0, max_value=25,
                    value=int(item.get("age", 0)), key=f"dependent_age_{item_key(item)}")
                item["monthly_cost"] = st.number_input(
                    "Monthly child cost", min_value=0.0, value=float(item["monthly_cost"]),
                    step=25.0, key=f"dependent_cost_{item_key(item)}",
                )
                automatic_costs = st.checkbox(
                    "Add school cost from age 6*", value=item.get("school_start_age") is not None,
                    key=f"dependent_school_toggle_{item_key(item)}",
                )
                item["school_start_age"] = 6 if automatic_costs else None
                item["school_monthly_cost"] = st.number_input(
                    "School monthly cost*", min_value=0.0,
                    value=float(item.get("school_monthly_cost", 250.0)), step=25.0,
                    key=f"dependent_school_cost_{item_key(item)}", disabled=not automatic_costs,
                )
                st.caption("* Illustrative assumption; adjust or disable for your situation.")
                if st.button("Remove child", key=f"remove_dependent_{item_key(item)}"):
                    remove_item(st.session_state.dependents, index)
                    st.rerun()

        if st.session_state.expenses or st.session_state.dependents:
            st.caption(
                f"{len(st.session_state.expenses)} expense(s) · "
                f"{len(st.session_state.dependents)} dependent(s)"
            )

    config = SimulationConfig(
        years=years, seed=int(seed), initial_cash=initial_cash, yearly_fee=yearly_fee,
        investment_type="stock", investment_initial=0.0, investment_contribution=0.0,
        investments=[InvestmentConfig(**config_values(item)) for item in st.session_state.investments],
        incomes=[IncomeConfig(**config_values(item)) for item in st.session_state.incomes],
        expenses=[ExpenseConfig(**config_values(item)) for item in st.session_state.expenses],
        loans=[LoanConfig(**config_values(item)) for item in st.session_state.loans],
        additional_expenses=[ExpenseConfig(**item) for item in st.session_state.additional_expenses],
        dependents=[DependentConfig(**config_values(item)) for item in st.session_state.dependents],
    )
    try:
        simulations = run_simulations(config, simulation_count)
        net_worth = [sum(result[0][month] for result in simulations) / simulation_count
                     for month in range(len(simulations[0][0]))]
        repayment_dates = simulations[0][1]
        details = {
            key: [sum(result[2][key][month] for result in simulations) / simulation_count
                  for month in range(len(simulations[0][2][key]))]
            for key in simulations[0][2]
        }
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
    st.info(f"Monte Carlo overview — {simulation_count} run(s). {sample_size_message(simulation_count)}")

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
    st.caption("Illustrative projection only — this is not financial advice. *Age-based family costs are configurable assumptions, not validated rules.")


if __name__ == "__main__":
    main()