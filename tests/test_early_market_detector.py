"""Offline tests: no brokerage keys, network, orders, or alerts."""
import unittest
from datetime import date, timedelta

from early_market_detector import analyze_early, rank_early


def sample(start=100.0, count=100):
    base = date(2026, 1, 1)
    bars = []
    for i in range(count):
        bars.append({"date": (base + timedelta(days=i)).isoformat(),
                     "close": start + i * 0.1, "volume": 500_000})
    return bars


class EarlyDetectorTests(unittest.TestCase):
    def test_completed_bars_no_future_leakage(self):
        stock = sample()
        benchmark = sample(200)
        day = stock[90]["date"]
        original = analyze_early(stock[:91], benchmark[:91], day)
        future = analyze_early(stock + [
            {"date": "2026-12-01", "close": 9000, "volume": 9000000}
        ], benchmark, day)
        self.assertEqual(original, future)
        self.assertTrue(original["valid"])

    def test_duplicate_and_stale_bars_are_held(self):
        bars = sample()
        day = bars[-1]["date"]
        duplicated = bars + [dict(bars[-1])]
        self.assertFalse(analyze_early(duplicated, bars, day)["valid"])
        self.assertFalse(analyze_early(bars[:-1], bars, day)["valid"])

    def test_missing_benchmark_date_is_held(self):
        bars = sample()
        day = bars[-1]["date"]
        benchmark = [x for x in bars if x["date"] != bars[-6]["date"]]
        result = analyze_early(bars, benchmark, day)
        self.assertFalse(result["valid"])
        self.assertIn("불일치", result["reason"])

    def test_zero_volume_and_split_are_held(self):
        bars = sample()
        day = bars[-1]["date"]
        zero = [dict(x) for x in bars]
        zero[-1]["volume"] = 0
        self.assertFalse(analyze_early(zero, bars, day)["valid"])
        split = [dict(x) for x in bars]
        split[-1]["close"] *= 2
        self.assertFalse(analyze_early(split, bars, day)["valid"])

    def test_rank_only_verified_candidates(self):
        def item(code, score, excess):
            return {"ticker": code, "early_signal": {
                "candidate": True, "signal_count": score,
                "excess5_pct": excess, "volume_ratio": 2.0}}
        data = [item("000001", 3, 2), item("000002", 5, 1),
                item("000003", 4, 6), {"ticker": "000004", "early_signal": {
                    "candidate": False}}]
        self.assertEqual([x["ticker"] for x in rank_early(data, 2)],
                         ["000002", "000003"])


if __name__ == "__main__":
    unittest.main()
