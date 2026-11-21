"""Synthetic, offline checks: following-day execution, stop-first fills, no future data."""
import unittest
from datetime import date, timedelta
from pathlib import Path

from scripts.backtest_minervini_hy_daily_proxy import simulate_trade, hy_close_proxy, archive_price_matches
from rise_live_analysis import entry_signal


def fixture():
    first=date(2026, 9, 1)
    records=[]
    for i in range(110):
        dt=first+timedelta(days=i)
        if dt.weekday()>=5:continue
        price=100+0.5*len(records)
        records.append({"date":dt.isoformat(),"open":price,"high":price+1,
                        "low":price-1,"close":price+.2,"volume":100000})
    return records


class DailyProxyTests(unittest.TestCase):
    def test_backtest_trade_enters_after_signal_date(self):
        bars=fixture()
        t=simulate_trade(bars,bars[10]["date"],horizon=20)
        self.assertIsNotNone(t)
        self.assertEqual(t["entry_date"],bars[11]["date"])
        self.assertNotEqual(t["entry_date"],t["signal_date"])

    def test_stop_first_if_same_day_high_hits_target(self):
        bars=fixture()
        day=bars[10]["date"]
        buy=bars[11]["open"]
        bars[11]["low"]=buy*.90
        bars[11]["high"]=buy*1.3
        t=simulate_trade(bars,day,horizon=20)
        self.assertEqual(t["fills"][0][0],"stop")
        self.assertEqual(len(t["fills"]),1)
        self.assertLess(t["pnl_pct"],-7)

    def test_split_adjustment_mismatch_excludes_signal(self):
        self.assertTrue(archive_price_matches(7240, 7240))
        self.assertTrue(archive_price_matches(10000, 10200))
        self.assertFalse(archive_price_matches(10880, 2178))
        self.assertFalse(archive_price_matches(10000, 7000))

    def test_no_future_bar_means_no_full_horizon(self):
        bars=fixture()
        self.assertIsNone(simulate_trade(bars,bars[-10]["date"],horizon=20))

    def test_live_hy_gate_not_claimed_from_close_data(self):
        x={"valid":True,"intraday":False,"basis":"close","label":"🟢",
           "checks":{"gap":True,"volume":True,"close":True,"risk":True,"stage":True}}
        self.assertFalse(entry_signal(x))
        text=Path("scripts/backtest_minervini_hy_daily_proxy.py").read_text(encoding="utf8")
        self.assertIn("time_normalized_intraday_volume",text)
        self.assertIn("live_signal_persistence",text)


if __name__=="__main__":
    unittest.main()
