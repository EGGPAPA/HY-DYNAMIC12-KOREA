"""Offline tests for HY standalone daily proxy; no quotes, network, or orders."""
import unittest
from datetime import date, timedelta, datetime

import pandas as pd

from scripts.backtest_hy_5of7_daily_proxy import (
    select_universe, simulate_trade, daily_proxy, pass_macd_hist, pass_rsi,
    evaluate_stock, indicators, HOLD_BARS
)
from rise_live_analysis import evaluate_current, entry_signal, KST


def series(n=120):
    d=date(2026,2,2)
    out=[]
    while len(out)<n:
        if d.weekday()<5:
            i=len(out)
            p=1000+i*2
            out.append({"date":d.isoformat(),"open":p,"high":p+8,
                        "low":p-8,"close":p+1,"volume":100000})
        d+=timedelta(days=1)
    out[-21]["volume"]=250000
    return out


class StandaloneHYTests(unittest.TestCase):
    def test_no_same_day_buy_and_stop_before_profit(self):
        bars=series()
        i=95
        buy=bars[i+1]["open"]
        bars[i+1]["low"]=buy*.90
        bars[i+1]["high"]=buy*1.20
        trade=simulate_trade(bars,i)
        self.assertEqual(trade["entry_date"],bars[i+1]["date"])
        self.assertTrue(trade["stopped"])
        self.assertEqual(trade["fills"][0]["reason"],"STOP")
        self.assertLess(trade["pnl_pct"],-7)

    def test_no_complete_20_bar_horizon(self):
        bars=series()
        self.assertIsNone(simulate_trade(bars,len(bars)-12))

    def test_universe_uses_both_markets_and_excludes_preferred(self):
        src=[{"종목코드":"000001","종목명":"A","시장":"KOSPI"},
             {"종목코드":"000002","종목명":"B우","시장":"KOSPI"},
             {"종목코드":"000003","종목명":"C","시장":"KOSPI"},
             {"종목코드":"000004","종목명":"D","시장":"KOSDAQ"}]
        got,c=select_universe(src,2)
        self.assertEqual([x["ticker"] for x in got],["000001","000003","000004"])
        self.assertEqual(c,{"KOSPI":2,"KOSDAQ":1})

    def test_hy_daily_pass_is_not_live_alert(self):
        c={"gap":True,"volume":True,"close":True,"risk":True,
           "stage":True,"score":False,"persistence":False}
        row={"valid":True,"intraday":False,"basis":"close","label":"🟣 1차 매수구간","checks":c}
        self.assertTrue(daily_proxy(row))
        self.assertFalse(entry_signal(row))
        changed={**row,"checks":{**c,"gap":False,"score":True}}
        self.assertFalse(daily_proxy(changed))

    def test_indicator_uses_historical_prefix_only(self):
        closes=[x["close"] for x in series()]
        rsi1,macd1=indicators(closes[:90])
        rsi2,macd2=indicators(closes[:90]+[99999.]*30)
        self.assertAlmostEqual(rsi1[-1],rsi2[89])
        self.assertAlmostEqual(macd1[-1],macd2[89])
        self.assertFalse(pass_macd_hist([-.2,-.1],1))
        self.assertTrue(pass_rsi([53],0))

    def test_stock_screen_not_using_future_bars_to_evaluate_past(self):
        rows=series()
        row={"ticker":"000001","name":"Test","market":"KOSPI"}
        a,ta=evaluate_stock(row,rows,rows[80]["date"],rows[100]["date"])
        changed=[dict(x) for x in rows]
        changed[-5]["close"]*=1.05
        b,tb=evaluate_stock(row,changed,rows[80]["date"],rows[100]["date"])
        self.assertEqual(a["raw_signal_counts"],b["raw_signal_counts"])


if __name__=="__main__":
    unittest.main()
