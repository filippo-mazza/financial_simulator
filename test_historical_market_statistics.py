import unittest
from datetime import date
from historical_market_statistics import historical_statistics


class HistoricalStatisticsTests(unittest.TestCase):
    def test_constant_growth(self):
        rows = [(date(2000+i, 1, 1), 100*1.05**i) for i in range(21)]
        result = historical_statistics(rows, 1)
        for horizon in (5, 10, 20):
            self.assertAlmostEqual(result.annualized_returns[horizon], .05)
            self.assertEqual(len(result.rolling_returns[horizon]), 21-horizon)
        self.assertAlmostEqual(result.annualized_volatility, 0)
        self.assertEqual(result.maximum_drawdown, 0)

    def test_drawdown_and_short_coverage(self):
        result = historical_statistics([(date(2020+i, 1, 1), v)
                                        for i,v in enumerate([100, 120, 90, 110])], 1)
        self.assertAlmostEqual(result.maximum_drawdown, -.25)
        self.assertIsNone(result.annualized_returns[5])
        self.assertEqual(result.rolling_returns[5], ())
        self.assertGreater(result.annualized_volatility, 0)

    def test_quarterly_calendar_windows(self):
        rows = [(date(2020+i//4, 1+3*(i%4), 1), 100*1.04**(i/4))
                for i in range(21)]
        result = historical_statistics(rows, 4)
        self.assertAlmostEqual(result.annualized_returns[5], .04)
        self.assertIsNone(result.annualized_returns[10])

    def test_invalid_series(self):
        for rows in ([], [(date(2020,1,1), 0)],
                     [(date(2020,1,1), float("nan"))],
                     [(date(2020,1,1), 100), (date(2022,1,1), 120)],
                     [(date(2020,1,1), 100), (date(2020,1,2), 120)]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                historical_statistics(rows, 1)

    def test_single_observation(self):
        result = historical_statistics([(date(2020,1,1), 100)], 1)
        self.assertIsNone(result.annualized_volatility)
        self.assertEqual(result.maximum_drawdown, 0)


if __name__ == "__main__":
    unittest.main()
