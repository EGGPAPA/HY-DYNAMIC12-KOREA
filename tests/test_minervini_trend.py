"""Offline regression tests for isolated Minervini trend template."""
import unittest
from datetime import date, timedelta
from pathlib import Path

from minervini_trend import CHECK_LABELS, evaluate_trend, assign_market_rs


def sample(n=300, growth=1.02):
    last = date(2026, 11, 20)
    days = [last-timedelta(days=i) for i in range(600)][::-1]
    days = [d for d in days if d.weekday()<5][-n:]
    prices = [100 * growth**i for i in range(n)]
    return [{"date": d.isoformat(), "open": p, "high": p*1.01,
             "low": p*0.99, "close": p}
            for d, p in zip(days, prices)]


class MinerviniTests(unittest.TestCase):
    def test_rising_but_unranked_is_pending_not_eight_of_eight(self):
        x=evaluate_trend(sample(), asof="2026-11-20")
        self.assertTrue(x["valid"])
        self.assertEqual((x["passed"],x["pending"]), (7,1))
        self.assertFalse(x["all_pass"])
        self.assertIsNone(x["checks"]["rs_rank_70"])

    def test_valid_full_market_rank_can_pass_all_eight(self):
        x=evaluate_trend(sample(), asof="2026-11-20", rs_percentile=86)
        self.assertEqual(x["passed"], 8)
        self.assertTrue(x["all_pass"])
        self.assertEqual(len(CHECK_LABELS), 8)
        y=evaluate_trend(sample(), asof="2026-11-20", rs_percentile=69.9)
        self.assertEqual((y["passed"],y["failed"]),(7,1))
        self.assertFalse(y["all_pass"])

    def test_incomplete_history_is_never_marked_fail_or_pass(self):
        x=evaluate_trend(sample(120),asof="2026-11-20",rs_percentile=90)
        self.assertFalse(x["valid"])
        self.assertEqual(x["pending"],8)

    def test_duplicates_and_wrong_date_are_rejected(self):
        rows=sample(); rows[-1]["date"]="2026-11-19"
        self.assertFalse(evaluate_trend(rows,asof="2026-11-20")["valid"])

    def test_relative_strength_ranks_need_market_coverage(self):
        a=evaluate_trend(sample(growth=1.02),asof="2026-11-20")
        b=evaluate_trend(sample(growth=1.01),asof="2026-11-20")
        c=evaluate_trend(sample(growth=1.005),asof="2026-11-20")
        ready=assign_market_rs({"a":a,"b":b,"c":c},universe_size=3,minimum_count=3)
        self.assertTrue(ready["ready"])
        self.assertEqual(ready["ranks"]["a"],100)
        self.assertEqual(ready["ranks"]["c"],0)
        not_ready=assign_market_rs({"a":a,"b":b},universe_size=3,minimum_count=2)
        self.assertFalse(not_ready["ready"])

    def test_downtrend_fails_without_modifying_hy(self):
        x=evaluate_trend(sample(growth=0.995),asof="2026-11-20",rs_percentile=90)
        self.assertTrue(x["valid"])
        self.assertFalse(x["all_pass"])
        self.assertGreaterEqual(x["failed"],4)
        import rise_live_analysis
        source=Path("rise_live_analysis.py").read_text(encoding="utf-8")
        self.assertIn("MANDATORY_KEYS = ('gap', 'volume', 'close', 'risk')",source)
        self.assertIn("AUXILIARY_KEYS = ('stage', 'score', 'persistence')",source)


if __name__ == "__main__":
    unittest.main()
