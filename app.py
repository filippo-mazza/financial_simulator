"""Streamlit interface for the financial simulator.

The model-building functions are intentionally independent of Streamlit so they
can be exercised in unit tests and reused by another front end later.
"""

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
import json
import random
from statistics import fmean
import uuid

from bank_account import BankAccount, InsufficientFundsException
from expense import AgeBasedExpense, Expense
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


def run_simulations(config: SimulationConfig, simulation_count: int):
    """Run distinct reproducible scenarios and average their monthly results."""
    if simulation_count < 1:
        raise ValueError("simulation_count must be at least 1")

    runs = [
        run_simulation(replace(config, seed=config.seed + run_number))
        for run_number in range(simulation_count)
    ]
    average_net_worth = [fmean(month) for month in zip(*(run[0] for run in runs))]
    average_details = {
        detail_name: [
            fmean(month) for month in zip(*(run[2][detail_name] for run in runs))
        ]
        for detail_name in ("bank_account", "investments")
    }
    # Loan schedules do not depend on the stochastic investment/income paths.
    repayment_dates = runs[0][1]
    return average_net_worth, repayment_dates, average_details


def _money(value):
    return f"${value:,.0f}"


def _whole_amount(value):
    """Discard cents from large UI amounts where that precision is not useful."""
    return int(value)


def build_simulation_events(config, repayment_dates, months_simulated):
    """Return chart events with human-friendly, one-based month numbers."""
    events = [
        {
            "month": month_index + 1,
            "label": f"{name} repaid",
            "event_type": "Loan repaid",
        }
        for name, month_index in repayment_dates.items()
        if month_index < months_simulated
    ]
    for dependent in config.dependents:
        if (dependent.school_start_age is None
                or dependent.age >= dependent.school_start_age):
            continue
        # AgeBasedExpense changes age after each twelfth month, so the new
        # school cost first affects the following month.
        month = (dependent.school_start_age - dependent.age) * 12 + 1
        if month <= months_simulated:
            events.append({
                "month": month,
                "label": f"School starts for {dependent.name}",
                "event_type": "School starts",
            })
    return sorted(events, key=lambda event: (event["month"], event["label"]))


def create_simulation_export(config, net_worth, repayment_dates, details,
                             exported_at=None, simulation_count=1):
    """Return a JSON export containing reproducible inputs and monthly results."""
    exported_at = exported_at or datetime.now(timezone.utc)
    monthly_results = [
        {
            "month": index + 1,
            "net_worth": value,
            "cash": details["bank_account"][index],
            "investments": details["investments"][index],
        }
        for index, value in enumerate(net_worth)
    ]
    export = {
        "format": "financial-simulator-export",
        "schema_version": 1,
        "exported_at_utc": exported_at.astimezone(timezone.utc).isoformat(),
        "parameters": asdict(config),
        "results": {
            "simulation_count": simulation_count,
            "summary": {
                "months_simulated": len(net_worth),
                "final_net_worth": net_worth[-1],
                "final_cash": details["bank_account"][-1],
                "final_investments": details["investments"][-1],
                "loans_repaid": len(repayment_dates),
            },
            "monthly": monthly_results,
            "loan_repayments": [
                {"name": name, "month": month_index + 1}
                for name, month_index in repayment_dates.items()
            ],
            "events": build_simulation_events(config, repayment_dates, len(net_worth)),
        },
    }
    return json.dumps(export, indent=2, ensure_ascii=False)


def read_simulation_export(data):
    """Validate an exported JSON document and rebuild its simulation config."""
    try:
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        document = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as error:
        raise ValueError("The selected file is not valid UTF-8 JSON.") from error

    if not isinstance(document, dict) or document.get("format") != "financial-simulator-export":
        raise ValueError("This is not a Financial Simulator export.")
    if document.get("schema_version") != 1:
        raise ValueError("This export version is not supported.")
    parameters = document.get("parameters")
    if not isinstance(parameters, dict):
        raise ValueError("The export does not contain simulation parameters.")

    parameters = dict(parameters)

    def rebuild_collection(name, config_type):
        items = parameters.get(name)
        if items is None:
            return None
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise ValueError(f"The exported {name} must be a list.")
        try:
            return [config_type(**item) for item in items]
        except TypeError as error:
            raise ValueError(f"The exported {name} are invalid: {error}") from error

    parameters["investments"] = rebuild_collection("investments", InvestmentConfig)
    parameters["incomes"] = rebuild_collection("incomes", IncomeConfig)
    parameters["expenses"] = rebuild_collection("expenses", ExpenseConfig)
    parameters["loans"] = rebuild_collection("loans", LoanConfig)
    parameters["additional_expenses"] = (
        rebuild_collection("additional_expenses", ExpenseConfig) or []
    )
    parameters["dependents"] = rebuild_collection("dependents", DependentConfig) or []
    try:
        return SimulationConfig(**parameters)
    except TypeError as error:
        raise ValueError(f"The exported parameters are invalid: {error}") from error


def main():
    import altair as alt
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

    def editable_items(items, money_fields=()):
        editable = []
        for item in items:
            values = asdict(item)
            for field_name in money_fields:
                values[field_name] = float(_whole_amount(values[field_name]))
            editable.append({**values, "_id": uuid.uuid4().hex})
        return editable

    def import_config(imported):
        st.session_state.scenario_years = imported.years
        st.session_state.scenario_initial_cash = float(_whole_amount(imported.initial_cash))
        st.session_state.scenario_yearly_fee = float(_whole_amount(imported.yearly_fee))
        st.session_state.investments = editable_items(
            imported.investments if imported.investments is not None else [
                InvestmentConfig("Portfolio", imported.investment_type,
                                 imported.investment_initial, imported.investment_contribution)
            ], ("initial_amount", "monthly_contribution")
        )
        st.session_state.incomes = editable_items(
            imported.incomes if imported.incomes is not None else [
                IncomeConfig("Primary income", imported.income_type, imported.monthly_income,
                             imported.tax_rate, imported.bonus_pct)
            ], ("monthly_amount",)
        )
        st.session_state.expenses = editable_items(
            imported.expenses if imported.expenses is not None else [
                ExpenseConfig("Living expenses", imported.expense_type, imported.monthly_expense)
            ], ("monthly_amount",)
        )
        st.session_state.loans = editable_items(
            imported.loans if imported.loans is not None else [
                LoanConfig("Loan", imported.loan_amount, imported.loan_rate,
                           imported.loan_duration, imported.fixed_rate,
                           imported.loan_already_paid)
            ], ("amount", "already_paid")
        )
        st.session_state.additional_expenses = [
            {**asdict(item), "monthly_amount": float(_whole_amount(item.monthly_amount))}
            for item in imported.additional_expenses
        ]
        st.session_state.dependents = editable_items(
            imported.dependents, ("monthly_cost", "school_monthly_cost")
        )
        st.session_state.active_card = None
        st.session_state.pop("simulation_result", None)

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
        with st.expander("Import saved scenario"):
            uploaded_export = st.file_uploader(
                "Financial Simulator JSON", type="json",
                help="Select a JSON file created by the export button.",
            )
            if st.button(
                "Import parameters", disabled=uploaded_export is None,
                use_container_width=True,
            ):
                try:
                    import_config(read_simulation_export(uploaded_export.getvalue()))
                except ValueError as error:
                    st.error(str(error))
                else:
                    st.success("Scenario imported.")
                    st.rerun()

        years = st.slider(
            "Simulation horizon (years)", 1, 50,
            **({} if "scenario_years" in st.session_state else {"value": 10}),
            key="scenario_years",
        )
        st.subheader("Cash")
        initial_cash = st.number_input(
            "Starting cash", min_value=0.0, step=500.0, format="%.0f",
            **({} if "scenario_initial_cash" in st.session_state else {"value": 5000.0}),
            key="scenario_initial_cash",
        )
        yearly_fee = st.number_input(
            "Annual bank fee", min_value=0.0, step=10.0, format="%.0f",
            **({} if "scenario_yearly_fee" in st.session_state else {"value": 110.0}),
            key="scenario_yearly_fee",
        )
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
                    step=100.0, format="%.0f", key=f"investment_initial_{item_key(item)}",
                )
                item["monthly_contribution"] = st.number_input(
                    "Monthly contribution", min_value=0.0,
                    value=float(item["monthly_contribution"]), step=50.0, format="%.0f",
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
                    value=float(item["monthly_amount"]), step=100.0, format="%.0f",
                    key=f"income_amount_{item_key(item)}")
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
                    value=float(item["monthly_amount"]), step=25.0, format="%.0f",
                    key=f"expense_amount_{item_key(item)}")
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
                    value=float(item["amount"]), step=1000.0, format="%.0f",
                    key=f"loan_amount_{item_key(item)}")
                item["interest_rate"] = st.number_input("Interest rate (%)", min_value=0.0,
                    value=float(item["interest_rate"]), step=0.1, key=f"loan_rate_{item_key(item)}")
                item["duration_years"] = st.number_input("Duration (years)", min_value=1,
                    value=int(item["duration_years"]), key=f"loan_duration_{item_key(item)}")
                item["fixed_rate"] = st.checkbox("Fixed rate", value=item["fixed_rate"], key=f"loan_fixed_{item_key(item)}")
                item["already_paid"] = st.number_input("Already paid", min_value=0.0,
                    value=float(item["already_paid"]), step=100.0, format="%.0f",
                    key=f"loan_paid_{item_key(item)}")
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
                    step=25.0, format="%.0f", key=f"dependent_cost_{item_key(item)}",
                )
                automatic_costs = st.checkbox(
                    "Add school cost from age 6*", value=item.get("school_start_age") is not None,
                    key=f"dependent_school_toggle_{item_key(item)}",
                )
                item["school_start_age"] = 6 if automatic_costs else None
                item["school_monthly_cost"] = st.number_input(
                    "School monthly cost*", min_value=0.0,
                    value=float(item.get("school_monthly_cost", 250.0)), step=25.0, format="%.0f",
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

        st.divider()
        st.subheader("Run projection")
        simulation_count = st.number_input(
            "Number of simulations", min_value=1, max_value=500, value=25, step=1,
            help="More runs produce a smoother average but take longer.",
        )
        run_requested = st.button(
            "▶ Run simulations", type="primary", use_container_width=True,
        )

    config = SimulationConfig(
        years=years, seed=42, initial_cash=_whole_amount(initial_cash),
        yearly_fee=_whole_amount(yearly_fee),
        investment_type="stock", investment_initial=0.0, investment_contribution=0.0,
        investments=[InvestmentConfig(
            item["name"], item["investment_type"], _whole_amount(item["initial_amount"]),
            _whole_amount(item["monthly_contribution"]),
        ) for item in st.session_state.investments],
        incomes=[IncomeConfig(
            item["name"], item["income_type"], _whole_amount(item["monthly_amount"]),
            item["tax_rate"], item["bonus_pct"],
        ) for item in st.session_state.incomes],
        expenses=[ExpenseConfig(
            item["name"], item["expense_type"], _whole_amount(item["monthly_amount"]),
        ) for item in st.session_state.expenses],
        loans=[LoanConfig(
            item["name"], _whole_amount(item["amount"]), item["interest_rate"],
            item["duration_years"], item["fixed_rate"], _whole_amount(item["already_paid"]),
        ) for item in st.session_state.loans],
        additional_expenses=[ExpenseConfig(
            item["name"], item["expense_type"], _whole_amount(item["monthly_amount"]),
        ) for item in st.session_state.additional_expenses],
        dependents=[DependentConfig(
            item["name"], _whole_amount(item["monthly_cost"]), item["age"],
            item["school_start_age"], _whole_amount(item["school_monthly_cost"]),
        ) for item in st.session_state.dependents],
    )

    scenario_fingerprint = json.dumps(asdict(config), sort_keys=True)
    if run_requested:
        try:
            with st.spinner(f"Running {simulation_count} simulations..."):
                result = run_simulations(config, int(simulation_count))
        except (ValueError, TypeError, KeyError, InsufficientFundsException) as error:
            st.session_state.pop("simulation_result", None)
            st.error(f"This scenario cannot be simulated: {error}")
            return
        st.session_state.simulation_result = {
            "fingerprint": scenario_fingerprint,
            "simulation_count": int(simulation_count),
            "result": result,
        }

    stored_result = st.session_state.get("simulation_result")
    if stored_result is None:
        st.info("Choose the number of simulations and click **Run simulations**.")
        return
    if (stored_result["fingerprint"] != scenario_fingerprint
            or stored_result["simulation_count"] != int(simulation_count)):
        st.warning("The scenario or run count changed. Click **Run simulations** to refresh.")
        return

    net_worth, repayment_dates, details = stored_result["result"]

    final_net_worth = net_worth[-1]
    starting_net_worth = net_worth[0]
    columns = st.columns(4)
    columns[0].metric("Average final net worth", _money(final_net_worth), f"{final_net_worth - starting_net_worth:+,.0f}")
    columns[1].metric("Average final cash", _money(details["bank_account"][-1]))
    columns[2].metric("Average final investments", _money(details["investments"][-1]))
    columns[3].metric("Loans repaid", len(repayment_dates))

    st.download_button(
        "⬇️ Export parameters and results",
        data=create_simulation_export(
            config, net_worth, repayment_dates, details,
            simulation_count=int(simulation_count),
        ),
        file_name=f"financial-simulation-{config.years}y-{simulation_count}-runs.json",
        mime="application/json",
        help="Download the scenario inputs and month-by-month results as JSON.",
    )

    chart_values = []
    for month, (worth, cash, investments) in enumerate(zip(
            net_worth, details["bank_account"], details["investments"]), start=1):
        chart_values.extend([
            {"month": month, "amount": worth, "series": "Average net worth"},
            {"month": month, "amount": cash, "series": "Average cash"},
            {"month": month, "amount": investments, "series": "Average investments"},
        ])

    lines = alt.Chart(alt.Data(values=chart_values)).mark_line(strokeWidth=2.5).encode(
        x=alt.X("month:Q", title="Month", scale=alt.Scale(domain=[1, len(net_worth)])),
        y=alt.Y("amount:Q", title="Amount", axis=alt.Axis(format="$,.0f")),
        color=alt.Color(
            "series:N", title="Financial series",
            scale=alt.Scale(
                domain=["Average net worth", "Average cash", "Average investments"],
                range=["#35c99a", "#5b8def", "#f4b942"],
            ),
            legend=alt.Legend(orient="top", direction="horizontal"),
        ),
        tooltip=[
            alt.Tooltip("month:Q", title="Month", format=".0f"),
            alt.Tooltip("series:N", title="Series"),
            alt.Tooltip("amount:Q", title="Amount", format="$,.0f"),
        ],
    )

    events = build_simulation_events(config, repayment_dates, len(net_worth))
    chart = lines
    if events:
        label_height = max(
            max(net_worth), max(details["bank_account"]), max(details["investments"])
        )
        event_values = [{**event, "label_height": label_height} for event in events]
        event_data = alt.Data(values=event_values)
        event_colors = alt.Scale(
            domain=["Loan repaid", "School starts"],
            range=["#e06c75", "#b7791f"],
        )
        rules = alt.Chart(event_data).mark_rule(
            strokeDash=[7, 5], strokeWidth=2, opacity=0.85,
        ).encode(
            x=alt.X("month:Q"),
            color=alt.Color(
                "event_type:N", title="Events", scale=event_colors,
                legend=alt.Legend(orient="top", direction="horizontal"),
            ),
            tooltip=[
                alt.Tooltip("label:N", title="Event"),
                alt.Tooltip("month:Q", title="Month", format=".0f"),
            ],
        )
        labels = alt.Chart(event_data).mark_text(
            angle=270, align="right", baseline="middle", dx=-8, fontSize=11,
        ).encode(
            x=alt.X("month:Q"),
            y=alt.Y("label_height:Q"),
            text=alt.Text("label:N"),
            color=alt.Color("event_type:N", scale=event_colors, legend=None),
        )
        chart = (lines + rules + labels).resolve_scale(color="independent")

    chart = chart.properties(
        height=460,
        title=f"Average projected financial position ({simulation_count} simulations)",
    ).interactive()
    st.altair_chart(chart, use_container_width=True)
    if events:
        st.caption("Events: " + " · ".join(
            f"month {event['month']}: {event['label']}" for event in events
        ))
    st.caption("Illustrative projection only — this is not financial advice. *Age-based family costs are configurable assumptions, not validated rules.")


if __name__ == "__main__":
    main()
