"""Offline regression tests: 5/8~8/8 historical selection, no post-date leakage."""
import unittest
from datetime import date,timedelta

from scripts.backtest_minervini_5to8_forward import (
    select_sample, price_history_good, outcomes, monthly_anchors, summarize,
)
from minervini_trend import evaluate_trend


def fake_rows(n=350):
    d=date(2024,1,1)
    rows=[]
    while len(rows)<n:
        if d.weekday()<5:
            i=len(rows)
            close=100+0.3*i
            rows.append({"date":d.isoformat(),"open":close,
                         "high":close*1.01,"low":close*.99,
                         "close":close,"volume":100000})
        d+=timedelta(days=1)
    return rows


class MinerviniForwardTests(unittest.TestCase):
    def test_market_sample_not_all_stocks(self):
        raw=[{"종목코드":"000001","종목명":"A","시장":"KOSPI"},
             {"종목코드":"000002","종목명":"B","시장":"KOSDAQ"},
             {"종목코드":"000003","종목명":"C우","시장":"KOSPI"}]
        rows, counts=select_sample(raw,1)
        self.assertEqual(len(rows),2)
        self.assertEqual(counts,{"KOSPI":1,"KOSDAQ":1})

    def test_past_score_ignores_future(self):
        b=fake_rows()
        pivot=270
        s=b[pivot]["date"]
        before=evaluate_trend(b[:pivot+1],asof=s,rs_percentile=80)
        after=evaluate_trend(b[:pivot+1]+[{**x,"close":x["close"]*2}
                                             for x in b[pivot+1:]],asof=s,rs_percentile=80)
        self.assertTrue(before["valid"])
        self.assertFalse(after["valid"])  # evaluator requires exact asof, never a future bar
        self.assertEqual(len(before["checks"]),8)

    def test_split_check_only_uses_prefix(self):
        b=fake_rows()
        self.assertTrue(price_history_good(b,270))
        mutated=[dict(r) for r in b]
        mutated[280]["close"]*=2
        self.assertTrue(price_history_good(mutated,270))
        self.assertFalse(price_history_good(mutated,290))

    def test_forward_returns_cannot_use_incomplete_horizon(self):
        b=fake_rows()
        self.assertIsNone(outcomes(b, len(b)-60))
        x=outcomes(b,200)
        self.assertIsNotNone(x)
        self.assertIn("3m",x)
        self.assertIn("6m",x)
        self.assertGreater(x["6m"]["return_pct"],x["3m"]["return_pct"])

    def test_monthly_anchors_use_last_day_in_each_month(self):
        b=fake_rows()
        r=monthly_anchors({"000001":({"ticker":"000001"},b)})
        self.assertTrue(r)
        for month,asof in r.items():
            self.assertEqual(asof,max(x["date"] for x in b if x["date"][:7]==month))

    def test_group_summary_does_not_claim_portfolio_return(self):
        rows=[{"ticker":"A","return_pct":10,"max_favorable_pct":20,
              "max_adverse_pct":-5,"hit20":True,"hit50":False,"hit100":False,"down10":False}]
        result=summarize(rows)
        self.assertEqual(result["events"],1)
        self.assertEqual(result["unique_tickers"],1)
        self.assertNotIn("CAGR",result)


if __name__=="__main__":
    unittest.main()
