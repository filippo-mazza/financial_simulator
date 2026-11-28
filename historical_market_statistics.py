"""Descriptive historical index statistics; never a forecast."""
from dataclasses import dataclass
from datetime import date
from math import expm1, isfinite, log, sqrt
from statistics import stdev


@dataclass(frozen=True)
class HistoricalStatistics:
    start: date
    end: date
    observations: int
    periods_per_year: int
    annualized_volatility: float | None
    maximum_drawdown: float
    annualized_returns: dict[int, float | None]
    rolling_returns: dict[int, tuple[float, ...]]


def historical_statistics(observations, periods_per_year, horizons=(5, 10, 20)):
    """Positive index levels at consecutive monthly, quarterly or annual periods.

    Dates identify observation periods, not publication dates. Returns are
    decimal fractions. Missing periods are rejected, never interpolated.
    Rolling windows use calendar periods, with no partial-window estimates.
    Volatility is sample standard deviation of log returns times sqrt(frequency).
    """
    if periods_per_year not in (1, 4, 12):
        raise ValueError("Frequency must be annual, quarterly or monthly")
    rows = list(observations)
    if not rows:
        raise ValueError("At least one observation is required")
    horizons = tuple(horizons)
    if any(type(h) is not int or h <= 0 for h in horizons):
        raise ValueError("Horizons must be positive integer years")
    dates, values = zip(*rows)
    if any(type(d) is not date for d in dates):
        raise ValueError("Observation dates must be dates")
    if any(isinstance(v, bool) or not isfinite(v) or v <= 0 for v in values):
        raise ValueError("Index levels must be finite and positive")
    def period(d):
        return d.year * periods_per_year + (d.month - 1) // (12 // periods_per_year)
    if any(period(b) - period(a) != 1 for a, b in zip(dates, dates[1:])):
        raise ValueError("Observations must be ordered, unique and consecutive")
    logs = [log(v) for v in values]
    changes = [b - a for a, b in zip(logs, logs[1:])]
    volatility = stdev(changes) * sqrt(periods_per_year) if len(changes) >= 2 else None
    peak = values[0]
    drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        drawdown = min(drawdown, value / peak - 1)
    rolling = {}
    trailing = {}
    for years in horizons:
        window = years * periods_per_year
        returns = tuple(expm1((logs[i] - logs[i-window]) / years)
                        for i in range(window, len(logs)))
        rolling[years] = returns
        trailing[years] = returns[-1] if returns else None
    return HistoricalStatistics(dates[0], dates[-1], len(rows), periods_per_year,
                                volatility, drawdown, trailing, rolling)
