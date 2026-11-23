"""Offline HY early-screen reference-price regression tests.

No quote feed, trading, monitor-state writes or automatic orders.
"""
import unittest
from datetime import date, timedelta

from early_entry_levels import (
    compute_price_levels, checked_levels, decision_text, round_reference,
)
from early_market_detector import analyze_early
from early_market_dashboard import candidate_table


def fixture(n=100, asof_start=date(2026, 6, 1)):
    rows=[]
    for i in range(n):
        close=10000.0 if i<94 else 10000.0+(i-93)*120.0
        rows.append({"date":(asof_start+timedelta(days=i)).isoformat(),
                     "open":close-20,"high":close+50,"low":close-50,
                     "close":close,"volume":2_000_000. if i==n-1 else 500_000.})
    return rows


class EntryPriceLevelTests(unittest.TestCase):
    def test_rounding_and_hy_close_benchmark(self):
        rows=fixture()
        asof=rows[-1]["date"]
        result=compute_price_levels(rows,asof)
        ma=sum(x["close"] for x in rows[-20:])/20
        stop=min(rows[-1]["close"]*.97,
                 max(ma*.96,min(x["close"] for x in rows[-10:])*.98))
        self.assertAlmostEqual(result["ma20"],ma,places=3)
        self.assertEqual(result["watch_low"],round_reference(ma))
        self.assertEqual(result["watch_high"],round_reference(ma*1.03,up=True))
        self.assertEqual(result["breakout_ref"],
                         round_reference(max(x["close"] for x in rows[-21:-1]),up=True))
        self.assertEqual(result["stop_ref"],round_reference(stop))
        self.assertLess(result["stop_ref"],rows[-1]["close"])

    def test_missing_or_future_day_never_used(self):
        rows=fixture()
        asof=rows[-1]["date"]
        with self.assertRaises(ValueError):
            compute_price_levels(rows[:-1],asof)
        with self.assertRaises(ValueError):
            compute_price_levels(rows+[dict(rows[-1])],asof)
        modified=rows+[{"date":"2026-12-30","close":900000,"volume":100000}]
        with self.assertRaises(ValueError):
            compute_price_levels(modified,asof)

    def test_split_and_zero_volume_reference_holds(self):
        rows=fixture()
        rows[90]["close"]*=2
        with self.assertRaises(ValueError):
            compute_price_levels(rows,rows[-1]["date"])
        rows=fixture()
        rows[-1]["volume"]=0
        with self.assertRaises(ValueError):
            compute_price_levels(rows,rows[-1]["date"])

    def test_early_snapshot_carries_dated_prices_without_changing_signal_rules(self):
        bars=fixture()
        asof=bars[-1]["date"]
        benchmark=[{**x,"close":20000.} for x in bars]
        report=analyze_early(bars,benchmark,asof)
        self.assertTrue(report["valid"])
        self.assertTrue(report["candidate"])
        self.assertIn("entry_levels",report)
        levels=checked_levels(report,asof)
        self.assertIsNotNone(levels)
        self.assertEqual(levels["breakout_ref"],round_reference(
            max(r["close"] for r in bars[-21:-1]),up=True))
        self.assertEqual(report["signal_count"],sum(report["signals"].values()))

    def test_legacy_snapshot_does_not_fabricate_breakout_stop(self):
        asof="2026-11-20"
        legacy={"ticker":"064550","name":"바이오니아","market":"KOSDAQ",
                "early_signal":{"asof":asof,"valid":True,"candidate":True,
                                "close":7280,"gap20_pct":7.84,"signal_count":5,
                                "rsi14":53.17,"macd_hist":62.8,"volume_ratio":15.524,
                                "excess5_pct":18.19}}
        displayed=candidate_table([legacy],asof)
        self.assertEqual(displayed.iloc[0]["1차 관찰가"],"—")
        self.assertEqual(displayed.iloc[0]["돌파 확인가"],"—")
        self.assertEqual(displayed.iloc[0]["손절 참고가"],"—")
        self.assertIn("보류",displayed.iloc[0]["최종 매수 판단"])

    def test_recent_level_can_only_say_recheck_not_buy(self):
        rows=fixture()
        asof=rows[-1]["date"]
        l=compute_price_levels(rows,asof)
        signal={"close":rows[-1]["close"],"asof":asof}
        self.assertIsNotNone(checked_levels(signal,asof,l))
        self.assertIn("HY 재확인",decision_text(signal,l))
        self.assertIn("보류",decision_text(signal,l,stale=True))
        bad={**l,"reference_close":rows[-1]["close"]*1.10}
        self.assertIsNone(checked_levels(signal,asof,bad))
        self.assertIn("보류",decision_text(signal,None))

    def test_prices_follow_signal_day_only(self):
        bars=fixture()
        asof=bars[-1]["date"]
        x=compute_price_levels(bars,asof)
        future={**bars[-1],"date":"2026-12-31","close":50000.0,"volume":1e6}
        self.assertEqual(x,compute_price_levels([b for b in bars+[future]
                                                 if b["date"]<=asof],asof))


if __name__=="__main__":
    unittest.main()
