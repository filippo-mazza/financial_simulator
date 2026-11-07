"""Streamlit interface for the financial simulator.

The model-building functions are intentionally independent of Streamlit so they
can be exercised in unit tests and reused by another front end later.
"""

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from copy import deepcopy
import inspect
import json
import random
import re
from statistics import fmean
import uuid

from bank_account import BankAccount, InsufficientFundsException
from expense import AgeBasedExpense, ChildcareExpense, Expense, YearlyExpense
from financial_assumptions import FinancialAssumptions, INCOME_TYPES, INVESTMENT_TYPES
from income_source import IncomeSource
from investment import Investment
from utils import compute_net_worth_over_time
from property_purchase import (
    AutomaticPurchasePolicy,
    MortgageAffordabilityPolicy,
    PropertyPurchase,
    PurchaseCompletionDelayPolicy,
    evaluate_mortgage_affordability,
)
from property_jurisdiction_catalog import (
    default_property_jurisdiction_profiles,
    preset_update_notices,
)
from property_jurisdiction import (
    describe_profile_assumptions,
    PropertyJurisdictionProfile,
    estimate_property_costs,
    profiles_by_name,
)
from loan import Loan
from scenario_workspace import (
    ALTERNATIVE_ID,
    BASELINE_ID,
    ScenarioVariant,
    ScenarioWorkspace,
)

AUTO_INVEST_EXPORT_VERSION = 1
FINANCIAL_ASSUMPTIONS_EXPORT_VERSION = 1
MONTE_CARLO_TRAJECTORIES_EXPORT_VERSION = 1
SCENARIO_WORKSPACE_EXPORT_VERSION = 1
SIMULATION_DETAIL_VERSION = 4
MAX_SIMULATION_COUNT = 25
LOW_SAMPLE_THRESHOLD = 10
CHART_EVENT_COLORS = {
    "Loan repaid": "#e06c75",
    "School starts": "#b7791f",
    "Property purchase": "#0e7490",
    "Curveball": "#c2410c",
}
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
    # None preserves the legacy recurring-child-cost behavior for old exports.
    # A value enables the explicit childcare-to-school transition model.
    childcare_monthly_cost: float | None = None


@dataclass
class CurveballConfig:
    """One optional, temporary adverse event in a trajectory."""

    name: str
    enabled: bool = False
    probability: float = 0.0
    start_month_min: int = 1
    start_month_max: int = 120
    duration_months: int = 3
    affected_income_name: str | None = None
    income_reduction_pct: float = 100.0
    recovery_months: int = 0


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
    ownership_percentage: float = 100.0
    linked_loan_name: str | None = None
    liability_share_percentage: float = 100.0
    rental_income_name: str | None = None
    jurisdiction_profile_name: str | None = None


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
    curveballs: list[CurveballConfig] = field(default_factory=list)
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
    selected_rent_expense: str | None = None
    jurisdiction_profiles: list[PropertyJurisdictionProfile] = field(default_factory=list)
    property_purchase_jurisdiction_profile: str | None = None
    purchase_cadastral_income: float | None = None
    purchase_assessed_value: float | None = None


@dataclass(frozen=True)
class ScenarioSuggestion:
    """A deterministic, opt-in Alternative B derived from one current scenario."""

    key: str
    title: str
    description: str
    config: SimulationConfig


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


def deterministic_ab_suggestions(config: SimulationConfig):
    """Return small, reproducible A/B alternatives for the current inputs.

    These are transparent scenario prompts, not financial advice and not an AI
    call. Each rule changes one bounded assumption so the existing paired
    comparison can show the modeled consequence before the user adopts anything.
    """
    suggestions = []

    if config.property_purchase is None:
        income_specs = config.incomes or [IncomeConfig(
            "Primary income", config.income_type, config.monthly_income,
            config.tax_rate, config.bonus_pct,
        )]
        monthly_income = sum(max(0.0, item.monthly_amount) for item in income_specs)
        purchase_price = max(100_000.0, round(monthly_income * 48 / 5_000) * 5_000)
        purchase = PropertyPurchase(
            purchase_price=purchase_price,
            down_payment_percentage=20.0,
            transaction_fees=round(purchase_price * 0.03),
            mortgage_interest_rate=4.0,
            mortgage_duration_years=25,
            purchase_month=0,
            property_name="Suggested property",
        )
        alternative = deepcopy(config)
        alternative.property_purchase = AutomaticPurchasePolicy(
            purchase, minimum_available_cash=0.0, minimum_cash_reserve=0.0,
        )
        suggestions.append(ScenarioSuggestion(
            "automatic_property",
            "Test an automatic home purchase",
            (
                f"Model a ${purchase_price:,.0f} property with 20% down and "
                "3% transaction fees; the simulation still decides whether cash "
                "makes closing possible."
            ),
            alternative,
        ))
    else:
        alternative = deepcopy(config)
        alternative.property_purchase = None
        alternative.selected_rent_expense = None
        suggestions.append(ScenarioSuggestion(
            "without_property",
            "Compare without the property purchase",
            "Remove the configured purchase to isolate the rest of the household plan.",
            alternative,
        ))

    investments = config.investments
    if investments is None:
        investments = [InvestmentConfig(
            "Portfolio", config.investment_type, config.investment_initial,
            config.investment_contribution,
        )]
    eligible_index = next((index for index, item in enumerate(investments)
                           if item.investment_type in AUTO_INVEST_TYPES), None)
    if eligible_index is None:
        alternative = deepcopy(config)
        alternative.investments = [*deepcopy(investments), InvestmentConfig(
            "Suggested savings", "safe", 0.0, 100.0,
        )]
        title = "Add a modest savings investment"
        description = "Add a safe investment receiving a deterministic $100 monthly contribution."
    else:
        alternative = deepcopy(config)
        if alternative.investments is None:
            alternative.investments = deepcopy(investments)
        target = alternative.investments[eligible_index]
        increment = max(100.0, round(target.monthly_contribution * 0.10 / 50) * 50)
        target.monthly_contribution += increment
        title = "Increase monthly investing"
        description = (
            f"Increase {target.name}'s monthly contribution by ${increment:,.0f} "
            "to show the modeled savings sensitivity."
        )
    suggestions.append(ScenarioSuggestion(
        "increase_investing", title, description, alternative,
    ))

    alternative = deepcopy(config)
    cash_increment = max(1_000.0, round(max(config.initial_cash, 1_000.0) * 0.25 / 500) * 500)
    alternative.initial_cash += cash_increment
    suggestions.append(ScenarioSuggestion(
        "larger_cash_buffer",
        "Keep a larger starting cash buffer",
        f"Add ${cash_increment:,.0f} to starting cash as a liquidity sensitivity check.",
        alternative,
    ))
    return suggestions


def property_purchase_payment(purchase):
    """Return the preview payment using the authoritative Loan calculation."""
    return Loan("preview", purchase.mortgage_principal,
                purchase.mortgage_interest_rate, purchase.mortgage_duration_years,
                purchase.fixed_rate).monthly_repayment


def mortgage_affordability_preview(purchase, policy, incomes, loans, assumptions):
    """Evaluate UI configuration with the same domain objects as a simulation."""
    income_sources = []
    for item in incomes:
        taxation = (
            item["tax_rate"]
            if item["tax_rate"] else assumptions.income_tax_rates[item["income_type"]]
        )
        source = IncomeSource(
            item["name"], item["income_type"], item["monthly_amount"],
            specific_taxation=None, bonus_pct=item["bonus_pct"] or None,
        )
        source.specific_taxation = taxation
        income_sources.append(source)
    debt_loans = [
        Loan(
            item["name"], item["amount"], item["interest_rate"],
            item["duration_years"], item["fixed_rate"], item["already_paid"],
        )
        for item in loans
    ]
    return evaluate_mortgage_affordability(
        purchase, policy, income_sources, debt_loans,
    )


_BASIS_LABELS = {
    "statutory": "statutory rate",
    "indicative": "indicative allowance",
    "user": "user assumption",
}


def _jurisdiction_lookup(config):
    """Return validated scenario profiles keyed by exact display name."""
    return profiles_by_name(config.jurisdiction_profiles)


def _profile_for_name(lookup, name):
    if name is None:
        return None
    try:
        return lookup[name]
    except KeyError as error:
        raise ValueError(f"Unknown property jurisdiction profile '{name}'") from error


def _investment_profile_costs(item, income_specs, profile_lookup):
    """Return profile-derived monthly and annual costs for one investment."""
    profile = _profile_for_name(profile_lookup, item.jurisdiction_profile_name)
    if profile is None:
        return 0.0, 0.0
    if item.investment_type != "real_estate":
        raise ValueError("Jurisdiction profiles require a real-estate investment")
    gross_rent = next((
        income.monthly_amount for income in income_specs
        if income.name == item.rental_income_name
    ), 0.0)
    estimate = estimate_property_costs(
        profile, item.initial_amount, gross_rent, item.ownership_percentage,
    )
    return estimate.monthly_operating_costs, estimate.annual_property_tax


def purchase_cost_estimate(profile, purchase, cadastral_income=None,
                           assessed_value=None):
    """Estimate acquisition costs for a purchase, including financed amount."""
    return estimate_property_costs(
        profile, purchase.purchase_price,
        mortgage_amount=purchase.mortgage_principal,
        cadastral_income=cadastral_income, assessed_value=assessed_value,
    )


def resolved_property_purchase(config):
    """Add selected jurisdiction estimates to a purchase without mutating config."""
    configured = config.property_purchase
    profile = _profile_for_name(
        _jurisdiction_lookup(config), config.property_purchase_jurisdiction_profile,
    )
    if profile is None:
        return configured
    if configured is None:
        raise ValueError("A purchase jurisdiction profile requires a property purchase")
    purchase = configured.purchase if isinstance(
        configured, AutomaticPurchasePolicy
    ) else configured
    estimate = purchase_cost_estimate(
        profile, purchase, config.purchase_cadastral_income,
        config.purchase_assessed_value,
    )
    resolved = replace(
        purchase,
        transaction_fees=purchase.transaction_fees + estimate.acquisition_costs,
        monthly_expenses=(
            purchase.monthly_expenses + estimate.monthly_operating_costs
        ),
        yearly_tax=purchase.yearly_tax + estimate.annual_property_tax,
    )
    if not isinstance(configured, AutomaticPurchasePolicy):
        return resolved
    return AutomaticPurchasePolicy(
        resolved,
        configured.minimum_available_cash,
        configured.minimum_cash_reserve,
        configured.mortgage_affordability,
        configured.completion_delay,
    )


@dataclass(frozen=True)
class SimulationBatch:
    """A Monte Carlo average together with the individual paths behind it."""
    average_result: tuple
    trajectories: list[SimulationTrajectory]


@dataclass(frozen=True)
class PairedScenarioComparison:
    """Two batches run with the same horizon, trajectory count, and seeds."""

    baseline_name: str
    alternative_name: str
    baseline_config: SimulationConfig
    alternative_config: SimulationConfig
    baseline_batch: SimulationBatch
    alternative_batch: SimulationBatch
    simulation_count: int
    horizon_years: int
    base_seed: int
    warnings: tuple[str, ...]


def _scenario_structure_signature(config):
    """Describe draw-relevant structure without comparing financial amounts."""
    def collection_signature(items, fields, flags=()):
        return tuple(
            (
                *(getattr(item, field_name, None) for field_name in fields),
                *(bool(getattr(item, field_name, None)) for field_name in flags),
            )
            for item in items
        )

    income_specs = config.incomes if config.incomes is not None else [IncomeConfig(
        "Primary income", config.income_type, config.monthly_income,
        config.tax_rate, config.bonus_pct,
    )]
    expense_specs = [
        *(config.expenses if config.expenses is not None else [ExpenseConfig(
            "Living expenses", config.expense_type, config.monthly_expense,
        )]),
        *config.additional_expenses,
    ]
    investment_specs = (
        config.investments if config.investments is not None else [InvestmentConfig(
            "Portfolio", config.investment_type, config.investment_initial,
            config.investment_contribution,
        )]
    )
    loan_specs = config.loans if config.loans is not None else [LoanConfig(
        "Loan", config.loan_amount, config.loan_rate, config.loan_duration,
        config.fixed_rate, config.loan_already_paid,
    )]
    purchase = config.property_purchase
    if isinstance(purchase, AutomaticPurchasePolicy):
        # Any automatic-purchase policy input can alter the closing month. Closing
        # creates a new stochastic investment, so that timing affects later draws.
        purchase_signature = ("automatic", asdict(purchase))
    elif purchase is not None:
        purchase_signature = ("scheduled", purchase.purchase_month)
    else:
        purchase_signature = None
    return (
        # A non-zero bonus adds an annual Gaussian draw. Freelance and salary
        # stochastic behavior is already represented by income_type.
        collection_signature(
            income_specs, ("name", "income_type"), flags=("bonus_pct",),
        ),
        # Unexpected expenses consume draws; including all effective expenses
        # also preserves their order and covers additional_expenses.
        collection_signature(expense_specs, ("name", "expense_type")),
        collection_signature(investment_specs, ("name", "investment_type")),
        collection_signature(loan_specs, ("name",)),
        tuple((item.name, item.school_start_age) for item in config.dependents),
        tuple((
            item.name, item.enabled, item.probability, item.start_month_min,
            item.start_month_max, item.duration_months, item.affected_income_name,
            item.income_reduction_pct, item.recovery_months,
        ) for item in config.curveballs),
        purchase_signature,
    )


def run_paired_scenario_comparison(workspace, simulation_count):
    """Run A and B with matched seeds and Baseline A's horizon.

    A shared seed is reproducible common-random-number input. Structural changes
    can consume draws in a different order, so this does not claim perfect shock
    coupling when the scenario shapes differ.
    """
    if not isinstance(workspace, ScenarioWorkspace):
        raise ValueError("Create an A/B scenario workspace before comparing")
    baseline_variant = workspace.variant(BASELINE_ID)
    alternative_variant = workspace.variant(ALTERNATIVE_ID)
    baseline = baseline_variant.config
    alternative = alternative_variant.config
    horizon_years = baseline.years
    base_seed = baseline.seed
    warnings = []
    if alternative.years != horizon_years:
        warnings.append(
            f"Alternative B's saved {alternative.years}-year horizon was overridden "
            f"with Baseline A's {horizon_years}-year horizon for this comparison."
        )
    if alternative.seed != base_seed:
        warnings.append(
            "Alternative B's saved seed was overridden with Baseline A's seed so "
            "trajectory numbers are paired."
        )
    if baseline.assumptions != alternative.assumptions:
        warnings.append(
            "Financial return, fee, or tax assumptions differ between A and B; "
            "the displayed delta includes those assumption changes."
        )
    if _scenario_structure_signature(baseline) != _scenario_structure_signature(
            alternative):
        warnings.append(
            "Scenario structure differs. Seeds are paired by run number, but added or "
            "removed stochastic components may consume random draws in a different "
            "order, so identical seeds do not guarantee identical underlying shocks."
        )
    baseline = replace(baseline, years=horizon_years, seed=base_seed)
    alternative = replace(alternative, years=horizon_years, seed=base_seed)
    baseline_batch = run_simulation_batch(baseline, simulation_count)
    alternative_batch = run_simulation_batch(alternative, simulation_count)
    baseline_seeds = [item.seed for item in baseline_batch.trajectories]
    alternative_seeds = [item.seed for item in alternative_batch.trajectories]
    if baseline_seeds != alternative_seeds:
        raise RuntimeError("Paired scenario trajectory seeds do not match")
    return PairedScenarioComparison(
        baseline_variant.name, alternative_variant.name,
        baseline, alternative, baseline_batch, alternative_batch,
        simulation_count, horizon_years, base_seed, tuple(warnings),
    )


def _percentile(values, proportion):
    ordered = sorted(values)
    if not ordered:
        raise ValueError("Cannot calculate a percentile of an empty collection")
    position = (len(ordered) - 1) * proportion
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def paired_delta_quantiles(comparison, detail_name="net_worth"):
    """Return monthly statistics of paired B-minus-A trajectory differences."""
    if detail_name == "net_worth":
        baseline_series = [
            item.net_worth for item in comparison.baseline_batch.trajectories
        ]
        alternative_series = [
            item.net_worth for item in comparison.alternative_batch.trajectories
        ]
    else:
        baseline_series = [
            item.details[detail_name]
            for item in comparison.baseline_batch.trajectories
        ]
        alternative_series = [
            item.details[detail_name]
            for item in comparison.alternative_batch.trajectories
        ]
    rows = []
    for month_index in range(comparison.horizon_years * 12):
        deltas = [
            alternative[month_index] - baseline[month_index]
            for baseline, alternative in zip(baseline_series, alternative_series)
        ]
        rows.append({
            "month": month_index + 1,
            "mean": fmean(deltas),
            "p10": _percentile(deltas, 0.1),
            "median": _percentile(deltas, 0.5),
            "p90": _percentile(deltas, 0.9),
        })
    return rows


def _final_mapped_total(details, detail_name):
    return sum(
        values[-1] for values in details.get(detail_name, {}).values()
        if values
    )


def comparison_balance_rows(comparison):
    """Return non-additive final balance comparisons."""
    baseline_net_worth, _, baseline = comparison.baseline_batch.average_result
    alternative_net_worth, _, alternative = comparison.alternative_batch.average_result
    values = (
        ("Net worth", baseline_net_worth[-1], alternative_net_worth[-1]),
        ("Available cash", baseline["bank_account"][-1], alternative["bank_account"][-1]),
        ("Investment assets", baseline["investments"][-1], alternative["investments"][-1]),
        ("Outstanding debt", _final_mapped_total(baseline, "loan_balances"),
         _final_mapped_total(alternative, "loan_balances")),
        ("Property equity", _final_mapped_total(baseline, "property_equity"),
         _final_mapped_total(alternative, "property_equity")),
    )
    return [
        {"Balance": name, "Baseline A": a_value, "Alternative B": b_value,
         "B minus A": b_value - a_value}
        for name, a_value, b_value in values
    ]


def _sum_mapped_flows(details, detail_name, predicate=lambda _name: True):
    return sum(
        sum(values) for name, values in details.get(detail_name, {}).items()
        if predicate(name)
    )


def comparison_cash_flow_rows(comparison):
    """Compare tracked flows without presenting them as an additive bridge."""
    baseline = comparison.baseline_batch.average_result[2]
    alternative = comparison.alternative_batch.average_result[2]

    def totals(details, config):
        configured_expenses = [
            *(config.expenses if config.expenses is not None else [
                ExpenseConfig("Living expenses", config.expense_type,
                              config.monthly_expense)
            ]),
            *config.additional_expenses,
        ]
        housing_names = {
            item.name for item in configured_expenses
            if item.expense_type == "housing"
        }
        property_tax = _sum_mapped_flows(
            details, "expense_values",
            lambda name: "property tax (linked)" in name.lower(),
        )
        housing = _sum_mapped_flows(
            details, "expense_values",
            lambda name: (
                name in housing_names
                or "property expenses (linked)" in name.lower()
            ),
        )
        all_expenses = _sum_mapped_flows(details, "expense_values")
        return {
            "Net income received": _sum_mapped_flows(details, "income_values"),
            "Housing and property operating expenses": housing,
            "Other household expenses": all_expenses - property_tax - housing,
            "Property ownership tax": property_tax,
            "Debt-service payments": _sum_mapped_flows(details, "loan_payments"),
            "Automatic investment gross cash transfers": _sum_mapped_flows(
                details, "automatic_contributions",
            ),
            "Automatic investment fees": _sum_mapped_flows(
                details, "automatic_contribution_fees",
            ),
            "Property purchase fees": sum(details.get("purchase_fees", [])),
        }

    a_totals = totals(baseline, comparison.baseline_config)
    b_totals = totals(alternative, comparison.alternative_config)
    treatments = {
        "Net income received": "Cash inflow (income tax already netted)",
        "Housing and property operating expenses": "Cash outflow / cost",
        "Other household expenses": "Cash outflow / cost",
        "Property ownership tax": "Cash outflow / tax",
        "Debt-service payments": "Mixed principal transfer and interest cost",
        "Automatic investment gross cash transfers": (
            "Cash transfer including the fee shown below, not an extra cost"
        ),
        "Automatic investment fees": "Memo: included in gross transfer / wealth cost",
        "Property purchase fees": "Cash outflow / wealth cost",
    }
    return [
        {
            "Tracked flow": name,
            "Treatment": treatments[name],
            "Baseline A": a_totals[name],
            "Alternative B": b_totals[name],
            "B minus A": b_totals[name] - a_totals[name],
        }
        for name in treatments
    ]


def scenario_input_differences(comparison):
    """Return leaf-level input differences between effective comparison configs."""
    missing = object()

    def flatten(value, path=""):
        if isinstance(value, dict):
            if not value:
                return {path: {}}
            rows = {}
            for key in sorted(value):
                rows.update(flatten(value[key], f"{path}.{key}" if path else key))
            return rows
        if isinstance(value, list):
            if not value:
                return {path: []}
            rows = {}
            for index, item in enumerate(value):
                rows.update(flatten(item, f"{path}[{index}]"))
            return rows
        return {path: value}

    baseline = flatten(asdict(comparison.baseline_config))
    alternative = flatten(asdict(comparison.alternative_config))
    rows = []
    for path in sorted(set(baseline) | set(alternative)):
        a_value = baseline.get(path, missing)
        b_value = alternative.get(path, missing)
        if a_value != b_value:
            rows.append({
                "Input": path,
                "Baseline A": "(not present)" if a_value is missing else a_value,
                "Alternative B": "(not present)" if b_value is missing else b_value,
            })
    return rows


def _comparison_change_labels(comparison, differences):
    """Turn changed config paths into a short list of human-readable items."""
    labels = []
    collection_pattern = re.compile(r"^(\w+)\[(\d+)\](?:\.(.+))?$")
    indexed_paths = {}
    for difference in differences:
        match = collection_pattern.match(difference["Input"])
        if match:
            collection, index_text, field = match.groups()
            indexed_paths.setdefault((collection, int(index_text)), set()).add(field)
    indexed_collections = {collection for collection, _index in indexed_paths}
    field_labels = {
        "monthly_amount": "monthly amount",
        "monthly_contribution": "monthly contribution",
        "monthly_expenses": "monthly expenses",
        "yearly_tax": "yearly tax",
        "income_type": "income type",
        "expense_type": "expense type",
        "initial_amount": "initial amount",
    }
    for difference in differences:
        path = difference["Input"]
        if path.startswith("property_purchase"):
            label = "property purchase"
        elif path.startswith("selected_rent_expense"):
            label = "selected rent expense"
        elif path in {"initial_cash", "yearly_fee"}:
            label = "starting cash" if path == "initial_cash" else "yearly fee"
        else:
            match = collection_pattern.match(path)
            if not match:
                collection = path.split(".", 1)[0]
                if collection in indexed_collections:
                    continue
                label = collection.replace("_", " ")
            else:
                collection, index_text, field = match.groups()
                index = int(index_text)
                baseline_items = getattr(comparison.baseline_config, collection, None) or []
                alternative_items = (
                    getattr(comparison.alternative_config, collection, None) or []
                )
                baseline_item = (
                    baseline_items[index] if index < len(baseline_items) else None
                )
                alternative_item = (
                    alternative_items[index] if index < len(alternative_items) else None
                )
                baseline_name = getattr(baseline_item, "name", None)
                alternative_name = getattr(alternative_item, "name", None)
                baseline_names = {
                    getattr(item, "name", None) for item in baseline_items
                }
                alternative_names = {
                    getattr(item, "name", None) for item in alternative_items
                }
                if baseline_name != alternative_name:
                    if baseline_name and baseline_name not in alternative_names:
                        item_name = f"{baseline_name} (removed)"
                    elif alternative_name and alternative_name not in baseline_names:
                        item_name = f"{alternative_name} (added)"
                    else:
                        # The same named item moved to another index; this is not
                        # an input change and should not be presented as one.
                        continue
                else:
                    item_name = (
                        baseline_name or alternative_name
                        or collection.replace("_", " ")
                    )
                label = item_name
                fields = indexed_paths[(collection, index)]
                if field and len(fields) == 1:
                    label = f"{item_name}'s {field_labels.get(field, field.replace('_', ' '))}"
        if label not in labels:
            labels.append(label)
    return labels


def comparison_explanation(comparison):
    """Describe the modeled A/B result and its observable input/flow differences."""
    balance_rows = comparison_balance_rows(comparison)
    net_worth = next(row for row in balance_rows if row["Balance"] == "Net worth")
    delta = net_worth["B minus A"]
    differences = scenario_input_differences(comparison)
    labels = _comparison_change_labels(comparison, differences)
    if abs(delta) < 0.5:
        outcome = (
            f"Alternative B ({comparison.alternative_name}) finishes at approximately "
            f"the same average net worth as Baseline A ({_money(net_worth['Alternative B'])})."
        )
    else:
        direction = "higher" if delta > 0 else "lower"
        outcome = (
            f"Alternative B ({comparison.alternative_name}) finishes with average net worth "
            f"{_money(abs(delta))} {direction} than Baseline A "
            f"({_money(net_worth['Alternative B'])} vs {_money(net_worth['Baseline A'])})."
        )
    if labels:
        displayed = labels[:3]
        if len(labels) > 3:
            displayed.append(f"{len(labels) - 3} other changed input(s)")
        changes = "The compared change(s) are " + ", ".join(displayed) + "."
    else:
        changes = "The effective A and B inputs are identical."
    flow_differences = sorted(
        (row for row in comparison_cash_flow_rows(comparison)
         if abs(row["B minus A"]) >= 0.5),
        key=lambda row: abs(row["B minus A"]), reverse=True,
    )[:2]
    if flow_differences:
        drivers = " The largest tracked B−A flow differences are " + "; ".join(
            f"{row['Tracked flow']} ({row['B minus A']:+,.0f})"
            for row in flow_differences
        ) + "."
    else:
        drivers = " No tracked cash-flow category differs materially between A and B."
    return (
        f"{outcome} {changes}{drivers} This is an average modeled result from "
        f"{comparison.simulation_count} paired simulation(s), not financial advice "
        "or a guaranteed outcome; the listed flows are indicators, not a causal "
        "attribution bridge."
    )


def aggregate_purchase_events(trajectories):
    """Return one midpoint marker for actual purchases in the averaged Overview."""
    purchases = []
    for trajectory in trajectories:
        for event in trajectory.details.get("purchase_events", []):
            purchases.append(event)
    if not purchases:
        return []

    months = [event["month"] for event in purchases]
    first_month, last_month = min(months), max(months)
    # Round a half-month upward so the marker remains on an actual chart month.
    midpoint_month = (first_month + last_month + 1) // 2
    property_name = purchases[0]["property_name"]
    return [{
        "month": midpoint_month,
        "property_name": property_name,
        "chart_label": f"Avg purchase · M{midpoint_month + 1}",
        "label": (
            f"{property_name} average purchase period: month {midpoint_month + 1} "
            f"(range {first_month + 1}–{last_month + 1}; "
            f"{len(purchases)} of {len(trajectories)} trajectories)"
        ),
    }]


def build_components(config: SimulationConfig):
    """Create fresh model objects from UI/configuration values."""
    profile_lookup = _jurisdiction_lookup(config)
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

    if config.loans is None:
        loan_specs = [LoanConfig("Loan", config.loan_amount, config.loan_rate,
                                 config.loan_duration, config.fixed_rate,
                                 config.loan_already_paid)]
    else:
        loan_specs = config.loans

    investment_names = [item.name for item in investment_specs]
    if len(investment_names) != len(set(investment_names)):
        raise ValueError("Investment names must be unique")
    linked_loan_names = []
    linked_rental_names = []
    for item in investment_specs:
        if not 0 < item.ownership_percentage <= 100:
            raise ValueError("Property ownership percentage must be above 0 and at most 100")
        if not 0 < item.liability_share_percentage <= 100:
            raise ValueError("Property liability share must be above 0 and at most 100")
        if item.investment_type != "real_estate":
            if (item.ownership_percentage != 100
                    or item.liability_share_percentage != 100
                    or item.linked_loan_name is not None
                    or item.rental_income_name is not None
                    or item.jurisdiction_profile_name is not None):
                raise ValueError(
                    "Property ownership and links require a real-estate investment"
                )
            continue
        if item.linked_loan_name is not None:
            if sum(loan.name == item.linked_loan_name for loan in loan_specs) != 1:
                raise ValueError(
                    f"Linked property loan '{item.linked_loan_name}' must identify "
                    "exactly one configured loan"
                )
            linked_loan_names.append(item.linked_loan_name)
        if item.rental_income_name is not None:
            matches = [
                income for income in income_specs
                if income.name == item.rental_income_name
            ]
            if len(matches) != 1 or matches[0].income_type != "rent":
                raise ValueError(
                    f"Linked rental income '{item.rental_income_name}' must identify "
                    "exactly one rent income source"
                )
            linked_rental_names.append(item.rental_income_name)
    if len(linked_loan_names) != len(set(linked_loan_names)):
        raise ValueError("A loan can be linked to only one property")
    if len(linked_rental_names) != len(set(linked_rental_names)):
        raise ValueError("A rental income source can be linked to only one property")

    profile_costs = {
        item.name: _investment_profile_costs(item, income_specs, profile_lookup)
        for item in investment_specs
    }

    if config.expenses is None:
        expense_specs = [ExpenseConfig("Living expenses", config.expense_type,
                                       config.monthly_expense)]
    else:
        expense_specs = config.expenses
    expense_specs = [*expense_specs, *config.additional_expenses]
    expenses = [Expense(item.name, item.expense_type, item.monthly_amount)
                for item in expense_specs]
    used_expense_names = {item.name for item in expense_specs}
    for investment in investment_specs:
        if investment.investment_type != "real_estate":
            continue
        estimated_monthly, estimated_yearly_tax = profile_costs[investment.name]
        monthly_expenses = investment.monthly_expenses + estimated_monthly
        yearly_tax = investment.yearly_tax + estimated_yearly_tax
        if monthly_expenses > 0:
            linked_name = f"{investment.name} — property expenses (linked)"
            if linked_name in used_expense_names:
                raise ValueError(f"Linked property expense name '{linked_name}' already exists")
            expenses.append(Expense(
                linked_name,
                "housing", monthly_expenses,
            ))
            used_expense_names.add(linked_name)
        if yearly_tax > 0:
            linked_name = f"{investment.name} — property tax (linked)"
            if linked_name in used_expense_names:
                raise ValueError(f"Linked property expense name '{linked_name}' already exists")
            expenses.append(YearlyExpense(
                linked_name,
                yearly_tax,
            ))
            used_expense_names.add(linked_name)
    # A dependent is a recurring cost in the model. Keeping this explicit makes
    # it easy to evolve into age-based childcare/education rules later.
    for dependent in config.dependents:
        if dependent.childcare_monthly_cost is not None:
            expenses.append(ChildcareExpense(
                f"{dependent.name} (child)", dependent.childcare_monthly_cost,
                dependent.school_monthly_cost, dependent.age,
                dependent.school_start_age, dependent.monthly_cost,
            ))
        else:
            expenses.append(AgeBasedExpense(
                f"{dependent.name} (child)", dependent.monthly_cost, dependent.age,
                {dependent.school_start_age: dependent.school_monthly_cost}
                if dependent.school_start_age is not None else {},
            ))
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
        investment.ownership_percentage = item.ownership_percentage
        investment.linked_loan_name = item.linked_loan_name
        investment.liability_share_percentage = item.liability_share_percentage
        investment.rental_income_name = item.rental_income_name
        investments.append(investment)
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
            property_purchase=resolved_property_purchase(config),
            selected_rent_expense=config.selected_rent_expense,
            assumptions=config.assumptions,
            curveballs=config.curveballs,
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
        "curveball_income_adjustments",
        "expense_values", "loan_balances", "loan_payments",
        "property_market_values", "property_owned_values",
        "property_debt_balances", "property_equity",
        "property_rental_income", "property_operating_costs",
    ):
        item_names = set().union(*(
            trajectory.details.get(detail_name, {}) for trajectory in trajectories
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
        average_details["purchase_events"] = aggregate_purchase_events(trajectories)
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
    if "curveball_events" in trajectories[0].details:
        average_details["curveball_events"] = [
            trajectory.details["curveball_events"]
            for trajectory in trajectories
        ]
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


def purchase_outcome_from_details(details):
    """Normalize scheduled and automatic purchase details for the results UI."""
    outcome = details.get("purchase_outcome")
    if outcome is not None:
        return outcome
    events = details.get("purchase_events", [])
    if not events:
        return None
    return {"purchased": True, "purchase_month": events[0]["month"]}


def automatic_purchase_warning(policy, not_purchased, trajectory_count, outcomes=None):
    """Explain a deferred automatic purchase without treating it as an error."""
    completion_outcomes = [outcome for outcome in outcomes or [] if outcome]
    unfinished = sum(
        outcome.get("completion_status")
        == "not_completed_before_simulation_end"
        for outcome in completion_outcomes
    )
    cancelled = sum(
        outcome.get("completion_status") == "cancelled"
        for outcome in completion_outcomes
    )
    if unfinished or cancelled:
        reasons = []
        never_eligible = max(0, not_purchased - unfinished - cancelled)
        if unfinished:
            reasons.append(
                f"{unfinished} remained scheduled or deferred when the simulation ended"
            )
        if cancelled:
            reasons.append(
                f"{cancelled} were cancelled after closing checks no longer passed"
            )
        if never_eligible:
            reasons.append(
                f"{never_eligible} never met the initial eligibility checks"
            )
        return (
            f"Automatic purchase did not complete in {not_purchased} of "
            f"{trajectory_count} trajectories: {'; '.join(reasons)}. "
            "Those simulations continued without the purchase."
        )
    cash_message = (
        "Automatic purchase was not affordable during the simulation in "
        f"{not_purchased} of {trajectory_count} trajectories."
    )
    failed_assessments = [
        outcome["affordability"]
        for outcome in outcomes or []
        if outcome and outcome.get("affordability")
        and not outcome["affordability"]["eligible"]
    ]
    if failed_assessments:
        example = failed_assessments[0]
        return (
            f"{cash_message} The mortgage affordability rule was not met in "
            f"{len(failed_assessments)} trajectory/trajectories; for example: "
            f"{example['reason']} Simulations continued without the purchase."
        )
    return (
        f"{cash_message} Modeled cash did not reach the required "
        f"${policy.minimum_required_cash:,.0f} after monthly commitments; those "
        "simulations continued without the purchase."
    )


def purchase_timing_explanation(outcome):
    """Describe eligibility, scheduled closing, and actual automatic outcome."""
    if not isinstance(outcome, dict) or outcome.get("first_eligible_month") is None:
        return None
    eligible_month = outcome["first_eligible_month"] + 1
    scheduled_month = outcome["initial_scheduled_completion_month"] + 1
    delay_months = outcome["completion_delay_months"]
    if outcome.get("purchased"):
        result = f"completed in month {outcome['purchase_month'] + 1}"
    elif outcome.get("completion_status") == "cancelled":
        result = "was cancelled when the closing recheck failed"
    elif outcome.get("completion_status") == "not_completed_before_simulation_end":
        result = "did not complete before the simulation ended"
    elif outcome.get("completion_status") == "deferred":
        result = f"is deferred to month {outcome['next_completion_month'] + 1}"
    else:
        result = outcome.get("completion_status", "did not complete").replace("_", " ")
    return (
        f"First eligible in month {eligible_month}; initial closing scheduled for "
        f"month {scheduled_month} after a {delay_months}-month delay; {result}. "
        f"Closing checks attempted {outcome.get('completion_attempts', 0)} time(s)."
    )


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


def select_representative_trajectories(trajectories):
    """Pick the lowest, median-ranked and highest actual runs by terminal net worth.

    Runs are ranked ascending by final net worth, ties broken by run number, so
    selection is deterministic.  For an even run count the median is the
    lower-middle ranked run.  Returns one entry per distinct run (in rank order)
    with the roles it fills; one or two runs therefore yield fewer entries.
    """
    if not trajectories:
        raise ValueError("at least one trajectory is required")
    ranked = sorted(trajectories, key=lambda t: (t.net_worth[-1], t.run_number))
    picks = (
        ("Lowest", 0),
        ("Median-ranked", (len(ranked) - 1) // 2),
        ("Highest", len(ranked) - 1),
    )
    entries = []
    for role, index in picks:
        trajectory = ranked[index]
        for entry in entries:
            if entry["trajectory"] is trajectory:
                entry["roles"].append(role)
                break
        else:
            entries.append({
                "trajectory": trajectory, "rank": index + 1, "roles": [role],
            })
    return entries


def monte_carlo_compare_explanation(simulation_count):
    """Explain that Compare shows actual runs rather than percentile curves."""
    return (
        f"**Compare runs:** these are actual simulated trajectories chosen from "
        f"{simulation_count} run(s) by final net worth (ties broken by run number; "
        "for an even count the median-ranked run is the lower-middle one). They are "
        "sample paths, not percentile curves: each line is one real run, and the "
        "month-by-month ranking of runs can differ from this final ranking."
    )


def _consume_run_request(session_state, button_clicked):
    """Combine a manual run click with the one-shot post-import run request."""
    run_after_import = bool(session_state.pop("run_after_import", False))
    return bool(button_clicked or run_after_import)


def build_simulation_events(config, repayment_dates, months_simulated,
                            purchase_events=None, curveball_events=None):
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
            event = {
                "month": purchase["month"] + 1,
                "label": purchase.get(
                    "label", f"{purchase['property_name']} purchased",
                ),
                "event_type": "Property purchase",
            }
            if "chart_label" in purchase:
                event["chart_label"] = purchase["chart_label"]
            events.append(event)
    flattened_curveballs = []
    for candidate in curveball_events or []:
        if isinstance(candidate, list):
            flattened_curveballs.extend(candidate)
        else:
            flattened_curveballs.append(candidate)
    seen_curveballs = set()
    for curveball in flattened_curveballs:
        if not curveball.get("triggered") or curveball.get("start_month") is None:
            continue
        month = curveball["start_month"]
        key = (curveball.get("name"), month)
        if month > months_simulated or key in seen_curveballs:
            continue
        seen_curveballs.add(key)
        events.append({
            "month": month,
            "label": f"{curveball.get('name', 'Curveball')} started",
            "event_type": "Curveball",
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
    for category, detail_name in (
        ("Property market value", "property_market_values"),
        ("Owned property value", "property_owned_values"),
        ("Property attributable debt", "property_debt_balances"),
        ("Property equity", "property_equity"),
    ):
        balances.extend(
            {"Category": category, "Item": name, "Amount": values[index]}
            for name, values in details.get(detail_name, {}).items()
        )

    flows = []
    for category, detail_name in (
        ("Gross monthly income rate", "income_rates"),
        ("Income received", "income_values"),
        ("Curveball income reduction", "curveball_income_adjustments"),
        ("Automatic investment contribution", "automatic_contributions"),
        ("Automatic contribution fee", "automatic_contribution_fees"),
        ("Expense paid", "expense_values"),
        ("Loan payment", "loan_payments"),
        ("Property rental income (linked)", "property_rental_income"),
        ("Property operating costs (linked)", "property_operating_costs"),
    ):
        flows.extend(
            {"Category": category, "Item": name, "Amount": values[index]}
            for name, values in details.get(detail_name, {}).items()
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
            "curveball_income_adjustments": {
                name: values[index]
                for name, values in details.get("curveball_income_adjustments", {}).items()
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
            "property_positions": {
                name: {
                    "market_value": details["property_market_values"][name][index],
                    "owned_value": details["property_owned_values"][name][index],
                    "attributable_debt": details["property_debt_balances"][name][index],
                    "equity": details["property_equity"][name][index],
                    "rental_income": details["property_rental_income"][name][index],
                    "operating_costs": details["property_operating_costs"][name][index],
                }
                for name in details.get("property_market_values", {})
            },
        }
        for index, value in enumerate(net_worth)
    ]


def _serialized_trajectory(trajectory):
    serialized = {
        "run_number": trajectory.run_number,
        "seed": trajectory.seed,
        "monthly": _monthly_results(trajectory.net_worth, trajectory.details),
        "loan_repayments": [
            {"name": name, "month": month_index + 1}
            for name, month_index in trajectory.repayment_dates.items()
        ],
    }
    if "purchase_fees" in trajectory.details:
        serialized["purchase_fees"] = trajectory.details["purchase_fees"]
        serialized["purchase_events"] = trajectory.details.get("purchase_events", [])
    if "purchase_outcome" in trajectory.details:
        serialized["purchase_outcome"] = trajectory.details["purchase_outcome"]
    if "curveball_events" in trajectory.details:
        serialized["curveball_events"] = trajectory.details["curveball_events"]
    return serialized


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
                config, repayment_dates, len(net_worth), details.get("purchase_events"),
                details.get("curveball_events"),
            ),
        },
    }
    if "purchase_fees" in details:
        export["results"]["purchase_fees"] = details["purchase_fees"]
        export["results"]["purchase_events"] = details.get("purchase_events", [])
    if "purchase_outcome" in details:
        export["results"]["purchase_outcome"] = details["purchase_outcome"]
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


def _scenario_parameters_document(config):
    """Return a results-free document accepted by the existing config reader."""
    features = {
        "automatic_investment_contributions": AUTO_INVEST_EXPORT_VERSION,
        "financial_assumptions": FINANCIAL_ASSUMPTIONS_EXPORT_VERSION,
    }
    if config.property_purchase is not None:
        features["property_purchase"] = 1
    return {
        "format": "financial-simulator-export",
        "schema_version": 1,
        "features": features,
        "parameters": asdict(config),
    }


def create_scenario_workspace_export(workspace, exported_at=None):
    """Serialize named configuration snapshots without simulation results."""
    if not isinstance(workspace, ScenarioWorkspace):
        raise ValueError("A valid scenario workspace is required")
    exported_at = exported_at or datetime.now(timezone.utc)
    document = {
        "format": "financial-simulator-scenario-workspace",
        "schema_version": SCENARIO_WORKSPACE_EXPORT_VERSION,
        "exported_at_utc": exported_at.astimezone(timezone.utc).isoformat(),
        "active_variant": workspace.active_id,
        "variants": [
            {
                "id": variant.id,
                "name": variant.name,
                "scenario": _scenario_parameters_document(variant.config),
            }
            for variant in workspace.variants
        ],
    }
    return json.dumps(document, indent=2, ensure_ascii=False)


def read_scenario_workspace_export(data):
    """Validate and restore the additive A/B workspace format."""
    try:
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        document = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as error:
        raise ValueError("The selected workspace is not valid UTF-8 JSON.") from error
    if (not isinstance(document, dict)
            or document.get("format") != "financial-simulator-scenario-workspace"):
        raise ValueError("This is not a Financial Simulator scenario workspace.")
    if document.get("schema_version") != SCENARIO_WORKSPACE_EXPORT_VERSION:
        raise ValueError("This scenario workspace version is not supported.")
    variants = document.get("variants")
    if not isinstance(variants, list) or len(variants) != 2:
        raise ValueError("A scenario workspace must contain exactly two variants.")
    restored = []
    for item in variants:
        if not isinstance(item, dict) or not isinstance(item.get("scenario"), dict):
            raise ValueError("A scenario workspace variant is invalid.")
        try:
            config = read_simulation_export(json.dumps(item["scenario"]))
            restored.append(ScenarioVariant(item.get("id"), item.get("name"), config))
        except (TypeError, ValueError) as error:
            raise ValueError(f"A scenario workspace variant is invalid: {error}") from error
    if {item.id for item in restored} != {BASELINE_ID, ALTERNATIVE_ID}:
        raise ValueError("The workspace must contain Baseline A and Alternative B.")
    try:
        return ScenarioWorkspace(restored, document.get("active_variant"))
    except ValueError as error:
        raise ValueError(f"The scenario workspace is invalid: {error}") from error


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
                    values.setdefault("ownership_percentage", 100.0)
                    values.setdefault("linked_loan_name", None)
                    values.setdefault("liability_share_percentage", 100.0)
                    values.setdefault("rental_income_name", None)
                    values.setdefault("jurisdiction_profile_name", None)
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
    parameters["curveballs"] = rebuild_collection("curveballs", CurveballConfig) or []
    profile_values = parameters.get("jurisdiction_profiles", [])
    if (not isinstance(profile_values, list)
            or not all(isinstance(item, dict) for item in profile_values)):
        raise ValueError("The exported jurisdiction profiles must be a list.")
    try:
        parameters["jurisdiction_profiles"] = [
            PropertyJurisdictionProfile(**item) for item in profile_values
        ]
        profiles_by_name(parameters["jurisdiction_profiles"])
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"The exported jurisdiction profiles are invalid: {error}"
        ) from error
    parameters.setdefault("property_purchase_jurisdiction_profile", None)
    parameters.setdefault("purchase_cadastral_income", None)
    parameters.setdefault("purchase_assessed_value", None)
    purchase = parameters.get("property_purchase")
    if purchase is not None:
        if not isinstance(purchase, dict):
            raise ValueError("The exported property purchase must be an object.")
        try:
            if "purchase" in purchase:
                template = purchase["purchase"]
                if not isinstance(template, dict):
                    raise ValueError("automatic purchase template must be an object")
                affordability = purchase.get("mortgage_affordability")
                if affordability is not None:
                    if not isinstance(affordability, dict):
                        raise ValueError(
                            "mortgage affordability policy must be an object"
                        )
                    affordability = dict(affordability)
                    # A short-lived pre-merge format exposed bonus inclusion.
                    # Ignore it safely: bonuses are never qualifying income.
                    affordability.pop("bonus_income_inclusion_pct", None)
                    qualifying_names = affordability.get(
                        "qualifying_income_names", ()
                    )
                    if not isinstance(qualifying_names, (list, tuple)):
                        raise ValueError(
                            "qualifying income names must be a list"
                        )
                    affordability["qualifying_income_names"] = tuple(qualifying_names)
                    affordability = MortgageAffordabilityPolicy(**affordability)
                completion_delay = purchase.get("completion_delay")
                if completion_delay is not None:
                    if not isinstance(completion_delay, dict):
                        raise ValueError(
                            "purchase completion delay policy must be an object"
                        )
                    completion_delay = PurchaseCompletionDelayPolicy(
                        **completion_delay
                    )
                parameters["property_purchase"] = AutomaticPurchasePolicy(
                    PropertyPurchase(**template),
                    purchase["minimum_available_cash"],
                    purchase["minimum_cash_reserve"],
                    affordability,
                    completion_delay,
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
    active_workspace = st.session_state.get("scenario_workspace")
    if isinstance(active_workspace, ScenarioWorkspace):
        st.info(
            f"A/B workspace · editing **{active_workspace.active_variant.name}**. "
            "The other scenario is stored as an independent configuration snapshot."
        )
    workspace_notice = st.session_state.pop("workspace_success_notice", None)
    for import_warning in st.session_state.pop("import_warnings", []):
        st.warning(import_warning)

    st.session_state.setdefault("additional_expenses", [])
    st.session_state.setdefault("dependents", [])
    st.session_state.setdefault("curveballs", [])
    st.session_state.setdefault("investments", [{
        "name": "Portfolio", "investment_type": "stock",
        "initial_amount": 1000.0, "monthly_contribution": 100.0,
        "monthly_expenses": 0.0, "yearly_tax": 0.0,
        "auto_invest_enabled": False, "auto_invest_min_cash": 10000.0,
        "auto_invest_percentage": 10.0, "auto_invest_max_per_year": 3,
        "ownership_percentage": 100.0, "linked_loan_name": None,
        "liability_share_percentage": 100.0,
        "rental_income_name": None,
        "jurisdiction_profile_name": None,
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
    st.session_state.setdefault("jurisdiction_profiles", [
        {**asdict(profile), "_id": uuid.uuid4().hex}
        for profile in default_property_jurisdiction_profiles()
    ])
    st.session_state.setdefault("purchase_jurisdiction_profile", None)
    st.session_state.setdefault("purchase_cadastral_income", 0.0)
    st.session_state.setdefault("purchase_assessed_value", 0.0)
    st.session_state.setdefault("purchase_enabled", False)
    st.session_state.setdefault("purchase_mode", "Automatic — when affordable")
    st.session_state.setdefault("purchase_min_cash", 10000.0)
    st.session_state.setdefault("purchase_reserve", 0.0)
    st.session_state.setdefault("purchase_affordability_enabled", False)
    st.session_state.setdefault("purchase_affordability_ratio", 33.0)
    st.session_state.setdefault("purchase_qualifying_incomes", ["Salary"])
    st.session_state.setdefault("purchase_variable_income_pct", 0.0)
    st.session_state.setdefault("purchase_base_living_buffer", 0.0)
    st.session_state.setdefault("purchase_adult_count", 1)
    st.session_state.setdefault("purchase_buffer_per_adult", 0.0)
    st.session_state.setdefault("purchase_dependent_count", 0)
    st.session_state.setdefault("purchase_buffer_per_dependent", 0.0)
    st.session_state.setdefault("purchase_delay_enabled", False)
    st.session_state.setdefault("purchase_delay_mode", "Fixed")
    st.session_state.setdefault("purchase_fixed_delay_months", 0)
    st.session_state.setdefault("purchase_random_delay_min", 1)
    st.session_state.setdefault("purchase_random_delay_max", 3)
    st.session_state.setdefault("purchase_delay_failure_policy", "retry")
    st.session_state.setdefault("selected_rent_expense", None)
    st.session_state.setdefault("scenario_workspace", None)
    st.session_state.setdefault("workspace_import_counter", 0)
    st.session_state.setdefault("active_result_mode", "single")
    st.session_state.setdefault("purchase_fields", {
        "purchase_price": 250000.0, "down_payment_percentage": 20.0,
        "transaction_fees": 5000.0, "mortgage_interest_rate": 4.0,
        "mortgage_duration_years": 25, "purchase_month": 0,
        "property_name": "Purchased property", "mortgage_name": "Property mortgage",
        "fixed_rate": False, "monthly_expenses": 0.0, "yearly_tax": 0.0,
    })
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
                item.setdefault("ownership_percentage", 100.0)
                item.setdefault("linked_loan_name", None)
                item.setdefault("liability_share_percentage", 100.0)
                item.setdefault("rental_income_name", None)
                item.setdefault("jurisdiction_profile_name", None)
    for profile in st.session_state.jurisdiction_profiles:
        profile.setdefault("_id", uuid.uuid4().hex)

    def item_key(item):
        return item["_id"]

    def configured_jurisdiction_profiles():
        return [
            PropertyJurisdictionProfile(**{
                key: value for key, value in profile.items() if key != "_id"
            })
            for profile in st.session_state.jurisdiction_profiles
            if profile.get("name", "").strip()
        ]

    def jurisdiction_profile_from_state(name):
        values = next((
            profile for profile in st.session_state.jurisdiction_profiles
            if profile.get("name") == name
        ), None)
        if values is None:
            return None
        try:
            return PropertyJurisdictionProfile(**{
                key: value for key, value in values.items() if key != "_id"
            })
        except ValueError:
            return None

    def investment_jurisdiction_estimate(item):
        profile = jurisdiction_profile_from_state(
            item.get("jurisdiction_profile_name")
        )
        if profile is None or item.get("investment_type") != "real_estate":
            return None
        gross_rent = next((
            income["monthly_amount"] for income in st.session_state.incomes
            if income["name"] == item.get("rental_income_name")
        ), 0.0)
        return estimate_property_costs(
            profile, item["initial_amount"], gross_rent,
            item.get("ownership_percentage", 100.0),
        )

    def editable_items(items, money_fields=()):
        editable = []
        for item in items:
            values = asdict(item)
            for field_name in money_fields:
                values[field_name] = float(_whole_amount(values[field_name]))
            editable.append({**values, "_id": uuid.uuid4().hex})
        return editable

    def import_config(imported, run_after_load=True):
        st.session_state.scenario_years = imported.years
        st.session_state.scenario_initial_cash = float(_whole_amount(imported.initial_cash))
        st.session_state.scenario_yearly_fee = float(_whole_amount(imported.yearly_fee))
        st.session_state.financial_assumptions = asdict(imported.assumptions)
        st.session_state.jurisdiction_profiles = [
            {**asdict(profile), "_id": uuid.uuid4().hex}
            for profile in imported.jurisdiction_profiles
        ]
        st.session_state.purchase_jurisdiction_profile = (
            imported.property_purchase_jurisdiction_profile
        )
        st.session_state.purchase_cadastral_income = (
            imported.purchase_cadastral_income or 0.0
        )
        st.session_state.purchase_assessed_value = (
            imported.purchase_assessed_value or 0.0
        )
        purchase = imported.property_purchase
        st.session_state.purchase_enabled = purchase is not None
        st.session_state.purchase_mode = (
            "Automatic — when affordable" if isinstance(purchase, AutomaticPurchasePolicy)
            else "Scheduled — in a selected month"
        )
        st.session_state.purchase_affordability_enabled = False
        st.session_state.purchase_delay_enabled = False
        template = purchase.purchase if isinstance(purchase, AutomaticPurchasePolicy) else purchase
        if template is not None:
            st.session_state.purchase_fields = asdict(template)
            if isinstance(purchase, AutomaticPurchasePolicy):
                st.session_state.purchase_min_cash = purchase.minimum_available_cash
                st.session_state.purchase_reserve = purchase.minimum_cash_reserve
                affordability = purchase.mortgage_affordability
                st.session_state.purchase_affordability_enabled = affordability is not None
                if affordability is not None:
                    st.session_state.purchase_affordability_ratio = (
                        affordability.max_debt_service_ratio_pct
                    )
                    st.session_state.purchase_qualifying_incomes = list(
                        affordability.qualifying_income_names
                    )
                    st.session_state.purchase_variable_income_pct = (
                        affordability.variable_income_inclusion_pct
                    )
                    st.session_state.purchase_base_living_buffer = (
                        affordability.base_monthly_living_buffer
                    )
                    st.session_state.purchase_adult_count = affordability.adult_count
                    st.session_state.purchase_buffer_per_adult = (
                        affordability.monthly_buffer_per_adult
                    )
                    st.session_state.purchase_dependent_count = (
                        affordability.dependent_count
                    )
                    st.session_state.purchase_buffer_per_dependent = (
                        affordability.monthly_buffer_per_dependent
                    )
                completion_delay = purchase.completion_delay
                st.session_state.purchase_delay_enabled = completion_delay is not None
                if completion_delay is not None:
                    is_fixed_delay = (
                        completion_delay.minimum_months
                        == completion_delay.maximum_months
                    )
                    st.session_state.purchase_delay_mode = (
                        "Fixed" if is_fixed_delay else "Bounded random"
                    )
                    st.session_state.purchase_fixed_delay_months = (
                        completion_delay.minimum_months
                    )
                    st.session_state.purchase_random_delay_min = (
                        completion_delay.minimum_months
                    )
                    st.session_state.purchase_random_delay_max = (
                        completion_delay.maximum_months
                    )
                    st.session_state.purchase_delay_failure_policy = (
                        completion_delay.on_ineligible
                    )
        for key in (
            "purchase_price", "purchase_down_pct", "purchase_fees", "purchase_rate",
            "purchase_term", "purchase_fixed", "purchase_monthly_cost",
            "purchase_yearly_tax", "purchase_threshold", "purchase_reserve_input",
            "purchase_month", "purchase_affordability_toggle",
            "purchase_affordability_ratio_input", "purchase_qualifying_income_input",
            "purchase_variable_income_input",
            "purchase_base_buffer_input", "purchase_adult_count_input",
            "purchase_adult_buffer_input", "purchase_dependent_count_input",
            "purchase_dependent_buffer_input",
            "purchase_delay_toggle", "purchase_delay_mode_input",
            "purchase_fixed_delay_input", "purchase_random_delay_min_input",
            "purchase_random_delay_max_input", "purchase_delay_failure_input",
            "purchase_rent",
            "purchase_jurisdiction_profile_input",
            "purchase_cadastral_income_input", "purchase_assessed_value_input",
        ):
            st.session_state.pop(key, None)
        st.session_state.selected_rent_expense = imported.selected_rent_expense
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
        for item, dependent in zip(st.session_state.dependents, imported.dependents):
            item["childcare_monthly_cost"] = dependent.childcare_monthly_cost
        st.session_state.curveballs = [
            {**asdict(item), "probability": item.probability * 100,
             "_id": uuid.uuid4().hex}
            for item in imported.curveballs
        ]
        st.session_state.active_card = None
        st.session_state.pop("simulation_result", None)
        if run_after_load:
            st.session_state.run_after_import = True
        else:
            st.session_state.pop("run_after_import", None)

    def switch_scenario_variant():
        workspace = st.session_state.get("scenario_workspace")
        selected = st.session_state.get("scenario_variant_selector")
        if (not isinstance(workspace, ScenarioWorkspace)
                or selected == workspace.active_id):
            return
        workspace = workspace.activate(selected)
        st.session_state.scenario_workspace = workspace
        import_config(workspace.config_for(selected), run_after_load=False)

    def reset_alternative_scenario():
        workspace = st.session_state.get("scenario_workspace")
        if not isinstance(workspace, ScenarioWorkspace):
            return
        workspace = workspace.reset_from(ALTERNATIVE_ID, BASELINE_ID)
        st.session_state.scenario_workspace = workspace
        if workspace.active_id == ALTERNATIVE_ID:
            import_config(workspace.config_for(ALTERNATIVE_ID), run_after_load=False)
        st.session_state.pop("simulation_result", None)
        st.session_state.pop("scenario_comparison_result", None)

    def import_scenario_workspace():
        uploaded = st.session_state.get(
            "scenario_workspace_file_"
            f"{st.session_state.workspace_import_counter}"
        )
        if uploaded is None:
            return
        try:
            imported_workspace = read_scenario_workspace_export(uploaded.getvalue())
        except ValueError as error:
            st.session_state.workspace_import_error = str(error)
            return
        st.session_state.scenario_workspace = imported_workspace
        for key in (
            "scenario_baseline_name", "scenario_alternative_name",
            "scenario_variant_selector",
        ):
            st.session_state.pop(key, None)
        import_config(imported_workspace.active_variant.config, run_after_load=False)
        st.session_state.pop("scenario_comparison_result", None)
        st.session_state.workspace_import_counter += 1
        st.session_state.workspace_success_notice = "A/B scenario workspace imported."

    def import_single_scenario():
        """Import parameters from a widget callback before the next widgets render."""
        uploaded = st.session_state.get(
            "scenario_import_file_"
            f"{st.session_state.get('import_uploader_counter', 0)}"
        )
        if uploaded is None:
            return
        try:
            imported, import_warnings = read_simulation_export(
                uploaded.getvalue(), include_warnings=True,
            )
        except ValueError as error:
            st.session_state.import_error = str(error)
            return
        import_config(imported)
        st.session_state.import_warnings = import_warnings
        st.session_state.import_uploader_counter = (
            st.session_state.get("import_uploader_counter", 0) + 1
        )
        st.session_state.import_success_notice = (
            f"Scenario imported from {uploaded.name}."
        )

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
            "name": f"Child {number}", "monthly_cost": 0.0, "childcare_monthly_cost": 300.0,
            "age": 0,
            "school_start_age": 6, "school_monthly_cost": 250.0,
            "_id": uuid.uuid4().hex,
        })

    def add_curveball():
        number = len(st.session_state.curveballs) + 1
        st.session_state.curveballs.append({
            "name": f"Curveball {number}", "enabled": False,
            "probability": 25.0, "start_month_min": 1,
            "start_month_max": max(1, int(st.session_state.get("scenario_years", 10)) * 12),
            "duration_months": 3, "affected_income_name": None,
            "income_reduction_pct": 100.0, "recovery_months": 3,
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
            "ownership_percentage": 100.0, "linked_loan_name": None,
            "liability_share_percentage": 100.0,
            "rental_income_name": None,
            "jurisdiction_profile_name": None,
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

    def add_jurisdiction_profile():
        st.session_state.jurisdiction_profiles.append({
            "name": f"Custom jurisdiction {len(st.session_state.jurisdiction_profiles) + 1}",
            "acquisition_cost_percentage": 0.0,
            "annual_property_tax_percentage": 0.0,
            "annual_maintenance_percentage": 0.0,
            "rental_expense_percentage": 0.0,
            "_id": uuid.uuid4().hex,
        })

    @st.dialog("Jurisdiction presets: assumptions, sources and limitations",
               width="large")
    def show_jurisdiction_help():
        st.markdown(
            "**Dated planning estimates, not legal or tax advice.** Preset "
            "values come from the official pages cited below (research dated "
            "2026-10-31) and may be outdated or incomplete. A preset covers one "
            "transaction scope only; reliefs, eligibility, municipal surcharges, "
            "agent and lender fees are not modelled unless listed.\n\n"
            "- **Statutory** values are rates/fees from the cited official page.\n"
            "- **Indicative allowance** values are budgeting guesses, not law.\n"
            "- **User assumption** values are yours, including any edited value.\n"
            "- *Source-checked* applies per component, never to the whole "
            "profile; profiles with omissions are at most *partially verified*.\n"
            "- Annual property tax depends on assessed/cadastral values and "
            "municipal rates: enter it yourself. Zero does **not** mean tax-free.\n"
            "- Maintenance is a planning assumption, not a tax. Rental costs "
            "depend on your management choices.\n"
            "- Do not repeat a cost in both a profile and the fixed-amount "
            "fields; profile costs are added on top of them.\n"
            "- Saved scenarios keep their own copy (with version); newer catalog "
            "versions never overwrite them."
        )
        for profile in configured_jurisdiction_profiles():
            with st.expander(profile.name):
                st.markdown(describe_profile_assumptions(profile))

    def render_jurisdiction_profiles():
        if st.button("? Assumptions, sources and limitations",
                     key="jurisdiction_help_button", use_container_width=True):
            show_jurisdiction_help()
        st.caption(
            "Dated planning estimates—not tax, legal, lending, or valuation advice. "
            "Profiles add costs to the explicit fixed amounts entered on each property; "
            "do not enter the same cost in both places."
        )
        catalog = {
            item.name: item for item in default_property_jurisdiction_profiles()
        }
        existing_names = {
            profile["name"] for profile in st.session_state.jurisdiction_profiles
        }
        for notice in preset_update_notices(configured_jurisdiction_profiles()):
            st.warning(notice)
        missing = [name for name in catalog if name not in existing_names]
        if missing:
            chosen = st.selectbox(
                "Catalog preset to add", missing, key="jurisdiction_catalog_choice",
            )
            if st.button("＋ Add catalog preset", use_container_width=True):
                st.session_state.jurisdiction_profiles.append({
                    **asdict(catalog[chosen]), "_id": uuid.uuid4().hex,
                })
                st.rerun()
        if st.button("＋ Add jurisdiction profile", use_container_width=True):
            add_jurisdiction_profile()
            st.rerun()
        for index, profile in enumerate(st.session_state.jurisdiction_profiles):
            profile_key = profile["_id"]
            with st.container(border=True):
                profile["name"] = st.text_input(
                    "Profile name", profile["name"],
                    key=f"jurisdiction_name_{profile_key}",
                )
                if profile.get("profile_id"):
                    st.caption(
                        f"Catalog preset `{profile['profile_id']}` v{profile.get('version', 1)} · "
                        f"{profile.get('verification_status', 'unverified')} · "
                        f"data as of {profile.get('data_as_of') or 'n/a'}"
                        + (f" · [source]({profile['source_url']})"
                           if profile.get("source_url") else "")
                    )
                    if profile.get("verification_status") != "verified":
                        st.warning(
                            "Rates are not verified against a primary source; "
                            "confirm before relying on them."
                        )
                    if profile.get("transaction_scope"):
                        st.caption(f"Covers: {profile['transaction_scope']}")
                    if profile.get("unsupported_cases"):
                        st.warning(f"Not covered: {profile['unsupported_cases']}")
                    if profile.get("notes"):
                        st.caption(profile["notes"])

                def rate_input(label, field, basis_field, key, max_value=100.0):
                    before = float(profile.get(field, 0.0))
                    basis = profile.get(basis_field, "user")
                    value = st.number_input(
                        f"{label} · {_BASIS_LABELS[basis]}",
                        min_value=0.0, max_value=max_value, value=before,
                        key=f"jurisdiction_{key}_{profile_key}",
                    )
                    profile[field] = value
                    if value != before:
                        profile[basis_field] = "user"

                for c_index, component in enumerate(
                        profile.get("acquisition_components", [])):
                    ckey = f"{profile_key}_{c_index}"
                    overridden = component.get("override_percentage") is not None
                    basis = "user" if overridden else component.get("basis", "user")
                    checked = (component.get("verified") and not overridden)
                    st.markdown(
                        f"**{component['label']}** · {_BASIS_LABELS[basis]} · "
                        + ("source-checked" if checked else "not source-checked")
                    )
                    if component["method"] == "fixed":
                        amount = st.number_input(
                            "Fixed amount ($)", min_value=0.0, max_value=1e9,
                            value=float(component["amount"]),
                            key=f"jurisdiction_component_amount_{ckey}",
                        )
                        if amount != component["amount"]:
                            component["amount"] = amount
                            component["basis"] = "user"
                            component["verified"] = False
                        continue
                    if component.get("note"):
                        st.caption(component["note"])
                    use_override = st.checkbox(
                        "Override with my own flat % of the tax base",
                        value=overridden, key=f"jurisdiction_component_toggle_{ckey}",
                    )
                    if use_override:
                        component["override_percentage"] = st.number_input(
                            "Own rate (% of base)", min_value=0.0, max_value=100.0,
                            value=float(component.get("override_percentage") or 0.0),
                            key=f"jurisdiction_component_override_{ckey}",
                        )
                    else:
                        component["override_percentage"] = None
                        if component["method"] == "rate":
                            st.caption(f"{component['rate_percentage']:g}% of the base")
                        else:
                            st.caption("Banded rates; see the help popup for the bands.")
                rate_input(
                    "Acquisition costs: transfer taxes (% of full purchase price)",
                    "acquisition_cost_percentage", "acquisition_cost_basis",
                    "acquisition",
                )
                rate_input(
                    "Other transaction costs (% of full purchase price)",
                    "other_transaction_cost_percentage",
                    "other_transaction_cost_basis", "other_transaction",
                )
                rate_input(
                    "Fixed acquisition fees ($)",
                    "fixed_acquisition_costs", "fixed_acquisition_cost_basis",
                    "fixed_acquisition", max_value=1e9,
                )
                tax_base = profile.get("property_tax_base", "market_value")
                rate_input(
                    "Annual property tax (% of owned starting value)",
                    "annual_property_tax_percentage", "property_tax_basis", "tax",
                )
                if tax_base == "assessed_value":
                    st.caption(
                        "This tax is levied on an assessed/cadastral value, not market "
                        "value; the simulator applies the rate to the property value "
                        "you enter, so adjust the rate to your assessed base."
                    )
                else:
                    st.caption("This tax base is the market value you enter.")
                rate_input(
                    "Annual maintenance (% of owned starting value)",
                    "annual_maintenance_percentage", "maintenance_basis",
                    "maintenance",
                )
                rate_input(
                    "Rental operating costs (% of gross monthly rent)",
                    "rental_expense_percentage", "rental_expense_basis", "rental",
                )
                if st.button("Remove profile", key=f"remove_jurisdiction_{profile_key}"):
                    remove_item(st.session_state.jurisdiction_profiles, index)
                    st.rerun()

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
                        owned_amount = (
                            item["initial_amount"]
                            * item.get("ownership_percentage", 100) / 100
                        )
                        profile_estimate = investment_jurisdiction_estimate(item)
                        estimated_monthly = (
                            profile_estimate.monthly_operating_costs
                            if profile_estimate else 0
                        )
                        estimated_tax = (
                            profile_estimate.annual_property_tax
                            if profile_estimate else 0
                        )
                        st.caption(
                            f"real estate · value ${item['initial_amount']:,.0f} · "
                            f"owned {item.get('ownership_percentage', 100):g}% "
                            f"(${owned_amount:,.0f}) · "
                            f"expenses ${item['monthly_expenses'] + estimated_monthly:,.0f}/month · "
                            f"tax ${item['yearly_tax'] + estimated_tax:,.0f}/year"
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
                    (
                        "Current full property value"
                        if item["investment_type"] == "real_estate"
                        else "Initial amount"
                    ),
                    min_value=0.0, value=float(item["initial_amount"]),
                    step=100.0, format="%.0f", key=f"investment_initial_{item_key(item)}",
                )
                if item["investment_type"] == "real_estate":
                    item["ownership_percentage"] = st.number_input(
                        "Ownership share (%)", min_value=0.01, max_value=100.0,
                        value=float(item.get("ownership_percentage", 100.0)),
                        step=1.0, key=f"investment_ownership_{item_key(item)}",
                        help=(
                            "Net worth includes only this share of the full property "
                            "market value."
                        ),
                    )
                    loan_options = [None] + [
                        loan["name"] for loan in st.session_state.loans
                    ]
                    current_loan = (
                        item.get("linked_loan_name")
                        if item.get("linked_loan_name") in loan_options else None
                    )
                    item["linked_loan_name"] = st.selectbox(
                        "Mortgage / loan linked to this property",
                        loan_options,
                        index=loan_options.index(current_loan),
                        format_func=lambda value: "No linked loan" if value is None else value,
                        key=f"investment_linked_loan_{item_key(item)}",
                        help=(
                            "The attributable share of the linked outstanding balance is "
                            "subtracted once globally from net worth."
                        ),
                    )
                    item["liability_share_percentage"] = st.number_input(
                        "Your share of the linked loan (%)", min_value=0.01,
                        max_value=100.0,
                        value=float(item.get("liability_share_percentage", 100.0)),
                        step=1.0, key=f"investment_liability_share_{item_key(item)}",
                        disabled=item["linked_loan_name"] is None,
                        help=(
                            "Portion of the linked loan balance attributable to you. "
                            "Loan payments entered remain your full cashflow."
                        ),
                    )
                    rent_options = [None] + [
                        income["name"] for income in st.session_state.incomes
                        if income["income_type"] == "rent"
                    ]
                    current_rent = (
                        item.get("rental_income_name")
                        if item.get("rental_income_name") in rent_options else None
                    )
                    item["rental_income_name"] = st.selectbox(
                        "Rental income linked to this property",
                        rent_options,
                        index=rent_options.index(current_rent),
                        format_func=lambda value: (
                            "No linked rental income" if value is None else value
                        ),
                        key=f"investment_rental_income_{item_key(item)}",
                        help=(
                            "Links an existing rent income source for reporting; it does "
                            "not create or deposit a second income. Enter that source as "
                            "your attributable share—it is not scaled again here."
                        ),
                    )
                    profile_options = [None] + [
                        profile["name"]
                        for profile in st.session_state.jurisdiction_profiles
                        if profile["name"]
                    ]
                    current_profile = (
                        item.get("jurisdiction_profile_name")
                        if item.get("jurisdiction_profile_name") in profile_options
                        else None
                    )
                    item["jurisdiction_profile_name"] = st.selectbox(
                        "Property jurisdiction estimate profile",
                        profile_options,
                        index=profile_options.index(current_profile),
                        format_func=lambda value: (
                            "No jurisdiction estimates" if value is None else value
                        ),
                        key=f"investment_jurisdiction_{item_key(item)}",
                        help=(
                            "Adds the selected profile's value- and rent-based estimates "
                            "to the fixed costs below."
                        ),
                    )
                    estimate = investment_jurisdiction_estimate(item)
                    if estimate is not None:
                        st.caption(
                            f"Profile estimate at starting values: "
                            f"${estimate.monthly_operating_costs:,.0f}/month operating "
                            f"costs and ${estimate.annual_property_tax:,.0f}/year tax. "
                            "Acquisition costs apply only to future purchases."
                        )
                        selected = jurisdiction_profile_from_state(
                            item.get("jurisdiction_profile_name"))
                        if selected is not None and selected.unsupported_cases:
                            st.warning(f"Not covered: {selected.unsupported_cases}")
                    item["monthly_expenses"] = st.number_input(
                        "Additional fixed monthly property expenses (your share)",
                        min_value=0.0,
                        value=float(item["monthly_expenses"]), step=50.0, format="%.0f",
                        key=f"investment_expenses_{item_key(item)}",
                        help="Maintenance, insurance, management, and similar recurring costs.",
                    )
                    item["yearly_tax"] = st.number_input(
                        "Additional fixed yearly property tax (your share)", min_value=0.0,
                        value=float(item["yearly_tax"]), step=100.0, format="%.0f",
                        key=f"investment_tax_{item_key(item)}",
                    )
                    st.caption(
                        "Fixed amounts are already attributable to your share. Profile "
                        "value-based estimates are scaled by ownership; linked gross rent "
                        "is already your share and is not scaled twice."
                    )
                else:
                    item["ownership_percentage"] = 100.0
                    item["linked_loan_name"] = None
                    item["liability_share_percentage"] = 100.0
                    item["rental_income_name"] = None
                    item["jurisdiction_profile_name"] = None
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
        st.caption("Add the people and lifecycle costs that shape this scenario.")
        if st.button("＋ Add daughter / child", use_container_width=True):
            add_dependent()
        for index, item in enumerate(st.session_state.dependents):
            with st.container(border=True):
                if not card_is_open("dependent", item):
                    st.caption(
                        f"age {item.get('age', 0)} · childcare ${item.get('childcare_monthly_cost') or item.get('monthly_cost', 0):,.0f}/month"
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
                    "Other monthly child cost", min_value=0.0, value=float(item["monthly_cost"]),
                    step=25.0, format="%.0f", key=f"dependent_cost_{item_key(item)}",
                )
                if item.get("childcare_monthly_cost") is None:
                    st.caption(
                        "Legacy child-cost behavior is preserved for this imported "
                        "scenario; add a new child to configure childcare explicitly."
                    )
                else:
                    item["childcare_monthly_cost"] = st.number_input(
                        "Childcare monthly cost (until school)", min_value=0.0,
                        value=float(item["childcare_monthly_cost"]), step=25.0,
                        format="%.0f", key=f"dependent_childcare_{item_key(item)}",
                        help=(
                            "This cost stops at the existing school-start milestone; "
                            "school cost begins in the following month."
                        ),
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

    def render_curveballs():
        st.caption(
            "Optional adverse events are sampled independently per trajectory. "
            "Disabled events consume no random draws and never trigger."
        )
        if st.button("＋ Add curveball", use_container_width=True):
            add_curveball()
        income_options = ["All incomes"] + [item["name"] for item in st.session_state.incomes]
        max_month = max(1, int(st.session_state.get("scenario_years", 10)) * 12)
        for index, item in enumerate(st.session_state.curveballs):
            with st.container(border=True):
                if not card_is_open("curveball", item):
                    state = "on" if item.get("enabled") else "off"
                    st.caption(
                        f"{state} · {item.get('probability', 0):.0f}% chance · "
                        f"{item.get('duration_months', 0)} months"
                    )
                    continue
                item["name"] = st.text_input(
                    "Name", item["name"], key=f"curveball_name_{item_key(item)}"
                )
                item["enabled"] = st.checkbox(
                    "Enabled", value=bool(item.get("enabled", False)),
                    key=f"curveball_enabled_{item_key(item)}",
                )
                item["probability"] = st.number_input(
                    "Probability per trajectory (%)", min_value=0.0, max_value=100.0,
                    value=float(item.get("probability", 0.0)), step=5.0,
                    key=f"curveball_probability_{item_key(item)}",
                )
                current_target = item.get("affected_income_name")
                target_label = (
                    current_target if current_target in income_options else "All incomes"
                )
                selected_target = st.selectbox(
                    "Affected income", income_options,
                    index=income_options.index(target_label),
                    key=f"curveball_income_{item_key(item)}",
                )
                item["affected_income_name"] = (
                    None if selected_target == "All incomes" else selected_target
                )
                start_left, start_right = st.columns(2)
                item["start_month_min"] = start_left.number_input(
                    "Earliest start month", min_value=1, max_value=max_month,
                    value=min(max_month, int(item.get("start_month_min", 1))), step=1,
                    key=f"curveball_start_min_{item_key(item)}",
                )
                item["start_month_max"] = start_right.number_input(
                    "Latest start month", min_value=1, max_value=max_month,
                    value=min(max_month, int(item.get("start_month_max", max_month))), step=1,
                    key=f"curveball_start_max_{item_key(item)}",
                )
                item["duration_months"] = st.number_input(
                    "Income-reduction duration (months)", min_value=1,
                    value=int(item.get("duration_months", 3)), step=1,
                    key=f"curveball_duration_{item_key(item)}",
                )
                item["income_reduction_pct"] = st.number_input(
                    "Income reduction (%)", min_value=0.0, max_value=100.0,
                    value=float(item.get("income_reduction_pct", 100.0)), step=5.0,
                    key=f"curveball_reduction_{item_key(item)}",
                )
                item["recovery_months"] = st.number_input(
                    "Recovery period (months)", min_value=0,
                    value=int(item.get("recovery_months", 0)), step=1,
                    key=f"curveball_recovery_{item_key(item)}",
                )
                st.caption(
                    "During recovery, the configured reduction tapers linearly to zero. "
                    "Overlapping events multiply their income factors."
                )
                if st.button("Remove curveball", key=f"remove_curveball_{item_key(item)}"):
                    remove_item(st.session_state.curveballs, index)
                    st.rerun()

    with st.sidebar:
        st.header("Scenario")
        scenario_controls_placeholder = st.empty()

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

        with st.expander(
            f"Property jurisdictions · {len(st.session_state.jurisdiction_profiles)} "
            "editable profile(s)",
            key="sidebar_jurisdictions",
        ):
            render_jurisdiction_profiles()

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
        linked_property_estimates = [
            (item, investment_jurisdiction_estimate(item))
            for item in linked_properties
        ]
        linked_monthly = sum(
            item["monthly_expenses"] + (
                estimate.monthly_operating_costs if estimate else 0
            )
            for item, estimate in linked_property_estimates
        )
        linked_yearly_tax = sum(
            item["yearly_tax"] + (
                estimate.annual_property_tax if estimate else 0
            )
            for item, estimate in linked_property_estimates
        )
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

        with st.container(border=True):
            st.subheader("Purchase Property · optional")
            st.session_state.purchase_enabled = st.checkbox(
                "Enable future property purchase", value=st.session_state.purchase_enabled,
                help="Disabled preserves the existing scenario exactly.")
            purchase = st.session_state.purchase_fields
            if st.session_state.purchase_enabled:
                st.session_state.purchase_mode = st.radio(
                    "Timing", ("Automatic — when affordable", "Scheduled — in a selected month"),
                    index=0 if st.session_state.purchase_mode.startswith("Automatic") else 1,
                    help="Automatic mode purchases once when both cash conditions are met.")
                purchase_profile_options = [None] + [
                    profile["name"]
                    for profile in st.session_state.jurisdiction_profiles
                    if profile["name"]
                ]
                current_purchase_profile = (
                    st.session_state.purchase_jurisdiction_profile
                    if st.session_state.purchase_jurisdiction_profile
                    in purchase_profile_options else None
                )
                st.session_state.purchase_jurisdiction_profile = st.selectbox(
                    "Purchase jurisdiction estimate profile",
                    purchase_profile_options,
                    index=purchase_profile_options.index(current_purchase_profile),
                    format_func=lambda value: (
                        "No jurisdiction estimates" if value is None else value
                    ),
                    key="purchase_jurisdiction_profile_input",
                    help=(
                        "Adds estimated acquisition, maintenance, and property-tax "
                        "costs from the editable profile to the fixed amounts below."
                    ),
                )
                purchase["purchase_price"] = st.number_input("Target property price", min_value=1.0, value=float(purchase["purchase_price"]), step=1000.0, key="purchase_price")
                purchase["down_payment_percentage"] = st.number_input("Down payment (%)", min_value=0.0, max_value=100.0, value=float(purchase["down_payment_percentage"]), key="purchase_down_pct")
                purchase_profile = jurisdiction_profile_from_state(
                    st.session_state.purchase_jurisdiction_profile)
                if purchase_profile is not None:
                    needs = {c.base for c in purchase_profile.acquisition_components}
                    if "cadastral_value" in needs:
                        st.session_state.purchase_cadastral_income = st.number_input(
                            "Cadastral income (rendita catastale, annual; 0 = not entered)",
                            min_value=0.0,
                            value=float(st.session_state.purchase_cadastral_income),
                            key="purchase_cadastral_income_input",
                            help="Only for the prezzo-valore regime. If not entered, the "
                                 "sale price is used as an indicative proxy.",
                        )
                    if "higher_of_price_and_assessed" in needs:
                        st.session_state.purchase_assessed_value = st.number_input(
                            "Assessed value (e.g. Portuguese VPT; 0 = not entered)",
                            min_value=0.0,
                            value=float(st.session_state.purchase_assessed_value),
                            key="purchase_assessed_value_input",
                            help="The tax base is the higher of price and assessed value.",
                        )
                    purchase_estimate = purchase_cost_estimate(
                        purchase_profile, PropertyPurchase(**purchase),
                        st.session_state.purchase_cadastral_income or None,
                        st.session_state.purchase_assessed_value or None,
                    )
                    for label, amount, basis in purchase_estimate.acquisition_breakdown:
                        st.caption(f"{label}: ${amount:,.0f} ({_BASIS_LABELS[basis]})")
                    for warning in purchase_estimate.warnings:
                        st.warning(warning)
                    if purchase_profile.unsupported_cases:
                        st.warning(f"Not covered: {purchase_profile.unsupported_cases}")
                purchase["transaction_fees"] = st.number_input("Additional fixed one-off transaction fees", min_value=0.0, value=float(purchase["transaction_fees"]), step=500.0, key="purchase_fees")
                purchase["mortgage_interest_rate"] = st.number_input("Mortgage annual interest (%)", min_value=0.0, value=float(purchase["mortgage_interest_rate"]), step=0.1, key="purchase_rate")
                purchase["mortgage_duration_years"] = st.number_input("Mortgage term (years)", min_value=1, value=int(purchase["mortgage_duration_years"]), key="purchase_term")
                purchase["fixed_rate"] = st.checkbox("Fixed-rate mortgage", value=bool(purchase["fixed_rate"]), key="purchase_fixed")
                purchase["monthly_expenses"] = st.number_input("Additional fixed monthly ownership costs", min_value=0.0, value=float(purchase["monthly_expenses"]), step=25.0, key="purchase_monthly_cost")
                purchase["yearly_tax"] = st.number_input("Additional fixed annual ownership tax", min_value=0.0, value=float(purchase["yearly_tax"]), step=100.0, key="purchase_yearly_tax")
                if st.session_state.purchase_mode.startswith("Automatic"):
                    st.session_state.purchase_min_cash = st.number_input("Minimum cash threshold", min_value=0.0, value=float(st.session_state.purchase_min_cash), step=500.0, key="purchase_threshold")
                    st.session_state.purchase_reserve = st.number_input(
                        "Minimum cash retained at closing", min_value=0.0,
                        value=float(st.session_state.purchase_reserve), step=500.0,
                        key="purchase_reserve_input",
                        help=(
                            "Cash left immediately after the purchase. This is an "
                            "affordability check, not a protected account; later expenses, "
                            "payments, and unforeseen costs can use it."
                        ),
                    )
                    st.session_state.purchase_affordability_enabled = st.checkbox(
                        "Also require mortgage affordability",
                        value=bool(st.session_state.purchase_affordability_enabled),
                        key="purchase_affordability_toggle",
                        help=(
                            "Optional planning heuristic only—not a lender decision or "
                            "guarantee of approval. Disabled preserves cash-only behavior."
                        ),
                    )
                    if st.session_state.purchase_affordability_enabled:
                        st.caption(
                            "Mortgage affordability · editable planning assumptions. "
                            "Annual bonuses are excluded because they are not guaranteed, "
                            "giving a more conservative estimate."
                        )
                        income_options = [
                            item["name"] for item in st.session_state.incomes
                        ]
                        selected_incomes = [
                            name for name in st.session_state.purchase_qualifying_incomes
                            if name in income_options
                        ]
                        st.session_state.purchase_qualifying_incomes = st.multiselect(
                            "Qualifying income sources",
                            income_options,
                            default=selected_incomes,
                            key="purchase_qualifying_income_input",
                            help=(
                                "Select only household income sources you want this "
                                "planning rule to count."
                            ),
                        )
                        st.session_state.purchase_affordability_ratio = st.number_input(
                            "Maximum total debt service (% of qualifying income)",
                            min_value=0.0, max_value=100.0,
                            value=float(st.session_state.purchase_affordability_ratio),
                            step=1.0, key="purchase_affordability_ratio_input",
                            help=(
                                "The maximum share of qualifying monthly income available "
                                "for existing debt payments and the proposed mortgage."
                            ),
                        )
                        st.session_state.purchase_variable_income_pct = st.number_input(
                            "Count freelance / royalty / other income (%)",
                            min_value=0.0, max_value=100.0,
                            value=float(st.session_state.purchase_variable_income_pct),
                            step=5.0, key="purchase_variable_income_input",
                            help=(
                                "Choose how much of selected freelance, royalty, and other "
                                "non-salary income to count toward affordability."
                            ),
                        )
                        st.session_state.purchase_base_living_buffer = st.number_input(
                            "Base monthly living buffer",
                            min_value=0.0,
                            value=float(st.session_state.purchase_base_living_buffer),
                            step=100.0, format="%.0f", key="purchase_base_buffer_input",
                            help=(
                                "A monthly amount reserved for household living costs "
                                "before assessing income available for debt payments."
                            ),
                        )
                        buffer_left, buffer_right = st.columns(2)
                        st.session_state.purchase_adult_count = buffer_left.number_input(
                            "Adults", min_value=0,
                            value=int(st.session_state.purchase_adult_count),
                            step=1, key="purchase_adult_count_input",
                            help="Number of adults included in the household buffer estimate.",
                        )
                        st.session_state.purchase_buffer_per_adult = buffer_right.number_input(
                            "Buffer per adult", min_value=0.0,
                            value=float(st.session_state.purchase_buffer_per_adult),
                            step=100.0, format="%.0f", key="purchase_adult_buffer_input",
                            help="Monthly living-cost buffer added for each adult.",
                        )
                        dependent_left, dependent_right = st.columns(2)
                        st.session_state.purchase_dependent_count = dependent_left.number_input(
                            "Dependents", min_value=0,
                            value=int(st.session_state.purchase_dependent_count),
                            step=1, key="purchase_dependent_count_input",
                            help=(
                                "Number of dependents included in the household buffer "
                                "estimate."
                            ),
                        )
                        st.session_state.purchase_buffer_per_dependent = dependent_right.number_input(
                            "Buffer per dependent", min_value=0.0,
                            value=float(st.session_state.purchase_buffer_per_dependent),
                            step=100.0, format="%.0f",
                            key="purchase_dependent_buffer_input",
                            help="Monthly living-cost buffer added for each dependent.",
                        )
                    st.session_state.purchase_delay_enabled = st.checkbox(
                        "Add a delay between eligibility and closing",
                        value=bool(st.session_state.purchase_delay_enabled),
                        key="purchase_delay_toggle",
                        help=(
                            "When enabled, becoming eligible schedules a later closing. "
                            "Cash and mortgage affordability are checked again then."
                        ),
                    )
                    if st.session_state.purchase_delay_enabled:
                        st.session_state.purchase_delay_mode = st.radio(
                            "Closing delay",
                            ("Fixed", "Bounded random"),
                            index=(
                                0 if st.session_state.purchase_delay_mode == "Fixed"
                                else 1
                            ),
                            horizontal=True,
                            key="purchase_delay_mode_input",
                        )
                        if st.session_state.purchase_delay_mode == "Fixed":
                            st.session_state.purchase_fixed_delay_months = st.number_input(
                                "Months from eligibility to closing",
                                min_value=0,
                                value=int(st.session_state.purchase_fixed_delay_months),
                                step=1,
                                key="purchase_fixed_delay_input",
                            )
                        else:
                            delay_left, delay_right = st.columns(2)
                            st.session_state.purchase_random_delay_min = delay_left.number_input(
                                "Minimum delay (months)",
                                min_value=0,
                                value=int(st.session_state.purchase_random_delay_min),
                                step=1,
                                key="purchase_random_delay_min_input",
                            )
                            st.session_state.purchase_random_delay_max = delay_right.number_input(
                                "Maximum delay (months)",
                                min_value=0,
                                value=int(st.session_state.purchase_random_delay_max),
                                step=1,
                                key="purchase_random_delay_max_input",
                            )
                        failure_options = {
                            "Retry next month until the simulation ends": "retry",
                            "Cancel this trajectory's purchase": "cancel",
                        }
                        current_failure_label = next(
                            label for label, value in failure_options.items()
                            if value == st.session_state.purchase_delay_failure_policy
                        )
                        selected_failure_label = st.selectbox(
                            "If closing checks no longer pass",
                            tuple(failure_options),
                            index=tuple(failure_options).index(current_failure_label),
                            key="purchase_delay_failure_input",
                        )
                        st.session_state.purchase_delay_failure_policy = (
                            failure_options[selected_failure_label]
                        )
                        st.caption(
                            "Rent continues and no property, mortgage, or owner costs are "
                            "created until closing actually succeeds."
                        )
                else:
                    max_purchase_month = years * 12
                    current_purchase_month = min(
                        max_purchase_month, max(1, int(purchase.get("purchase_month", 0)) + 1)
                    )
                    purchase["purchase_month"] = st.number_input(
                        "Purchase month (1-based)", min_value=1, max_value=max_purchase_month,
                        value=current_purchase_month, key="purchase_month",
                    ) - 1
                rent_options = ["Do not stop any expense"] + [item["name"] for item in st.session_state.expenses]
                selected = st.selectbox("Rent expense to stop after purchase", rent_options, index=(rent_options.index(st.session_state.selected_rent_expense) if st.session_state.selected_rent_expense in rent_options else 0), key="purchase_rent")
                st.session_state.selected_rent_expense = None if selected == rent_options[0] else selected
                preview = PropertyPurchase(**purchase)
                selected_profile = jurisdiction_profile_from_state(
                    st.session_state.purchase_jurisdiction_profile
                )
                profile_estimate = (
                    purchase_cost_estimate(
                        selected_profile, preview,
                        st.session_state.purchase_cadastral_income or None,
                        st.session_state.purchase_assessed_value or None,
                    ) if selected_profile is not None else None
                )
                estimated_fees = (
                    profile_estimate.acquisition_costs if profile_estimate else 0
                )
                estimated_monthly = (
                    profile_estimate.monthly_operating_costs if profile_estimate else 0
                )
                estimated_tax = (
                    profile_estimate.annual_property_tax if profile_estimate else 0
                )
                st.info(
                    f"Configuration estimate: upfront cash **${preview.down_payment + preview.transaction_fees + estimated_fees:,.0f}**, "
                    f"mortgage principal **${preview.mortgage_principal:,.0f}**, estimated "
                    f"payment **${property_purchase_payment(preview):,.0f}/month**, ownership "
                    f"cost **${preview.monthly_expenses + estimated_monthly:,.0f}/month + "
                    f"${preview.yearly_tax + estimated_tax:,.0f}/year tax**. Automatic "
                    "purchase is stochastic and may not occur. Profile estimates use the "
                    "starting purchase price and do not predict future law or reassessment."
                )
                if (st.session_state.purchase_mode.startswith("Automatic")
                        and st.session_state.purchase_delay_enabled):
                    if st.session_state.purchase_delay_mode == "Fixed":
                        delay_text = (
                            f"{st.session_state.purchase_fixed_delay_months} month(s)"
                        )
                    else:
                        delay_text = (
                            f"{st.session_state.purchase_random_delay_min}–"
                            f"{st.session_state.purchase_random_delay_max} months, "
                            "sampled once per trajectory"
                        )
                    failure_text = (
                        "retry monthly"
                        if st.session_state.purchase_delay_failure_policy == "retry"
                        else "cancel the purchase"
                    )
                    st.caption(
                        f"Closing delay: {delay_text}. At the scheduled month, recheck "
                        f"cash and mortgage affordability; if either fails, {failure_text}."
                    )
                if (st.session_state.purchase_mode.startswith("Automatic")
                        and st.session_state.purchase_affordability_enabled):
                    affordability_policy = MortgageAffordabilityPolicy(
                        max_debt_service_ratio_pct=float(
                            st.session_state.purchase_affordability_ratio
                        ),
                        qualifying_income_names=tuple(
                            st.session_state.purchase_qualifying_incomes
                        ),
                        variable_income_inclusion_pct=float(
                            st.session_state.purchase_variable_income_pct
                        ),
                        base_monthly_living_buffer=float(
                            st.session_state.purchase_base_living_buffer
                        ),
                        adult_count=int(st.session_state.purchase_adult_count),
                        monthly_buffer_per_adult=float(
                            st.session_state.purchase_buffer_per_adult
                        ),
                        dependent_count=int(
                            st.session_state.purchase_dependent_count
                        ),
                        monthly_buffer_per_dependent=float(
                            st.session_state.purchase_buffer_per_dependent
                        ),
                    )
                    assessment = mortgage_affordability_preview(
                        preview, affordability_policy,
                        st.session_state.incomes, st.session_state.loans,
                        FinancialAssumptions(**st.session_state.financial_assumptions),
                    )
                    status = "passes" if assessment["eligible"] else "does not pass"
                    ratio = assessment["debt_service_ratio_pct"]
                    ratio_text = f"{ratio:.1f}%" if ratio is not None else "not defined"
                    st.caption(
                        f"Current estimate {status}: qualifying income "
                        f"${assessment['qualifying_monthly_income']:,.0f}/month; "
                        f"existing debt ${assessment['existing_monthly_debt_payments']:,.0f}; "
                        f"new mortgage ${assessment['proposed_mortgage_payment']:,.0f}; "
                        f"debt-service ratio {ratio_text} (limit "
                        f"{assessment['max_debt_service_ratio_pct']:.1f}%); household "
                        f"buffer ${assessment['monthly_living_buffer']:,.0f}. "
                        f"{assessment['reason']}"
                    )

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

        enabled_curveballs = sum(
            bool(item.get("enabled")) for item in st.session_state.curveballs
        )
        with st.expander(
            f"Curveballs · {enabled_curveballs} enabled / "
            f"{len(st.session_state.curveballs)} configured",
            key="sidebar_curveballs",
        ):
            render_curveballs()

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
        comparison_requested = False
        if isinstance(st.session_state.get("scenario_workspace"), ScenarioWorkspace):
            comparison_requested = st.button(
                "⇄ Compare A/B",
                use_container_width=True,
                help=(
                    "Run both saved variants with the same horizon, trajectory count, "
                    "and effective seeds."
                ),
            )

    purchase_config = None
    if st.session_state.purchase_enabled:
        purchase_template = PropertyPurchase(**st.session_state.purchase_fields)
        affordability_policy = None
        completion_delay_policy = None
        if (st.session_state.purchase_mode.startswith("Automatic")
                and st.session_state.purchase_affordability_enabled):
            affordability_policy = MortgageAffordabilityPolicy(
                max_debt_service_ratio_pct=float(
                    st.session_state.purchase_affordability_ratio
                ),
                qualifying_income_names=tuple(
                    st.session_state.purchase_qualifying_incomes
                ),
                variable_income_inclusion_pct=float(
                    st.session_state.purchase_variable_income_pct
                ),
                base_monthly_living_buffer=float(
                    st.session_state.purchase_base_living_buffer
                ),
                adult_count=int(st.session_state.purchase_adult_count),
                monthly_buffer_per_adult=float(
                    st.session_state.purchase_buffer_per_adult
                ),
                dependent_count=int(st.session_state.purchase_dependent_count),
                monthly_buffer_per_dependent=float(
                    st.session_state.purchase_buffer_per_dependent
                ),
            )
        if (st.session_state.purchase_mode.startswith("Automatic")
                and st.session_state.purchase_delay_enabled):
            if st.session_state.purchase_delay_mode == "Fixed":
                minimum_delay = maximum_delay = int(
                    st.session_state.purchase_fixed_delay_months
                )
            else:
                minimum_delay = int(st.session_state.purchase_random_delay_min)
                maximum_delay = int(st.session_state.purchase_random_delay_max)
            completion_delay_policy = PurchaseCompletionDelayPolicy(
                minimum_months=minimum_delay,
                maximum_months=maximum_delay,
                on_ineligible=st.session_state.purchase_delay_failure_policy,
            )
        purchase_config = (
            AutomaticPurchasePolicy(
                purchase_template,
                float(st.session_state.purchase_min_cash),
                float(st.session_state.purchase_reserve),
                affordability_policy,
                completion_delay_policy,
            )
            if st.session_state.purchase_mode.startswith("Automatic") else purchase_template
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
            ownership_percentage=float(item.get("ownership_percentage", 100.0)),
            linked_loan_name=item.get("linked_loan_name"),
            liability_share_percentage=float(item.get("liability_share_percentage", 100.0)),
            rental_income_name=item.get("rental_income_name"),
            jurisdiction_profile_name=item.get("jurisdiction_profile_name"),
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
            (_whole_amount(item["childcare_monthly_cost"])
             if item.get("childcare_monthly_cost") is not None else None),
        ) for item in st.session_state.dependents],
        curveballs=[CurveballConfig(
            item["name"], bool(item["enabled"]), float(item["probability"]) / 100,
            int(item["start_month_min"]), int(item["start_month_max"]),
            int(item["duration_months"]), item.get("affected_income_name"),
            float(item["income_reduction_pct"]), int(item["recovery_months"]),
        ) for item in st.session_state.curveballs],
        assumptions=FinancialAssumptions(**st.session_state.financial_assumptions),
        property_purchase=purchase_config,
        selected_rent_expense=st.session_state.selected_rent_expense,
        jurisdiction_profiles=configured_jurisdiction_profiles(),
        property_purchase_jurisdiction_profile=(
            st.session_state.purchase_jurisdiction_profile
            if purchase_config is not None else None
        ),
        purchase_cadastral_income=(
            st.session_state.purchase_cadastral_income or None
            if purchase_config is not None else None
        ),
        purchase_assessed_value=(
            st.session_state.purchase_assessed_value or None
            if purchase_config is not None else None
        ),
    )

    with scenario_controls_placeholder.container():
        with st.expander("Scenario import/export", key="sidebar_import"):
            st.markdown("**Single-scenario JSON**")
            uploaded_export = st.file_uploader(
                "Import single-scenario JSON", type="json",
                help="Select a single-scenario JSON file exported by this simulator.",
                key=f"scenario_import_file_{st.session_state.get('import_uploader_counter', 0)}",
            )
            import_notice = st.session_state.pop("import_success_notice", None)
            if import_notice:
                st.success(import_notice)
            import_error = st.session_state.pop("import_error", None)
            if import_error:
                st.error(import_error)
            st.button(
                "Import parameters", disabled=uploaded_export is None,
                use_container_width=True,
                on_click=import_single_scenario,
            )

            single_export_placeholder = st.empty()
            single_export_placeholder.caption(
                "Run simulations to export this single scenario with results."
            )
            st.divider()
            st.markdown("**A/B workspace JSON**")
            if workspace_notice:
                st.success(workspace_notice)
            workspace_error = st.session_state.pop("workspace_import_error", None)
            workspace = st.session_state.scenario_workspace
            if workspace is None:
                st.caption(
                    "Create independent Baseline A and Alternative B copies from the "
                    "current inputs. Single-scenario simulation remains the default."
                )
                if st.button(
                    "Create Baseline A and Alternative B",
                    key="create_scenario_workspace",
                ):
                    st.session_state.scenario_workspace = ScenarioWorkspace.from_config(config)
                    st.session_state.pop("simulation_result", None)
                    st.session_state.pop("scenario_variant_selector", None)
                    st.rerun()
            else:
                # Every rerun first saves the visible editor into only the active
                # variant.  ScenarioWorkspace defensively copies the configuration.
                workspace = workspace.save(workspace.active_id, config)
                baseline_name = st.text_input(
                    "Baseline name",
                    value=workspace.variant(BASELINE_ID).name,
                    key="scenario_baseline_name",
                )
                alternative_name = st.text_input(
                    "Alternative name",
                    value=workspace.variant(ALTERNATIVE_ID).name,
                    key="scenario_alternative_name",
                )
                try:
                    if baseline_name != workspace.variant(BASELINE_ID).name:
                        workspace = workspace.rename(BASELINE_ID, baseline_name)
                    if alternative_name != workspace.variant(ALTERNATIVE_ID).name:
                        workspace = workspace.rename(ALTERNATIVE_ID, alternative_name)
                except ValueError as error:
                    st.error(str(error))
                st.session_state.scenario_workspace = workspace

                variant_ids = [BASELINE_ID, ALTERNATIVE_ID]
                st.radio(
                    "Scenario to edit",
                    variant_ids,
                    index=variant_ids.index(workspace.active_id),
                    format_func=lambda variant_id: workspace.variant(variant_id).name,
                    horizontal=True,
                    key="scenario_variant_selector",
                    on_change=switch_scenario_variant,
                    help=(
                        "The visible sidebar edits only the selected scenario. Switching "
                        "saves the current inputs and clears their simulation result."
                    ),
                )

                reset_col, export_col = st.columns(2)
                reset_col.button(
                    "Reset Alternative B from Baseline A",
                    key="reset_alternative_scenario",
                    use_container_width=True,
                    on_click=reset_alternative_scenario,
                )
                export_col.download_button(
                    "Export A/B workspace JSON",
                    data=create_scenario_workspace_export(workspace),
                    file_name="financial-scenario-workspace.json",
                    mime="application/json",
                    use_container_width=True,
                    help=(
                        "Exports both named input configurations without simulation results."
                    ),
                )
                st.caption(
                    "Use **Compare A/B** on the main screen to run both configurations "
                    "with matched trajectory seeds. Workspace JSON stores inputs only."
                )

            uploaded_workspace = st.file_uploader(
                "Import A/B workspace JSON",
                type="json",
                key=(
                    "scenario_workspace_file_"
                    f"{st.session_state.workspace_import_counter}"
                ),
                help="Select an A/B workspace JSON exported by this simulator.",
            )
            st.button(
                "Import A/B workspace",
                disabled=uploaded_workspace is None,
                key="import_scenario_workspace",
                on_click=import_scenario_workspace,
                use_container_width=True,
            )
            if workspace_error:
                st.error(workspace_error)

    workspace = st.session_state.scenario_workspace
    comparison_fingerprint = None
    if isinstance(workspace, ScenarioWorkspace):
        comparison_fingerprint = json.dumps({
            "simulation_count": int(simulation_count),
            "baseline_name": workspace.variant(BASELINE_ID).name,
            "alternative_name": workspace.variant(ALTERNATIVE_ID).name,
            "baseline": asdict(workspace.config_for(BASELINE_ID)),
            "alternative": asdict(workspace.config_for(ALTERNATIVE_ID)),
        }, sort_keys=True)

    if comparison_requested:
        try:
            with st.spinner(
                    f"Running {simulation_count} paired simulations for A and B..."):
                comparison = run_paired_scenario_comparison(
                    workspace, int(simulation_count),
                )
        except (ValueError, TypeError, KeyError, InsufficientFundsException) as error:
            st.session_state.pop("scenario_comparison_result", None)
            st.error(f"These scenarios cannot be compared: {error}")
            return
        st.session_state.scenario_comparison_result = {
            "fingerprint": comparison_fingerprint,
            "comparison": comparison,
        }
        st.session_state.active_result_mode = "comparison"

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
        st.session_state.active_result_mode = "single"

    if st.session_state.active_result_mode == "comparison":
        comparison_record = st.session_state.get("scenario_comparison_result")
        if comparison_record is None:
            st.info("Click **Compare A/B** to run the paired scenario comparison.")
            return
        if comparison_record["fingerprint"] != comparison_fingerprint:
            st.warning(
                "The A/B inputs, names, or run count changed. Click **Compare A/B** "
                "to refresh the paired results."
            )
            return
        comparison = comparison_record["comparison"]
        st.subheader("Paired A/B comparison")
        st.info(
            f"Arithmetic averages across {comparison.simulation_count} paired "
            f"trajectory/trajectories over {comparison.horizon_years} years. "
            f"Run n uses seed {comparison.base_seed} + n − 1 in both scenarios. "
            "B−A is calculated within each seed pair before uncertainty summaries."
        )
        st.info(comparison_explanation(comparison), icon="ℹ️")
        for warning in comparison.warnings:
            st.warning(warning, icon="⚠️")

        balance_rows = comparison_balance_rows(comparison)
        balances_by_name = {row["Balance"]: row for row in balance_rows}
        metric_columns = st.columns(3)
        for column, balance_name in zip(
                metric_columns, ("Net worth", "Available cash", "Property equity")):
            row = balances_by_name[balance_name]
            column.metric(
                f"{balance_name} · B minus A",
                _money(row["B minus A"]),
                help="Alternative B minus Baseline A at the comparison horizon.",
            )

        st.markdown("**Final average balances**")
        st.dataframe(
            balance_rows, hide_index=True, use_container_width=True,
            column_config={
                "Baseline A": st.column_config.NumberColumn(format="$%.0f"),
                "Alternative B": st.column_config.NumberColumn(format="$%.0f"),
                "B minus A": st.column_config.NumberColumn(format="$%+.0f"),
            },
        )
        st.caption(
            "These are overlapping balances, not additive attribution: property equity "
            "is part of net worth, and outstanding debt is already reflected in net worth."
        )

        baseline_result = comparison.baseline_batch.average_result
        alternative_result = comparison.alternative_batch.average_result
        comparison_chart_values = []
        for month, (a_worth, b_worth, a_cash, b_cash) in enumerate(zip(
                baseline_result[0], alternative_result[0],
                baseline_result[2]["bank_account"],
                alternative_result[2]["bank_account"]), start=1):
            comparison_chart_values.extend((
                {"month": month, "amount": a_worth,
                 "series": f"{comparison.baseline_name} · net worth"},
                {"month": month, "amount": b_worth,
                 "series": f"{comparison.alternative_name} · net worth"},
                {"month": month, "amount": a_cash,
                 "series": f"{comparison.baseline_name} · cash"},
                {"month": month, "amount": b_cash,
                 "series": f"{comparison.alternative_name} · cash"},
            ))
        comparison_chart = alt.Chart(
            alt.Data(values=comparison_chart_values)
        ).mark_line(strokeWidth=2.3).encode(
            x=alt.X("month:Q", title="Month"),
            y=alt.Y("amount:Q", title="Amount", axis=alt.Axis(format="$,.0f")),
            color=alt.Color(
                "series:N", title="Scenario and balance",
                legend=alt.Legend(orient="top", direction="horizontal"),
            ),
            tooltip=[
                alt.Tooltip("month:Q", title="Month", format=".0f"),
                alt.Tooltip("series:N", title="Series"),
                alt.Tooltip("amount:Q", title="Amount", format="$,.0f"),
            ],
        ).properties(height=380, title="Average net worth and available cash")
        st.altair_chart(
            comparison_chart, use_container_width=True,
            key="paired_scenario_balances",
        )

        delta_rows = paired_delta_quantiles(comparison)
        delta_data = alt.Data(values=delta_rows)
        delta_band = alt.Chart(delta_data).mark_area(
            color="#8b5cf6", opacity=0.18,
        ).encode(
            x=alt.X("month:Q", title="Month"),
            y=alt.Y("p10:Q", title="Net-worth B − A", axis=alt.Axis(format="$,.0f")),
            y2="p90:Q",
            tooltip=[
                alt.Tooltip("month:Q", title="Month", format=".0f"),
                alt.Tooltip("p10:Q", title="10th percentile", format="$,.0f"),
                alt.Tooltip("median:Q", title="Median", format="$,.0f"),
                alt.Tooltip("p90:Q", title="90th percentile", format="$,.0f"),
                alt.Tooltip("mean:Q", title="Mean", format="$,.0f"),
            ],
        )
        delta_lines = alt.Chart(delta_data).transform_fold(
            ["mean", "median"], as_=["statistic", "amount"],
        ).mark_line(strokeWidth=2.2).encode(
            x="month:Q",
            y=alt.Y("amount:Q", title="Net-worth B − A"),
            color=alt.Color(
                "statistic:N", title="Paired difference",
                scale=alt.Scale(
                    domain=["mean", "median"], range=["#7c3aed", "#0e7490"],
                ),
            ),
        )
        st.altair_chart(
            (delta_band + delta_lines).properties(
                height=320,
                title="Uncertainty of the paired net-worth difference (B − A)",
            ),
            use_container_width=True,
            key="paired_scenario_delta",
        )
        final_delta = delta_rows[-1]
        st.caption(
            f"At month {final_delta['month']}, paired B−A net worth: mean "
            f"{_money(final_delta['mean'])}, median {_money(final_delta['median'])}, "
            f"10th–90th percentile {_money(final_delta['p10'])} to "
            f"{_money(final_delta['p90'])}. With few runs these are descriptive "
            "sample quantiles, not stable tail-risk estimates."
        )

        with st.expander("Computed cash-flow comparison", expanded=True):
            st.dataframe(
                comparison_cash_flow_rows(comparison),
                hide_index=True, use_container_width=True,
                column_config={
                    "Baseline A": st.column_config.NumberColumn(format="$%.0f"),
                    "Alternative B": st.column_config.NumberColumn(format="$%.0f"),
                    "B minus A": st.column_config.NumberColumn(format="$%+.0f"),
                },
            )
            st.caption(
                "Tracked flows are not summed into a causal bridge. Debt service mixes "
                "principal and interest. Automatic contributions are gross transfers and "
                "their fee row is a memo already contained in that gross amount. Income "
                "tax is netted into income and cannot be split reliably from bonuses or "
                "variable income in current outputs. Down payments, scheduled investment "
                "contributions, bank fees, and investment returns are not separately "
                "auditable until the transaction ledger in #54."
            )

        with st.expander("Input differences"):
            differences = scenario_input_differences(comparison)
            if differences:
                st.dataframe(differences, hide_index=True, use_container_width=True)
            else:
                st.success("The effective A and B inputs are identical.")
        st.caption(
            "Observed differences are illustrative model outcomes, not a recommendation "
            "that either scenario is universally better. Stored historical-run comparison "
            "is a separate future workflow in issue #124."
        )
        return

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

    suggestions = deterministic_ab_suggestions(config)
    suggestion_notice_key = f"{run_id}:{scenario_fingerprint}"
    if st.session_state.get("ab_suggestion_notice_key") != suggestion_notice_key:
        st.session_state.ab_suggestion_notice_key = suggestion_notice_key
        st.toast(
            "Deterministic A/B alternatives are available. Open the suggestion "
            "panel below the results to compare one.",
            icon="✨",
        )
    with st.popover("✨ Suggested A/B alternatives", use_container_width=False):
        st.caption(
            "These are static, transparent scenario rules based on the current "
            "inputs. Nothing changes until you choose a comparison."
        )
        for suggestion in suggestions:
            st.markdown(f"**{suggestion.title}**")
            st.caption(suggestion.description)
            if st.button(
                "Apply and compare",
                key=f"apply_ab_suggestion_{suggestion.key}",
                use_container_width=True,
            ):
                suggestion_workspace = ScenarioWorkspace.from_config(config)
                suggestion_workspace = suggestion_workspace.rename(
                    BASELINE_ID, "Current scenario",
                ).rename(ALTERNATIVE_ID, suggestion.title)
                suggestion_workspace = suggestion_workspace.save(
                    ALTERNATIVE_ID, suggestion.config,
                )
                suggestion_fingerprint = json.dumps({
                    "simulation_count": int(simulation_count),
                    "baseline_name": suggestion_workspace.variant(BASELINE_ID).name,
                    "alternative_name": suggestion_workspace.variant(ALTERNATIVE_ID).name,
                    "baseline": asdict(suggestion_workspace.config_for(BASELINE_ID)),
                    "alternative": asdict(suggestion_workspace.config_for(ALTERNATIVE_ID)),
                }, sort_keys=True)
                try:
                    with st.spinner(
                            f"Comparing current scenario with {suggestion.title}..."):
                        comparison = run_paired_scenario_comparison(
                            suggestion_workspace, int(simulation_count),
                        )
                except (ValueError, TypeError, KeyError, InsufficientFundsException) as error:
                    st.error(f"This suggestion cannot be compared: {error}")
                else:
                    for key in (
                        "scenario_baseline_name", "scenario_alternative_name",
                        "scenario_variant_selector",
                    ):
                        st.session_state.pop(key, None)
                    st.session_state.scenario_workspace = suggestion_workspace
                    st.session_state.scenario_comparison_result = {
                        "fingerprint": suggestion_fingerprint,
                        "comparison": comparison,
                    }
                    st.session_state.active_result_mode = "comparison"
                    st.rerun()

    if config.property_purchase is not None:
        outcomes = [purchase_outcome_from_details(t.details) for t in trajectories]
        purchased = [o for o in outcomes if o and o.get("purchased")]
        if purchased:
            months = [o["purchase_month"] + 1 for o in purchased]
            st.success(f"Purchased in {len(purchased)} of {len(trajectories)} trajectories ({len(purchased) / len(trajectories):.0%}); purchase months ranged from {min(months)} to {max(months)}.")
        if isinstance(config.property_purchase, AutomaticPurchasePolicy):
            not_purchased = len(trajectories) - len(purchased)
            if not_purchased:
                st.warning(automatic_purchase_warning(
                    config.property_purchase, not_purchased, len(trajectories), outcomes,
                ))
        elif not purchased:
            st.info("Not purchased during this simulation.")

    curveball_rows = []
    for trajectory in trajectories:
        for event in trajectory.details.get("curveball_events", []):
            curveball_rows.append({
                "Run": trajectory.run_number,
                "Event": event["name"],
                "Triggered": event["triggered"],
                "Start month": event["start_month"],
                "End month": event["end_month"],
                "Cash below pre-event level": event["cash_below_pre_event_month"],
                "Recovered month": event["recovery_month"],
                "Recovery months observed": event["recovery_months_observed"],
            })
    if curveball_rows:
        with st.expander("Curveball outcomes", expanded=True):
            st.dataframe(curveball_rows, hide_index=True, use_container_width=True)
            st.caption(
                "Recovery means both cash and net worth returned to their respective "
                "pre-event levels. A blank recovery month means the threshold was not "
                "reached before the simulation ended."
            )

    st.subheader("Monte Carlo analysis")
    view_mode = st.radio(
        "Result view", ("Overview", "Individual run", "Compare"), horizontal=True,
        key=f"monte_carlo_view_{run_id}",
        help=(
            "Overview is the month-by-month arithmetic average across all runs. "
            "Individual run shows one simulated path. Compare shows the lowest, "
            "median-ranked and highest runs by final net worth."
        ),
    )
    if view_mode == "Compare":
        st.info(monte_carlo_compare_explanation(int(simulation_count)))
        entries = select_representative_trajectories(trajectories)
        rows, chart_rows = [], []
        for entry in entries:
            trajectory = entry["trajectory"]
            series = f"Run {trajectory.run_number} (seed {trajectory.seed})"
            rows.append({
                "Role": " / ".join(entry["roles"]),
                "Run": trajectory.run_number,
                "Seed": trajectory.seed,
                "Final net worth": trajectory.net_worth[-1],
                "Rank by final net worth": f"{entry['rank']} of {len(trajectories)}",
            })
            chart_rows.extend(
                {"month": month, "amount": worth, "series": series,
                 "role": " / ".join(entry["roles"])}
                for month, worth in enumerate(trajectory.net_worth, start=1)
            )
        if len({t.net_worth[-1] for t in trajectories}) == 1 and len(trajectories) > 1:
            st.caption("All runs ended with the same final net worth; "
                       "run number was used to break ties.")
        elif len(entries) < 3:
            st.caption("With so few distinct outcomes, one run fills several roles.")
        st.dataframe(
            rows, hide_index=True, use_container_width=True,
            column_config={
                "Final net worth": st.column_config.NumberColumn(format="$%.0f"),
            },
        )
        compare_chart = alt.Chart(alt.Data(values=chart_rows)).mark_line(
            strokeWidth=2.5,
        ).encode(
            x=alt.X("month:Q", title="Month"),
            y=alt.Y("amount:Q", title="Net worth", axis=alt.Axis(format="$,.0f")),
            color=alt.Color("series:N", title="Actual run",
                            legend=alt.Legend(orient="top", direction="horizontal")),
            tooltip=[
                alt.Tooltip("role:N", title="Role"),
                alt.Tooltip("series:N", title="Run"),
                alt.Tooltip("month:Q", title="Month", format=".0f"),
                alt.Tooltip("amount:Q", title="Net worth", format="$,.0f"),
            ],
        ).properties(height=420, title="Net worth of representative actual runs")
        st.altair_chart(compare_chart, use_container_width=True,
                        key=f"compare_chart_{run_id}")
        st.caption("Illustrative projection only — this is not financial advice.")
        return
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

    selected_outcome = purchase_outcome_from_details(details)
    timing_explanation = purchase_timing_explanation(selected_outcome)
    if timing_explanation is not None:
        st.info(f"**{detail_prefix} purchase timing:** {timing_explanation}")
    affordability = (
        selected_outcome.get("affordability")
        if isinstance(selected_outcome, dict) else None
    )
    if affordability is not None:
        ratio = affordability["debt_service_ratio_pct"]
        ratio_text = f"{ratio:.1f}%" if ratio is not None else "not defined"
        st.info(
            f"**{detail_prefix} mortgage affordability:** "
            f"{'Eligible' if affordability['eligible'] else 'Not eligible'} — "
            f"qualifying income ${affordability['qualifying_monthly_income']:,.0f}/month; "
            f"existing debt ${affordability['existing_monthly_debt_payments']:,.0f}; "
            f"proposed mortgage ${affordability['proposed_mortgage_payment']:,.0f}; "
            f"debt-service ratio {ratio_text} against "
            f"{affordability['max_debt_service_ratio_pct']:.1f}%; household buffer "
            f"${affordability['monthly_living_buffer']:,.0f}; annual bonuses excluded. "
            f"{affordability['reason']}"
        )

    final_net_worth = net_worth[-1]
    starting_net_worth = net_worth[0]
    columns = st.columns(4)
    columns[0].metric(f"{label_prefix}final net worth", _money(final_net_worth), f"{final_net_worth - starting_net_worth:+,.0f}")
    columns[1].metric(f"{label_prefix}final cash", _money(details["bank_account"][-1]))
    columns[2].metric(f"{label_prefix}final investments", _money(details["investments"][-1]))
    columns[3].metric("Loans repaid", len(repayment_dates))

    single_export_placeholder.download_button(
        "Export single-scenario JSON",
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
        config, repayment_dates, len(net_worth), details.get("purchase_events"),
        details.get("curveball_events"),
    )
    chart = lines + month_hitboxes + selected_month_rule
    if events:
        label_height = max(
            max(net_worth), max(details["bank_account"]), max(details["investments"])
        )
        event_values = [
            {
                **event,
                "chart_label": event.get("chart_label", event["label"]),
                "label_height": label_height,
            }
            for event in events
        ]
        event_data = alt.Data(values=event_values)
        event_colors = alt.Scale(
            domain=list(CHART_EVENT_COLORS),
            range=list(CHART_EVENT_COLORS.values()),
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
            text=alt.Text("chart_label:N"),
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
