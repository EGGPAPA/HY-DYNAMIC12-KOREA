"""Offline HY integrated pilot: mandatory gates, no free cash, stop-first."""
import unittest
from datetime import date,timedelta

from scripts.backtest_hy_integrated_pilot import (
    point_in_time_data_gate, hy_daily_signal, month_after, portfolio,
    GROUPS, INITIAL_CASH, MAX_POSITIONS
)


def fixture(n=250,growth=.002):
    start=date(2023,1,2)
    bars=[]
    d=start
    while len(bars)<n:
        if d.weekday()<5:
            p=1000*(1+growth)**len(bars)
            bars.append({"date":d.isoformat(),"open":p,"high":p*1.013,
                         "low":p*.99,"close":p,"volume":100000})
        d+=timedelta(days=1)
    return bars


def event(bars,ix=10):
    return {"ticker":"000001","name":"Test","market":"KOSPI",
            "group":8,"signal_date":bars[ix-1]["date"],
            "entry_date":bars[ix]["date"],"entry_index":ix,
            "hy_score":90,"sample_rs":95,"distance_pct":1.0}


class TestIntegratedPilot(unittest.TestCase):
    def test_data_gate_fails_without_full_historical_universe(self):
        x=point_in_time_data_gate()
        self.assertFalse(x["eligible_for_production"])
        self.assertFalse(x["checks"]["as_listed_full_KRX_universe"])
        self.assertFalse(x["checks"]["delisted_and_recovery_prices"])

    def test_month_after_rollover(self):
        self.assertEqual(month_after("2024-12"),"2025-01")
        self.assertEqual(month_after("2024-02"),"2024-03")

    def test_hy_proxy_does_not_replace_original_quote_gate(self):
        from rise_live_analysis import entry_signal
        row={"valid":True,"basis":"close","intraday":False,"label":"🟢 상승초입",
             "checks":{"gap":True,"volume":True,"close":True,"risk":True,
                       "stage":False,"score":True,"persistence":False}}
        self.assertTrue(hy_daily_signal(row))
        self.assertFalse(entry_signal(row))
        row["checks"]["risk"]=False
        self.assertFalse(hy_daily_signal(row))

    def test_stop_priority_and_no_same_day_close_signal_buy(self):
        bars=fixture()
        ix=15
        buy=bars[ix]["open"]
        bars[ix]["high"]=buy*1.30
        bars[ix]["low"]=buy*.89
        x=event(bars,ix)
        report,trades,daily=portfolio([x],{"000001":({"ticker":"000001"},bars)},8,"fixed")
        self.assertEqual(report["completed_trades"],1)
        self.assertEqual(trades[0]["entry_date"],bars[ix]["date"])
        self.assertEqual(trades[0]["fills"][0]["reason"],"STOP")
        self.assertLess(trades[0]["return_pct"],-7)
        self.assertLess(report["ending_cash_won"],INITIAL_CASH)
        self.assertLessEqual(report["MDD_pct"],0)

    def test_runner_can_keep_winner_longer_than_fixed(self):
        bars=fixture(310,.009)
        ix=20
        x=event(bars,ix)
        raw={"000001":({"ticker":"000001"},bars)}
        fixed,t1,_=portfolio([x],raw,8,"fixed")
        runner,t2,_=portfolio([x],raw,8,"runner")
        self.assertEqual(fixed["completed_trades"],1)
        self.assertEqual(runner["completed_trades"],1)
        self.assertGreater(t2[0]["return_pct"],t1[0]["return_pct"])

    def test_wrong_group_has_no_entries(self):
        bars=fixture()
        r,trades,daily=portfolio([event(bars)],{"000001":({"ticker":"000001"},bars)},5,"fixed")
        self.assertEqual(r["completed_trades"],0)
        self.assertEqual(r["initial_cash_won"],r["ending_cash_won"])
        self.assertEqual(r["MDD_pct"],0.0)


if __name__=="__main__":
    unittest.main()
