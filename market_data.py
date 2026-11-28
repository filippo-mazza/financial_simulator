"""Offline market snapshot contract and manual Eurostat import.

No network access occurs on import or snapshot reads. Historical observations
are descriptive, not forecasts. Only publish redistribution-cleared series.
"""
from dataclasses import dataclass, asdict
from datetime import date, datetime, timezone
from math import isfinite
from pathlib import Path
from typing import Protocol
import json


@dataclass(frozen=True)
class MarketSeries:
    indicator: str
    geography: str
    periods_per_year: int
    unit: str
    currency: str | None
    return_type: str
    source_url: str
    retrieved_at: str
    revision: str | None
    license_url: str
    observations: tuple[tuple[date, float], ...]
    flags: tuple[tuple[date, str], ...] = ()

    def __post_init__(self):
        if self.periods_per_year not in (1, 4, 12):
            raise ValueError("Unsupported frequency")
        if not self.observations:
            raise ValueError("Empty series")
        if any(not isfinite(v) or v <= 0 for _, v in self.observations):
            raise ValueError("Index levels must be positive and finite")
        dates = [d for d, _ in self.observations]
        if dates != sorted(set(dates)):
            raise ValueError("Dates must be unique and ordered")
        if not all((self.source_url, self.license_url, self.retrieved_at)):
            raise ValueError("Missing provenance")


class MarketProvider(Protocol):
    def parse(self, payload: dict, retrieved_at: str) -> MarketSeries: ...


HPI_URL = ("https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/"
           "data/prc_hpi_q?lang=EN&geo=ES&purchase=TOTAL&unit=I15_Q&freq=Q")
LICENSE_URL = "https://ec.europa.eu/eurostat/help/copyright-notice"


class SpainEurostatHPI:
    """Normalize a separately downloaded, single-series JSON-stat response."""
    def parse(self, payload, retrieved_at):
        ids, sizes = payload["id"], payload["size"]
        if len(ids) != len(sizes) or set(ids) != {"freq", "unit", "purchase", "geo", "time"}:
            raise ValueError("Unexpected dimensions")
        dimensions = payload["dimension"]
        def categories(key):
            idx = dimensions[key]["category"]["index"]
            return idx if isinstance(idx, list) else [
                k for k, _ in sorted(idx.items(), key=lambda item: item[1])]
        expected = {"freq": "Q", "unit": "I15_Q", "purchase": "TOTAL", "geo": "ES"}
        for key, wanted in expected.items():
            if categories(key) != [wanted] or sizes[ids.index(key)] != 1:
                raise ValueError("Unexpected filter: " + key)
        times = categories("time")
        if len(times) != sizes[ids.index("time")]:
            raise ValueError("Inconsistent time dimension")
        values = payload["value"]
        statuses = payload.get("status", {})
        rows, flags = [], []
        def at(items, i):
            if isinstance(items, list):
                return items[i] if i < len(items) else None
            return items.get(str(i))
        for i, period in enumerate(times):
            year, quarter = period.split("-Q")
            q = int(quarter)
            if q not in (1,2,3,4):
                raise ValueError("Invalid quarter")
            day = date(int(year), 1 + 3*(q-1), 1)
            value = at(values, i)
            if value is not None:
                rows.append((day, float(value)))
            status = at(statuses, i)
            if status:
                flags.append((day, str(status)))
        return MarketSeries("house_price_index", "ES", 4, "2015=100", None,
                            "nominal_price_index", HPI_URL, retrieved_at,
                            payload.get("updated"), LICENSE_URL,
                            tuple(sorted(rows)), tuple(sorted(flags)))


def write_snapshot(path, series):
    """Manual publication; caller must verify licensing and source observations."""
    records = []
    for item in series:
        record = asdict(item)
        for key in ("observations", "flags"):
            record[key] = [[d.isoformat(), v] for d, v in record[key]]
        records.append(record)
    target = Path(path)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps({"schema_version": 1, "series": records},
                                    indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(target)


def read_snapshot(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError("Unsupported snapshot version")
    result = []
    for row in data["series"]:
        row = dict(row)
        for key in ("observations", "flags"):
            row[key] = tuple((date.fromisoformat(d), v) for d, v in row.get(key, []))
        result.append(MarketSeries(**row))
    return tuple(result)
