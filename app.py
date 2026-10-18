"""Streamlit interface for the financial simulator.

The model-building functions are intentionally independent of Streamlit so they
can be exercised in unit tests and reused by another front end later.
"""

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
import inspect
import json
import random
from statistics import fmean
import uuid

from bank_account import BankAccount, InsufficientFundsException
from expense import AgeBasedExpense, Expense, YearlyExpense
from financial_assumptions import FinancialAssumptions, INCOME_TYPES, INVESTMENT_TYPES
from income_source import IncomeSource
from investment import Investment
from loan import Loan
from utils import compute_net_worth_over_time
from property_purchase import PropertyPurchase, AutomaticPurchasePolicy

AUTO_INVEST_EXPORT_VERSION = 1
FINANCIAL_ASSUMPTIONS_EXPORT_VERSION = 1
MONTE_CARLO_TRAJECTORIES_EXPORT_VERSION = 1
SIMULATION_DETAIL_VERSION = 3
MAX_SIMULATION_COUNT = 25
LOW_SAMPLE_THRESHOLD = 10
AUTO_INVEST_TYPES = frozenset({"stock", "safe"})
LEGACY_AUTO_INVEST_WARNING = (
    "This export predates or uses an unsupported version of configurable "
    "automatic investment contributions. "
    "Automatic contributions were disabled for safety; review each stock or "
    "safe investment before running the imported scenario."
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
    monthly_expenses: float = 0.0
    yearly_tax: float = 0.0
    auto_invest_enabled: bool = False
    auto_invest_min_cash: float = 10000.0
    auto_invest_percentage: float = 10.0
    auto_invest_max_per_year: int = 3


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
    assumptions: FinancialAssumptions = field(default_factory=FinancialAssumptions)
    # Opt-in; purchase UI and savings-trigger policy are intentionally separate
    # follow-up features.  The asset is recorded at price and is 100% owned.
    property_purchase: PropertyPurchase | AutomaticPurchasePolicy | None = None


@dataclass(frozen=True)
class SimulationTrajectory:
    """One reproducible Monte Carlo path and its effective seed."""
    run_number: int
    seed: int
    net_worth: list[float]
    repayment_dates: dict[str, int]
    details: dict

    @property
    def result(self):
        return self.net_worth, self.repayment_dates, self.details


@dataclass(frozen=True)
class SimulationBatch:
    """A Monte Carlo average together with the individual paths behind it."""
    average_result: tuple
    trajectories: list[SimulationTrajectory]


def build_components(config: SimulationConfig):
    """Create fresh model objects from UI/configuration values."""
    if config.incomes is None:
        income_specs = [IncomeConfig("Primary income", config.income_type,
                                     config.monthly_income, config.tax_rate, config.bonus_pct)]
    else:
        income_specs = config.incomes
    incomes = []
    for item in income_specs:
        taxation = (
            item.tax_rate
            if item.tax_rate else config.assumptions.income_tax_rates[item.income_type]
        )
        if not 0 <= taxation <= 100:
            raise ValueError("Taxation must be between 0 and 100")
        # Assign after construction so a cached pre-feature IncomeSource accepts
        # configured zero rates instead of applying its former 5% lower bound.
        income = IncomeSource(
            item.name, item.income_type, item.monthly_amount,
            specific_taxation=None, bonus_pct=item.bonus_pct or None,
        )
        income.specific_taxation = taxation
        incomes.append(income)

    if config.investments is None:
        investment_specs = [InvestmentConfig(
            "Portfolio", config.investment_type, config.investment_initial,
            0 if config.investment_type == "real_estate" else config.investment_contribution,
            config.investment_contribution if config.investment_type == "real_estate" else 0,
        )]
    else:
        investment_specs = config.investments

    if config.expenses is None:
        expense_specs = [ExpenseConfig("Living expenses", config.expense_type,
                                       config.monthly_expense)]
    else:
        expense_specs = config.expenses
    expense_specs = [*expense_specs, *config.additional_expenses]
    expenses = [Expense(item.name, item.expense_type, item.monthly_amount)
                for item in expense_specs]
    for investment in investment_specs:
        if investment.investment_type != "real_estate":
            continue
        if investment.monthly_expenses > 0:
            expenses.append(Expense(
                f"{investment.name} — property expenses (linked)",
                "housing", investment.monthly_expenses,
            ))
        if investment.yearly_tax > 0:
            expenses.append(YearlyExpense(
                f"{investment.name} — property tax (linked)",
                investment.yearly_tax,
            ))
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
    investments = []
    for item in investment_specs:
        if item.auto_invest_enabled and item.investment_type not in AUTO_INVEST_TYPES:
            raise ValueError(
                "Automatic contributions are only supported for stock and safe investments"
            )
        if item.auto_invest_min_cash < 0:
            raise ValueError("Automatic contribution minimum cash cannot be negative")
        if not 0 < item.auto_invest_percentage <= 100:
            raise ValueError("Automatic contribution percentage must be between 0 and 100")
        if item.auto_invest_max_per_year < 1:
            raise ValueError("Automatic contribution maximum per year must be positive")
        investment_args = (
            item.name, item.investment_type, item.initial_amount,
            0 if item.investment_type == "real_estate" else item.monthly_contribution,
        )
        if "annual_return_mean" in inspect.signature(Investment).parameters:
            investment = Investment(
                *investment_args,
                annual_return_mean=(
                    config.assumptions.investment_return_means[item.investment_type]
                ),
                annual_return_std_dev=(
                    config.assumptions.investment_return_std_devs[item.investment_type]
                ),
                dividend_rate=config.assumptions.stock_dividend_rate,
                contribution_fee=config.assumptions.contribution_fee,
            )
        else:
            # A Streamlit hot reload can retain the old class. Its methods read
            # class constants, so configure a per-scenario subclass rather than
            # assigning ineffective instance attributes.
            typology_details = {
                name: dict(details)
                for name, details in getattr(Investment, "TOPOLOGY_DETAILS", {}).items()
            }
            typology_details[item.investment_type] = {
                "mean": config.assumptions.investment_return_means[item.investment_type],
                "std_dev": (
                    config.assumptions.investment_return_std_devs[item.investment_type]
                ),
            }
            configured_type = type(
                "ConfiguredLegacyInvestment",
                (Investment,),
                {
                    "TOPOLOGY_DETAILS": typology_details,
                    "DIVIDEND": config.assumptions.stock_dividend_rate,
                    "CONTRIBUTION_FEE": config.assumptions.contribution_fee,
                    "_random_annual_gain_percentage": lambda self: max(
                        -100.0,
                        random.gauss(
                            self.TOPOLOGY_DETAILS[self.typology]["mean"],
                            self.TOPOLOGY_DETAILS[self.typology]["std_dev"],
                        ),
                    ),
                },
            )
            investment = configured_type(*investment_args)
        investment.annual_return_mean = (
            config.assumptions.investment_return_means[item.investment_type]
        )
        investment.annual_return_std_dev = (
            config.assumptions.investment_return_std_devs[item.investment_type]
        )
        investment.dividend_rate = config.assumptions.stock_dividend_rate
        investment.contribution_fee = config.assumptions.contribution_fee
        # Set policy attributes after construction so a Streamlit hot reload
        # remains compatible with an Investment class cached from older code.
        investment.auto_invest_enabled = item.auto_invest_enabled
        investment.auto_invest_min_cash = item.auto_invest_min_cash
        investment.auto_invest_percentage = item.auto_invest_percentage
        investment.auto_invest_max_per_year = item.auto_invest_max_per_year
        investments.append(investment)
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
        return compute_net_worth_over_time(
            config.years, *components, seed=config.seed,
            property_purchase=config.property_purchase,
            assumptions=config.assumptions,
        )
    finally:
        random.setstate(random_state)


def run_simulation_batch(config: SimulationConfig, simulation_count: int):
    """Run and retain distinct reproducible scenarios plus their average."""
    if simulation_count < 1:
        raise ValueError("simulation_count must be at least 1")
    if simulation_count > MAX_SIMULATION_COUNT:
        raise ValueError(f"simulation_count cannot exceed {MAX_SIMULATION_COUNT}")

    trajectories = []
    for run_index in range(simulation_count):
        result = run_simulation(replace(config, seed=config.seed + run_index))
        trajectories.append(SimulationTrajectory(
            run_number=run_index + 1,
            seed=config.seed + run_index,
            net_worth=result[0],
            repayment_dates=result[1],
            details=result[2],
        ))
    average_net_worth = [fmean(month) for month in zip(*(
        trajectory.net_worth for trajectory in trajectories
    ))]
    average_details = {
        detail_name: [
            fmean(month) for month in zip(*(
                trajectory.details[detail_name] for trajectory in trajectories
            ))
        ]
        for detail_name in ("bank_account", "investments")
    }
    for detail_name in (
        "investment_values", "automatic_contributions",
        "automatic_contribution_fees", "income_rates", "income_values",
        "expense_values", "loan_balances", "loan_payments",
    ):
        item_names = set().union(*(
            trajectory.details[detail_name] for trajectory in trajectories
        ))
        average_details[detail_name] = {
            item_name: [
                fmean(month)
                for month in zip(*(
                    trajectory.details[detail_name].get(
                        item_name, [0] * len(trajectory.net_worth)
                    )
                    for trajectory in trajectories
                ))
            ]
            for item_name in item_names
        }
    if "purchase_fees" in trajectories[0].details:
        average_details["purchase_fees"] = [
            fmean(month) for month in zip(*(
                trajectory.details["purchase_fees"] for trajectory in trajectories
            ))
        ]
        average_details["purchase_events"] = trajectories[0].details["purchase_events"]
    if "purchase_outcome" in trajectories[0].details:
        average_details["purchase_outcome"] = {
            "purchased": any(
                trajectory.details["purchase_outcome"]["purchased"]
                for trajectory in trajectories
            ),
            "trajectories": [
                trajectory.details["purchase_outcome"] for trajectory in trajectories
            ],
        }
    # Loan schedules do not depend on the stochastic investment/income paths.
    average_result = average_net_worth, trajectories[0].repayment_dates, average_details
    return SimulationBatch(average_result, trajectories)


def run_simulations(config: SimulationConfig, simulation_count: int):
    """Return monthly averages while preserving the original public contract."""
    return run_simulation_batch(config, simulation_count).average_result


def _money(value):
    return f"${value:,.0f}"


def _whole_amount(value):
    """Discard cents from large UI amounts where that precision is not useful."""
    return int(value)


def monte_carlo_view_explanation(view_mode, simulation_count):
    """Return plain-language guidance for interpreting Monte Carlo views."""
    if view_mode == "Overview":
        if simulation_count == 1:
            return (
                "**Single-run overview:** with one simulation, the arithmetic average "
                "is the same as Run 1 and does not hide differences between runs. It is "
                "still a simulated trajectory, not a guaranteed forecast. Run multiple "
                "simulations to compare varied outcomes."
            )
        return (
            f"**Arithmetic-average overview:** each month is the average across "
            f"{simulation_count} simulated runs. These are estimates based on the "
            f"selected run count, not a guaranteed "
            "or individually realizable household path. Average cash and net worth can "
            "hide liquidity shortfalls in individual runs. Quantitative downside metrics "
            "and uncertainty shading are tracked in "
            "[issue #25](https://github.com/filippo-mazza/financial_simulator/issues/25) "
            "and [issue #67](https://github.com/filippo-mazza/financial_simulator/issues/67)."
        )
    return (
        "**Individual run:** this is one simulated trajectory rather than an average. "
        "Switch runs to inspect outcomes, including shortfalls the Overview may hide."
    )


def monte_carlo_sample_guidance(simulation_count):
    """Explain the limits of estimates derived from the selected run count."""
    estimate = (
        f"Frequencies and other summary values are estimates based on the selected "
        f"{simulation_count} simulation(s)."
    )
    if simulation_count < LOW_SAMPLE_THRESHOLD:
        return (
            f"{estimate} With fewer than {LOW_SAMPLE_THRESHOLD} runs, the sample is "
            "especially small: do not treat it as reliable evidence for tail "
            "probabilities, percentiles, or confidence claims."
        )
    return estimate


def _consume_run_request(session_state, button_clicked):
    """Combine a manual run click with the one-shot post-import run request."""
    run_after_import = bool(session_state.pop("run_after_import", False))
    return bool(button_clicked or run_after_import)


def build_simulation_events(config, repayment_dates, months_simulated,
                            purchase_events=None):
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
    if purchase_events is None and isinstance(config.property_purchase, PropertyPurchase):
        purchase_events = [{
            "month": config.property_purchase.purchase_month,
            "property_name": config.property_purchase.property_name,
        }]
    for purchase in purchase_events or []:
        if purchase["month"] < months_simulated:
            events.append({
                "month": purchase["month"] + 1,
                "label": f"{purchase['property_name']} purchased",
                "event_type": "Property purchase",
            })
    return sorted(events, key=lambda event: (event["month"], event["label"]))


def get_month_details(net_worth, details, month):
    """Build balance and flow rows for a one-based simulation month."""
    if month < 1 or month > len(net_worth):
        raise ValueError("month is outside the simulation range")
    index = month - 1
    balances = [
        {"Category": "Overview", "Item": "Net worth", "Amount": net_worth[index]},
        {"Category": "Overview", "Item": "Cash", "Amount": details["bank_account"][index]},
        {"Category": "Overview", "Item": "Investments total",
         "Amount": details["investments"][index]},
    ]
    balances.extend(
        {"Category": "Investment", "Item": name, "Amount": values[index]}
        for name, values in details["investment_values"].items()
    )
    balances.extend(
        {"Category": "Loan balance", "Item": name, "Amount": values[index]}
        for name, values in details["loan_balances"].items()
    )

    flows = []
    for category, detail_name in (
        ("Gross monthly income rate", "income_rates"),
        ("Income received", "income_values"),
        ("Automatic investment contribution", "automatic_contributions"),
        ("Automatic contribution fee", "automatic_contribution_fees"),
        ("Expense paid", "expense_values"),
        ("Loan payment", "loan_payments"),
    ):
        flows.extend(
            {"Category": category, "Item": name, "Amount": values[index]}
            for name, values in details[detail_name].items()
        )
    return balances, flows


def _monthly_results(net_worth, details):
    """Serialize the complete month-by-month accounting result."""
    return [
        {
            "month": index + 1,
            "net_worth": value,
            "cash": details["bank_account"][index],
            "investments": details["investments"][index],
            "investment_values": {
                name: values[index]
                for name, values in details["investment_values"].items()
            },
            "automatic_contributions": {
                name: values[index]
                for name, values in details["automatic_contributions"].items()
            },
            "automatic_contribution_fees": {
                name: values[index]
                for name, values in details["automatic_contribution_fees"].items()
            },
            "income_values": {
                name: values[index] for name, values in details["income_values"].items()
            },
            "income_rates": {
                name: values[index] for name, values in details["income_rates"].items()
            },
            "expense_values": {
                name: values[index] for name, values in details["expense_values"].items()
            },
            "loan_balances": {
                name: values[index] for name, values in details["loan_balances"].items()
            },
            "loan_payments": {
                name: values[index] for name, values in details["loan_payments"].items()
            },
        }
        for index, value in enumerate(net_worth)
    ]


def _serialized_trajectory(trajectory):
    return {
        "run_number": trajectory.run_number,
        "seed": trajectory.seed,
        "monthly": _monthly_results(trajectory.net_worth, trajectory.details),
        "loan_repayments": [
            {"name": name, "month": month_index + 1}
            for name, month_index in trajectory.repayment_dates.items()
        ],
    }


def create_simulation_export(config, net_worth, repayment_dates, details,
                             exported_at=None, simulation_count=1,
                             trajectories=None):
    """Return a JSON export containing reproducible inputs and monthly results."""
    exported_at = exported_at or datetime.now(timezone.utc)
    if trajectories is not None and len(trajectories) != simulation_count:
        raise ValueError("trajectory count must match simulation_count")
    export = {
        "format": "financial-simulator-export",
        "schema_version": 1,
        "features": {
            "automatic_investment_contributions": AUTO_INVEST_EXPORT_VERSION,
            "financial_assumptions": FINANCIAL_ASSUMPTIONS_EXPORT_VERSION,
        },
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
            "monthly": _monthly_results(net_worth, details),
            "loan_repayments": [
                {"name": name, "month": month_index + 1}
                for name, month_index in repayment_dates.items()
            ],
            "events": build_simulation_events(
                config, repayment_dates, len(net_worth), details.get("purchase_events")
            ),
        },
    }
    if config.property_purchase is not None:
        export["features"]["property_purchase"] = 1
    if trajectories is not None:
        export["features"]["monte_carlo_trajectories"] = (
            MONTE_CARLO_TRAJECTORIES_EXPORT_VERSION
        )
        export["results"]["trajectories"] = [
            _serialized_trajectory(trajectory) for trajectory in trajectories
        ]
    return json.dumps(export, indent=2, ensure_ascii=False)


def read_simulation_export(data, include_warnings=False):
    """Validate an export and rebuild its config, optionally with warnings."""
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
    import_warnings = []
    features = document.get("features")
    if not isinstance(features, dict):
        features = {}
    feature_version = features.get(
        "automatic_investment_contributions"
    )
    auto_invest_feature_supported = feature_version == AUTO_INVEST_EXPORT_VERSION
    if not auto_invest_feature_supported:
        import_warnings.append(LEGACY_AUTO_INVEST_WARNING)

    def rebuild_collection(name, config_type):
        items = parameters.get(name)
        if items is None:
            return None
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise ValueError(f"The exported {name} must be a list.")
        try:
            rebuilt = []
            for item in items:
                values = dict(item)
                if (name == "investments"
                        and values.get("investment_type") == "real_estate"
                        and "monthly_expenses" not in values):
                    values["monthly_expenses"] = values.get("monthly_contribution", 0)
                    values["monthly_contribution"] = 0
                if name == "investments":
                    values.setdefault("auto_invest_enabled", False)
                    values.setdefault("auto_invest_min_cash", 10000.0)
                    values.setdefault("auto_invest_percentage", 10.0)
                    values.setdefault("auto_invest_max_per_year", 3)
                    if not auto_invest_feature_supported:
                        values["auto_invest_enabled"] = False
                rebuilt.append(config_type(**values))
            return rebuilt
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
    purchase = parameters.get("property_purchase")
    if purchase is not None:
        if not isinstance(purchase, dict):
            raise ValueError("The exported property purchase must be an object.")
        try:
            if "purchase" in purchase:
                template = purchase["purchase"]
                if not isinstance(template, dict):
                    raise ValueError("automatic purchase template must be an object")
                parameters["property_purchase"] = AutomaticPurchasePolicy(
                    PropertyPurchase(**template),
                    purchase["minimum_available_cash"],
                    purchase["minimum_cash_reserve"],
                )
            else:
                parameters["property_purchase"] = PropertyPurchase(**purchase)
        except (TypeError, ValueError) as error:
            raise ValueError(f"The exported property purchase is invalid: {error}") from error
    assumptions = parameters.get("assumptions")
    if assumptions is None:
        parameters["assumptions"] = FinancialAssumptions()
    elif not isinstance(assumptions, dict):
        raise ValueError("The exported financial assumptions must be an object.")
    else:
        try:
            parameters["assumptions"] = FinancialAssumptions(**assumptions)
        except (TypeError, ValueError) as error:
            raise ValueError(f"The exported financial assumptions are invalid: {error}") from error
    try:
        config = SimulationConfig(**parameters)
    except TypeError as error:
        raise ValueError(f"The exported parameters are invalid: {error}") from error

    trajectory_version = features.get("monte_carlo_trajectories")
    if trajectory_version is not None:
        if trajectory_version != MONTE_CARLO_TRAJECTORIES_EXPORT_VERSION:
            raise ValueError("This Monte Carlo trajectory version is not supported.")
        if not isinstance(config.seed, int) or isinstance(config.seed, bool):
            raise ValueError("The exported simulation seed must be an integer.")
        results = document.get("results")
        trajectories = results.get("trajectories") if isinstance(results, dict) else None
        simulation_count = results.get("simulation_count") if isinstance(results, dict) else None
        if (not isinstance(simulation_count, int)
                or not 1 <= simulation_count <= MAX_SIMULATION_COUNT):
            raise ValueError("The exported simulation count is invalid.")
        if not isinstance(trajectories, list) or len(trajectories) != simulation_count:
            raise ValueError("The exported trajectory count is invalid.")
        expected_months = config.years * 12
        for index, trajectory in enumerate(trajectories, start=1):
            if not isinstance(trajectory, dict):
                raise ValueError("An exported trajectory is invalid.")
            if trajectory.get("run_number") != index:
                raise ValueError("Exported trajectory run numbers must be sequential.")
            if trajectory.get("seed") != config.seed + index - 1:
                raise ValueError("An exported trajectory seed is invalid.")
            monthly = trajectory.get("monthly")
            if not isinstance(monthly, list) or len(monthly) != expected_months:
                raise ValueError("An exported trajectory has invalid monthly results.")
    return (config, import_warnings) if include_warnings else config


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
    for import_warning in st.session_state.pop("import_warnings", []):
        st.warning(import_warning)

    st.session_state.setdefault("additional_expenses", [])
    st.session_state.setdefault("dependents", [])
    st.session_state.setdefault("investments", [{
        "name": "Portfolio", "investment_type": "stock",
        "initial_amount": 1000.0, "monthly_contribution": 100.0,
        "monthly_expenses": 0.0, "yearly_tax": 0.0,
        "auto_invest_enabled": False, "auto_invest_min_cash": 10000.0,
        "auto_invest_percentage": 10.0, "auto_invest_max_per_year": 3,
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
    st.session_state.setdefault("financial_assumptions", asdict(FinancialAssumptions()))
    for collection_name in ("investments", "incomes", "expenses", "loans", "dependents"):
        for item in st.session_state[collection_name]:
            item.setdefault("_id", uuid.uuid4().hex)
            if collection_name == "investments":
                if "monthly_expenses" not in item:
                    item["monthly_expenses"] = (
                        item.get("monthly_contribution", 0.0)
                        if item.get("investment_type") == "real_estate" else 0.0
                    )
                    if item.get("investment_type") == "real_estate":
                        item["monthly_contribution"] = 0.0
                item.setdefault("yearly_tax", 0.0)
                item.setdefault("auto_invest_enabled", False)
                item.setdefault("auto_invest_min_cash", 10000.0)
                item.setdefault("auto_invest_percentage", 10.0)
                item.setdefault("auto_invest_max_per_year", 3)

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
        st.session_state.financial_assumptions = asdict(imported.assumptions)
        for key in list(st.session_state):
            if key.startswith("assumption_"):
                del st.session_state[key]
        st.session_state.investments = editable_items(
            imported.investments if imported.investments is not None else [
                InvestmentConfig("Portfolio", imported.investment_type,
                                 imported.investment_initial,
                                 0 if imported.investment_type == "real_estate"
                                 else imported.investment_contribution,
                                 imported.investment_contribution
                                 if imported.investment_type == "real_estate" else 0)
            ], (
                "initial_amount", "monthly_contribution", "monthly_expenses", "yearly_tax",
                "auto_invest_min_cash",
            )
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
        st.session_state.run_after_import = True

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
            "monthly_contribution": 0.0 if investment_type == "real_estate" else 100.0,
            "monthly_expenses": 100.0 if investment_type == "real_estate" else 0.0,
            "yearly_tax": 1000.0 if investment_type == "real_estate" else 0.0,
            "auto_invest_enabled": False, "auto_invest_min_cash": 10000.0,
            "auto_invest_percentage": 10.0, "auto_invest_max_per_year": 3,
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

    def render_investments():
        investment_types = sorted(Investment.TOPOLOGY_DETAILS)
        add_type = st.selectbox(
            "Investment type to add", investment_types,
            index=investment_types.index("stock"), key="investment_type_to_add",
        )
        if st.button("＋ Add investment", use_container_width=True):
            add_investment(add_type)
        for index, item in enumerate(st.session_state.investments):
            with st.container(border=True):
                if not card_is_open("investment", item):
                    if item["investment_type"] == "real_estate":
                        st.caption(
                            f"real estate · value ${item['initial_amount']:,.0f} · "
                            f"expenses ${item['monthly_expenses']:,.0f}/month · "
                            f"tax ${item['yearly_tax']:,.0f}/year"
                        )
                    else:
                        auto_summary = ""
                        if (item["investment_type"] in AUTO_INVEST_TYPES
                                and item["auto_invest_enabled"]):
                            auto_summary = (
                                f" · auto {item['auto_invest_percentage']:g}% when cash > "
                                f"${item['auto_invest_min_cash']:,.0f}"
                            )
                        st.caption(
                            f"{item['investment_type']} · initial "
                            f"${item['initial_amount']:,.0f} · monthly contribution "
                            f"${item['monthly_contribution']:,.0f}{auto_summary}"
                        )
                    continue
                item["name"] = st.text_input(
                    "Name", item["name"], key=f"investment_name_{item_key(item)}"
                )
                item["investment_type"] = st.selectbox(
                    "Type", investment_types,
                    index=investment_types.index(item["investment_type"]),
                    key=f"investment_type_{item_key(item)}",
                )
                item["initial_amount"] = st.number_input(
                    "Initial amount", min_value=0.0, value=float(item["initial_amount"]),
                    step=100.0, format="%.0f", key=f"investment_initial_{item_key(item)}",
                )
                if item["investment_type"] == "real_estate":
                    item["monthly_expenses"] = st.number_input(
                        "Monthly property expenses", min_value=0.0,
                        value=float(item["monthly_expenses"]), step=50.0, format="%.0f",
                        key=f"investment_expenses_{item_key(item)}",
                        help="Maintenance, insurance, management, and similar recurring costs.",
                    )
                    item["yearly_tax"] = st.number_input(
                        "Yearly property tax", min_value=0.0,
                        value=float(item["yearly_tax"]), step=100.0, format="%.0f",
                        key=f"investment_tax_{item_key(item)}",
                    )
                    st.caption(
                        "These costs are deducted by the simulation and linked read-only "
                        "in the Expenses category."
                    )
                else:
                    item["monthly_contribution"] = st.number_input(
                        "Monthly contribution", min_value=0.0,
                        value=float(item["monthly_contribution"]), step=50.0,
                        format="%.0f", key=f"investment_contribution_{item_key(item)}",
                    )
                    if item["investment_type"] in AUTO_INVEST_TYPES:
                        item["auto_invest_enabled"] = st.checkbox(
                            "Enable automatic cash-sweep contributions",
                            value=bool(item["auto_invest_enabled"]),
                            key=f"investment_auto_enabled_{item_key(item)}",
                            help=(
                                "Legacy-style policy. Disabled by default; explicit monthly "
                                "contributions are unaffected."
                            ),
                        )
                        item["auto_invest_min_cash"] = st.number_input(
                            "Automatic contribution cash threshold", min_value=0.0,
                            value=float(item["auto_invest_min_cash"]), step=500.0,
                            format="%.0f", key=f"investment_auto_cash_{item_key(item)}",
                            disabled=not item["auto_invest_enabled"],
                            help="A sweep is considered only when cash is above this amount.",
                        )
                        item["auto_invest_percentage"] = st.number_input(
                            "Automatic contribution (% of current cash)",
                            min_value=0.1, max_value=100.0,
                            value=float(item["auto_invest_percentage"]), step=1.0,
                            key=f"investment_auto_pct_{item_key(item)}",
                            disabled=not item["auto_invest_enabled"],
                        )
                        item["auto_invest_max_per_year"] = st.number_input(
                            "Maximum automatic contributions per year",
                            min_value=1, max_value=12,
                            value=int(item["auto_invest_max_per_year"]), step=1,
                            key=f"investment_auto_max_{item_key(item)}",
                            disabled=not item["auto_invest_enabled"],
                        )
                        st.caption(
                            "A sweep also requires cumulative yearly expenses below 80% "
                            "of cumulative net income. Each transaction has the modeled "
                            "$50 investment fee."
                        )
                if st.button("Remove investment", key=f"remove_investment_{item_key(item)}"):
                    remove_item(st.session_state.investments, index)
                    st.rerun()

    def render_incomes():
        income_types = sorted(IncomeSource("x", "salary", 1).income_growth)
        income_to_add = st.selectbox(
            "Income type to add", income_types, key="income_type_to_add"
        )
        if st.button("＋ Add income", use_container_width=True):
            add_income(income_to_add)
        for index, item in enumerate(st.session_state.incomes):
            with st.container(border=True):
                if not card_is_open("income", item):
                    st.caption(f"{item['income_type']} · ${item['monthly_amount']:,.0f}/month")
                    continue
                item["name"] = st.text_input(
                    "Name", item["name"], key=f"income_name_{item_key(item)}"
                )
                item["income_type"] = st.selectbox(
                    "Type", income_types, index=income_types.index(item["income_type"]),
                    key=f"income_type_{item_key(item)}",
                )
                item["monthly_amount"] = st.number_input(
                    "Monthly amount", min_value=0.0, value=float(item["monthly_amount"]),
                    step=100.0, format="%.0f", key=f"income_amount_{item_key(item)}",
                )
                item["tax_rate"] = st.number_input(
                    "Custom tax rate", 0.0, 100.0, value=float(item["tax_rate"]),
                    key=f"income_tax_{item_key(item)}",
                )
                item["bonus_pct"] = st.number_input(
                    "Bonus cap (%)", 0.0, 100.0, value=float(item["bonus_pct"]),
                    key=f"income_bonus_{item_key(item)}",
                )
                if st.button("Remove income", key=f"remove_income_{item_key(item)}"):
                    remove_item(st.session_state.incomes, index)
                    st.rerun()

    def render_expenses():
        expense_types = sorted(Expense("x", "housing", 1).expense_growth)
        expense_to_add = st.selectbox(
            "Expense type to add", expense_types, key="expense_type_to_add"
        )
        if st.button("＋ Add expense", use_container_width=True):
            add_expense()
        for index, item in enumerate(st.session_state.expenses):
            with st.container(border=True):
                if not card_is_open("expense", item):
                    st.caption(f"{item['expense_type']} · ${item['monthly_amount']:,.0f}/month")
                    continue
                item["name"] = st.text_input(
                    "Name", item["name"], key=f"expense_name_{item_key(item)}"
                )
                item["expense_type"] = st.selectbox(
                    "Type", expense_types,
                    index=expense_types.index(item["expense_type"]),
                    key=f"expense_type_{item_key(item)}",
                )
                item["monthly_amount"] = st.number_input(
                    "Monthly amount", min_value=0.0, value=float(item["monthly_amount"]),
                    step=25.0, format="%.0f", key=f"expense_amount_{item_key(item)}",
                )
                if st.button("Remove expense", key=f"remove_expense_{item_key(item)}"):
                    remove_item(st.session_state.expenses, index)
                    st.rerun()

        linked_properties = [
            item for item in st.session_state.investments
            if item["investment_type"] == "real_estate"
        ]
        if linked_properties:
            st.markdown("**Linked real-estate costs**")
            st.caption("Read-only here — edit these values in Investments.")
        for item in linked_properties:
            with st.container(border=True):
                st.markdown(f"**↗ {item['name']}**")
                st.caption(
                    f"Property expenses: ${item['monthly_expenses']:,.0f}/month · "
                    f"Property tax: ${item['yearly_tax']:,.0f}/year"
                )

    def render_loans():
        if st.button("＋ Add loan", use_container_width=True):
            add_loan()
        for index, item in enumerate(st.session_state.loans):
            with st.container(border=True):
                if not card_is_open("loan", item):
                    st.caption(
                        f"${item['amount']:,.0f} · {item['interest_rate']:.2f}% · "
                        f"{item['duration_years']} years"
                    )
                    continue
                item["name"] = st.text_input(
                    "Name", item["name"], key=f"loan_name_{item_key(item)}"
                )
                item["amount"] = st.number_input(
                    "Amount", min_value=0.0, value=float(item["amount"]), step=1000.0,
                    format="%.0f", key=f"loan_amount_{item_key(item)}",
                )
                item["interest_rate"] = st.number_input(
                    "Interest rate (%)", min_value=0.0, value=float(item["interest_rate"]),
                    step=0.1, key=f"loan_rate_{item_key(item)}",
                )
                item["duration_years"] = st.number_input(
                    "Duration (years)", min_value=1, value=int(item["duration_years"]),
                    key=f"loan_duration_{item_key(item)}",
                )
                item["fixed_rate"] = st.checkbox(
                    "Fixed rate", value=item["fixed_rate"],
                    key=f"loan_fixed_{item_key(item)}",
                )
                item["already_paid"] = st.number_input(
                    "Already paid", min_value=0.0, value=float(item["already_paid"]),
                    step=100.0, format="%.0f", key=f"loan_paid_{item_key(item)}",
                )
                if st.button("Remove loan", key=f"remove_loan_{item_key(item)}"):
                    remove_item(st.session_state.loans, index)
                    st.rerun()

    def render_dependents():
        st.caption("Add the people and recurring costs that shape this scenario.")
        if st.button("＋ Add daughter / child", use_container_width=True):
            add_dependent()
        for index, item in enumerate(st.session_state.dependents):
            with st.container(border=True):
                if not card_is_open("dependent", item):
                    st.caption(
                        f"age {item.get('age', 0)} · ${item['monthly_cost']:,.0f}/month"
                    )
                    continue
                item["name"] = st.text_input(
                    "Name", item["name"], key=f"dependent_name_{item_key(item)}"
                )
                item["age"] = st.number_input(
                    "Current age", min_value=0, max_value=25,
                    value=int(item.get("age", 0)), key=f"dependent_age_{item_key(item)}",
                )
                item["monthly_cost"] = st.number_input(
                    "Monthly child cost", min_value=0.0, value=float(item["monthly_cost"]),
                    step=25.0, format="%.0f", key=f"dependent_cost_{item_key(item)}",
                )
                automatic_costs = st.checkbox(
                    "Add school cost from age 6*",
                    value=item.get("school_start_age") is not None,
                    key=f"dependent_school_toggle_{item_key(item)}",
                )
                item["school_start_age"] = 6 if automatic_costs else None
                item["school_monthly_cost"] = st.number_input(
                    "School monthly cost*", min_value=0.0,
                    value=float(item.get("school_monthly_cost", 250.0)), step=25.0,
                    format="%.0f", key=f"dependent_school_cost_{item_key(item)}",
                    disabled=not automatic_costs,
                )
                st.caption("* Illustrative assumption; adjust or disable for your situation.")
                if st.button("Remove child", key=f"remove_dependent_{item_key(item)}"):
                    remove_item(st.session_state.dependents, index)
                    st.rerun()

    with st.sidebar:
        st.header("Scenario")
        with st.expander("Import saved scenario", key="sidebar_import"):
            uploaded_export = st.file_uploader(
                "Financial Simulator JSON", type="json",
                help="Select a JSON file created by the export button.",
            )
            if st.button(
                "Import parameters", disabled=uploaded_export is None,
                use_container_width=True,
            ):
                try:
                    imported, import_warnings = read_simulation_export(
                        uploaded_export.getvalue(), include_warnings=True,
                    )
                    import_config(imported)
                    st.session_state.import_warnings = import_warnings
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

        cash_value = st.session_state.get("scenario_initial_cash", 5000.0)
        fee_value = st.session_state.get("scenario_yearly_fee", 110.0)
        with st.expander(
            f"Cash · ${cash_value:,.0f} start · ${fee_value:,.0f}/year",
            expanded=True,
            key="sidebar_cash",
        ):
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

        investments = st.session_state.investments
        investment_initial = sum(item["initial_amount"] for item in investments)
        investment_monthly = sum(
            item["monthly_contribution"] for item in investments
            if item["investment_type"] != "real_estate"
        )
        with st.expander(
            f"Investments · {len(investments)} · ${investment_initial:,.0f} start · "
            f"${investment_monthly:,.0f}/month",
            key="sidebar_investments",
        ):
            render_investments()

        incomes = st.session_state.incomes
        income_monthly = sum(item["monthly_amount"] for item in incomes)
        with st.expander(
            f"Income · {len(incomes)} source(s) · ${income_monthly:,.0f}/month",
            key="sidebar_income",
        ):
            render_incomes()

        expenses = st.session_state.expenses
        linked_properties = [
            item for item in investments if item["investment_type"] == "real_estate"
        ]
        linked_monthly = sum(item["monthly_expenses"] for item in linked_properties)
        linked_yearly_tax = sum(item["yearly_tax"] for item in linked_properties)
        expense_monthly = sum(item["monthly_amount"] for item in expenses) + linked_monthly
        with st.expander(
            f"Expenses · {len(expenses)} editable + {len(linked_properties)} linked · "
            f"${expense_monthly:,.0f}/month · ${linked_yearly_tax:,.0f}/year tax",
            key="sidebar_expenses",
        ):
            render_expenses()

        loans = st.session_state.loans
        outstanding = sum(item["amount"] - item["already_paid"] for item in loans)
        with st.expander(
            f"Loans · {len(loans)} · ${outstanding:,.0f} outstanding",
            key="sidebar_loans",
        ):
            render_loans()

        assumptions = st.session_state.financial_assumptions
        with st.expander(
            "Financial assumptions · returns, dividends, fees and taxes",
            key="sidebar_assumptions",
        ):
            st.caption(
                "Illustrative annual assumptions. Return values and taxes are percentages; "
                "the dividend value is shown as a percentage but stored as a decimal rate."
            )
            st.markdown("**Investment returns**")
            for investment_type in INVESTMENT_TYPES:
                left, right = st.columns(2)
                assumptions["investment_return_means"][investment_type] = left.number_input(
                    f"{investment_type.replace('_', ' ').title()} mean (%)",
                    min_value=-100.0, max_value=100.0,
                    value=float(assumptions["investment_return_means"][investment_type]),
                    key=f"assumption_return_mean_{investment_type}",
                )
                assumptions["investment_return_std_devs"][investment_type] = right.number_input(
                    f"{investment_type.replace('_', ' ').title()} volatility (%)",
                    min_value=0.0, max_value=100.0,
                    value=float(assumptions["investment_return_std_devs"][investment_type]),
                    key=f"assumption_return_std_{investment_type}",
                )
            assumptions["stock_dividend_rate"] = st.number_input(
                "Stock dividend rate (%)", min_value=0.0, max_value=100.0,
                value=float(assumptions["stock_dividend_rate"]) * 100,
                key="assumption_stock_dividend_pct",
            ) / 100
            assumptions["contribution_fee"] = st.number_input(
                "Investment contribution fee", min_value=0.0,
                value=float(assumptions["contribution_fee"]), step=1.0,
                key="assumption_contribution_fee",
            )
            st.markdown("**Default income tax rates**")
            for income_type in INCOME_TYPES:
                assumptions["income_tax_rates"][income_type] = st.number_input(
                    f"{income_type.title()} tax (%)", min_value=0.0, max_value=100.0,
                    value=float(assumptions["income_tax_rates"][income_type]),
                    key=f"assumption_income_tax_{income_type}",
                )

        dependents = st.session_state.dependents
        dependent_monthly = sum(item["monthly_cost"] for item in dependents)
        with st.expander(
            f"Family · {len(dependents)} dependent(s) · ${dependent_monthly:,.0f}/month",
            key="sidebar_family",
        ):
            render_dependents()

    run_copy, run_controls = st.columns([3, 1], gap="large")
    with run_copy:
        st.subheader("Run projection")
        st.caption(
            "Average multiple stochastic simulations for a smoother long-term projection. "
            f"The current maximum is {MAX_SIMULATION_COUNT} runs for performance and "
            "export-size reasons, not a guarantee of statistical reliability. A larger "
            "tail-risk option would require separate runtime and export-capacity design."
        )
    with run_controls:
        simulation_count = st.number_input(
            "Number of simulations", min_value=1, max_value=MAX_SIMULATION_COUNT,
            value=MAX_SIMULATION_COUNT, step=1,
            help=("More runs produce a smoother average but take longer. Results remain "
                  "estimates based on the selected run count; the 25-run maximum is a "
                  "performance/export limit, not a statistical sufficiency guarantee."),
        )
        run_requested = _consume_run_request(
            st.session_state,
            st.button("▶ Run simulations", type="primary", use_container_width=True),
        )

    config = SimulationConfig(
        years=years, seed=42, initial_cash=_whole_amount(initial_cash),
        yearly_fee=_whole_amount(yearly_fee),
        investment_type="stock", investment_initial=0.0, investment_contribution=0.0,
        investments=[InvestmentConfig(
            item["name"], item["investment_type"], _whole_amount(item["initial_amount"]),
            _whole_amount(item["monthly_contribution"]),
            _whole_amount(item["monthly_expenses"]), _whole_amount(item["yearly_tax"]),
            auto_invest_enabled=(
                bool(item["auto_invest_enabled"])
                if item["investment_type"] in AUTO_INVEST_TYPES else False
            ),
            auto_invest_min_cash=_whole_amount(item["auto_invest_min_cash"]),
            auto_invest_percentage=float(item["auto_invest_percentage"]),
            auto_invest_max_per_year=int(item["auto_invest_max_per_year"]),
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
        assumptions=FinancialAssumptions(**st.session_state.financial_assumptions),
    )

    scenario_fingerprint = json.dumps(asdict(config), sort_keys=True)
    if run_requested:
        try:
            with st.spinner(f"Running {simulation_count} simulations..."):
                batch = run_simulation_batch(config, int(simulation_count))
        except (ValueError, TypeError, KeyError, InsufficientFundsException) as error:
            st.session_state.pop("simulation_result", None)
            st.error(f"This scenario cannot be simulated: {error}")
            return
        st.session_state.simulation_result = {
            "detail_version": SIMULATION_DETAIL_VERSION,
            "fingerprint": scenario_fingerprint,
            "simulation_count": int(simulation_count),
            "run_id": uuid.uuid4().hex,
            "result": batch.average_result,
            "trajectories": batch.trajectories,
        }

    stored_result = st.session_state.get("simulation_result")
    if stored_result is None:
        st.info("Choose the number of simulations and click **Run simulations**.")
        return
    if stored_result.get("detail_version") != SIMULATION_DETAIL_VERSION:
        st.warning(
            "The available result predates the current monthly detail format. "
            "Run the simulations again."
        )
        return
    if (stored_result["fingerprint"] != scenario_fingerprint
            or stored_result["simulation_count"] != int(simulation_count)):
        st.warning("The scenario or run count changed. Click **Run simulations** to refresh.")
        return

    average_result = stored_result["result"]
    trajectories = stored_result["trajectories"]
    run_id = stored_result.get("run_id", "legacy")

    st.subheader("Monte Carlo analysis")
    view_mode = st.radio(
        "Result view", ("Overview", "Individual run"), horizontal=True,
        key=f"monte_carlo_view_{run_id}",
        help=(
            "Overview is the month-by-month arithmetic average across all runs. "
            "Individual run shows one simulated path."
        ),
    )
    st.info(monte_carlo_view_explanation(view_mode, int(simulation_count)))
    if view_mode == "Overview":
        if int(simulation_count) < LOW_SAMPLE_THRESHOLD:
            st.warning(monte_carlo_sample_guidance(int(simulation_count)), icon="⚠️")
        else:
            st.caption(monte_carlo_sample_guidance(int(simulation_count)))
    selected_trajectory = None
    if view_mode == "Individual run":
        selected_run = st.selectbox(
            "Simulation run", range(1, len(trajectories) + 1),
            format_func=lambda run: f"Run {run} — seed {trajectories[run - 1].seed}",
            key=f"monte_carlo_run_{run_id}",
        )
        selected_trajectory = trajectories[selected_run - 1]
        net_worth, repayment_dates, details = selected_trajectory.result
        label_prefix = ""
        chart_title = (
            f"Projected financial position — run {selected_trajectory.run_number} "
            f"(seed {selected_trajectory.seed})"
        )
        detail_prefix = f"Run {selected_trajectory.run_number}"
    else:
        net_worth, repayment_dates, details = average_result
        label_prefix = "Average "
        chart_title = (
            "Arithmetic-average projected financial position "
            f"({simulation_count} simulations)"
        )
        detail_prefix = "Average"

    final_net_worth = net_worth[-1]
    starting_net_worth = net_worth[0]
    columns = st.columns(4)
    columns[0].metric(f"{label_prefix}final net worth", _money(final_net_worth), f"{final_net_worth - starting_net_worth:+,.0f}")
    columns[1].metric(f"{label_prefix}final cash", _money(details["bank_account"][-1]))
    columns[2].metric(f"{label_prefix}final investments", _money(details["investments"][-1]))
    columns[3].metric("Loans repaid", len(repayment_dates))

    st.download_button(
        "⬇️ Export parameters and results",
        data=create_simulation_export(
            config, *average_result,
            simulation_count=int(simulation_count),
            trajectories=trajectories,
        ),
        file_name=f"financial-simulation-{config.years}y-{simulation_count}-runs.json",
        mime="application/json",
        help="Download the scenario inputs and month-by-month results as JSON.",
    )

    chart_values = []
    for month, (worth, cash, investments) in enumerate(zip(
            net_worth, details["bank_account"], details["investments"]), start=1):
        chart_values.extend([
            {"month": month, "amount": worth, "series": f"{label_prefix}net worth"},
            {"month": month, "amount": cash, "series": f"{label_prefix}cash"},
            {"month": month, "amount": investments, "series": f"{label_prefix}investments"},
        ])

    lines = alt.Chart(alt.Data(values=chart_values)).mark_line(strokeWidth=2.5).encode(
        x=alt.X("month:Q", title="Month", scale=alt.Scale(domain=[1, len(net_worth)])),
        y=alt.Y("amount:Q", title="Amount", axis=alt.Axis(format="$,.0f")),
        color=alt.Color(
            "series:N", title="Financial series",
            scale=alt.Scale(
                domain=[f"{label_prefix}net worth", f"{label_prefix}cash",
                        f"{label_prefix}investments"],
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

    month_selection = alt.selection_point(
        name="selected_month", fields=["month"], nearest=True,
        on="click", clear=False, value=[{"month": len(net_worth)}],
    )
    month_data = alt.Data(values=[
        {"month": month} for month in range(1, len(net_worth) + 1)
    ])
    month_hitboxes = alt.Chart(month_data).mark_rule(
        opacity=0, strokeWidth=14,
    ).encode(
        x=alt.X("month:Q"),
    ).add_params(month_selection)
    selected_month_rule = alt.Chart(month_data).mark_rule(
        color="#8b5cf6", strokeWidth=2, opacity=0.75,
    ).encode(
        x=alt.X("month:Q"),
    ).transform_filter(month_selection)

    events = build_simulation_events(
        config, repayment_dates, len(net_worth), details.get("purchase_events")
    )
    chart = lines + month_hitboxes + selected_month_rule
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
            angle=270, align="right", baseline="middle", dx=32, fontSize=11,
        ).encode(
            x=alt.X("month:Q"),
            y=alt.Y("label_height:Q"),
            text=alt.Text("label:N"),
            color=alt.Color("event_type:N", scale=event_colors, legend=None),
        )
        chart = (chart + rules + labels).resolve_scale(color="independent")

    chart = chart.properties(
        height=460,
        title=chart_title,
    ).interactive()
    st.caption(
        "Click any month on the chart to inspect its asset values, balances, "
        "income, and payments below."
    )
    chart_state = st.altair_chart(
        chart, use_container_width=True,
        key=f"projection_chart_{run_id}_{view_mode}_{getattr(selected_trajectory, 'run_number', 0)}",
        on_select="rerun", selection_mode="selected_month",
    )
    if events:
        st.caption("Events: " + " · ".join(
            f"month {event['month']}: {event['label']}" for event in events
        ))
    selected = chart_state.selection.get("selected_month", [])
    selected_month = len(net_worth)
    if selected:
        selection = selected[0] if isinstance(selected, list) else selected
        selected_month = int(round(selection["month"]))
    selected_month = min(max(selected_month, 1), len(net_worth))
    balances, flows = get_month_details(net_worth, details, selected_month)
    st.subheader(f"{detail_prefix} detail for month {selected_month}")
    balance_column, flow_column = st.columns(2, gap="large")
    with balance_column:
        st.markdown("**Ending balances and asset values**")
        st.dataframe(
            balances, hide_index=True, use_container_width=True,
            column_config={
                "Amount": st.column_config.NumberColumn(format="$%.0f"),
            },
        )
    with flow_column:
        st.markdown("**Income and payments during the month**")
        st.dataframe(
            flows, hide_index=True, use_container_width=True,
            column_config={
                "Amount": st.column_config.NumberColumn(format="$%.0f"),
            },
        )
    st.caption("Illustrative projection only — this is not financial advice. *Age-based family costs are configurable assumptions, not validated rules.")


if __name__ == "__main__":
    main()
