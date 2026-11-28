import tempfile
import unittest
from pathlib import Path
from market_data import SpainEurostatHPI, read_snapshot, write_snapshot


def fixture():
    categories = {"freq": ["Q"], "unit": ["I15_Q"], "purchase": ["TOTAL"],
                  "geo": ["ES"], "time": ["2020-Q1", "2020-Q2", "2020-Q3"]}
    return {"id": list(categories), "size": [1,1,1,1,3],
            "dimension": {key: {"category": {"index": values}}
                          for key, values in categories.items()},
            "value": {"0": 100, "2": 105}, "status": {"2": "p"},
            "updated": "synthetic"}


class MarketDataTests(unittest.TestCase):
    def test_sparse_values_and_roundtrip(self):
        series = SpainEurostatHPI().parse(fixture(), "2020-12-01T00:00:00Z")
        self.assertEqual(len(series.observations), 2)
        self.assertEqual(series.observations[1][1], 105)
        self.assertEqual(series.flags[0][1], "p")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            write_snapshot(path, [series])
            self.assertEqual(read_snapshot(path), (series,))

    def test_wrong_geo_rejected(self):
        payload = fixture()
        payload["dimension"]["geo"]["category"]["index"] = ["FR"]
        with self.assertRaises(ValueError):
            SpainEurostatHPI().parse(payload, "2020-12-01T00:00:00Z")

    def test_invalid_index_rejected(self):
        payload = fixture()
        payload["value"]["0"] = 0
        with self.assertRaises(ValueError):
            SpainEurostatHPI().parse(payload, "2020-12-01T00:00:00Z")
