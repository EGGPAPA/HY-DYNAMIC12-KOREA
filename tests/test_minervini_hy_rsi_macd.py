"""Offline non-lookahead and threshold tests for RSI/MACD research filter."""
import unittest
from datetime import date, timedelta
from scripts.compare_minervini_hy_rsi_macd import indicator_at, B_BASELINE


def candles(n=110):
    base=date(2026,6,1)
    out=[]
    for i in range(n):
        d=base+timedelta(days=i)
        v=100+0.2*i+4*((i%11)/11)
        out.append({"date":d.isoformat(),"open":v-.1,"high":v+.5,
                    "low":v-.5,"close":v,"volume":100000})
    return out


class TestRSIMACDPilot(unittest.TestCase):
    def test_same_signal_ignores_future_prices(self):
        b=candles()
        day=b[79]["date"]
        original=indicator_at(b[:80],day)
        adjusted=b[:80]+[{**x,"close":x["close"]*2} for x in b[80:]]
        self.assertEqual(original,indicator_at(adjusted,day))
        self.assertEqual(original["both_pass"],original["rsi_pass"] and original["macd_pass"])

    def test_missing_signal_day_rejected(self):
        b=candles()
        with self.assertRaises(ValueError):
            indicator_at(b,"2025-01-01")

    def test_pilot_uses_all_5_baseline_trades(self):
        self.assertEqual(len(B_BASELINE),5)
        self.assertEqual(len({x[0] for x in B_BASELINE}),5)


if __name__ == "__main__":
    unittest.main()
