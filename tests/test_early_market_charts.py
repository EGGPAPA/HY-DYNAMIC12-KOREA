"""Tests for the independent early-market OHLCV / indicator chart screen."""
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from early_market_charts import parse_ohlcv, chart_rows, get_naver_chart_rows
from early_market_detector import analyze_early


def synthetic_bars(n=100):
    start=date(2026,6,1)
    rows=[]
    for i in range(n):
        close=12000+25*i+(80 if i%7==0 else 0)
        rows.append({"date":(start+timedelta(days=i)).isoformat(),
                     "open":float(close-15),"high":float(close+80),
                     "low":float(close-80),"close":float(close),
                     "volume":float(500000+i*500)})
    return rows


def naver_xml(rows):
    items=[]
    for r in rows:
        d=r["date"].replace("-","")
        values=(d,int(r["open"]),int(r["high"]),int(r["low"]),int(r["close"]),int(r["volume"]))
        items.append('<item data="'+'|'.join(str(x) for x in values)+'"/>')
    return ('<protocol><chartdata>' + ''.join(items) + '</chartdata></protocol>').encode("euc-kr")


class ChartHistoryTests(unittest.TestCase):
    def test_asof_never_uses_future_bar(self):
        bars=synthetic_bars(110)
        asof=bars[99]["date"]
        left=parse_ohlcv(naver_xml(bars[:100]),asof)
        right=parse_ohlcv(naver_xml(bars),asof)
        self.assertEqual(left,right)
        self.assertEqual(len(left),100)

    def test_duplicate_and_stale_are_rejected(self):
        bars=synthetic_bars(100)
        with self.assertRaises(ValueError):
            parse_ohlcv(naver_xml(bars+[bars[-1]]),bars[-1]["date"])
        with self.assertRaises(ValueError):
            parse_ohlcv(naver_xml(bars[:-1]),bars[-1]["date"])

    def test_screen_rsi_macd_agree_with_chart_values(self):
        bars=synthetic_bars(100)
        asof=bars[-1]["date"]
        screen=[{"date":r["date"],"close":r["close"],"volume":r["volume"]} for r in bars]
        evaluated=analyze_early(screen,screen,asof)
        self.assertTrue(evaluated["valid"])
        chart=chart_rows(bars)
        self.assertEqual(chart[-1]["date"],asof)
        self.assertAlmostEqual(chart[-1]["rsi14"],evaluated["rsi14"],places=2)
        self.assertAlmostEqual(chart[-1]["hist"],evaluated["macd_hist"],places=4)
        self.assertAlmostEqual(chart[-1]["ma20"],
                               sum(r["close"] for r in bars[-20:])/20,places=4)

    def test_symbol_validated_before_network(self):
        with patch("early_market_charts.urlopen") as fetched:
            with self.assertRaises(ValueError):
                get_naver_chart_rows("5;rm -rf /",synthetic_bars()[-1]["date"])
            fetched.assert_not_called()

    def test_sidebar_page_and_main_screen_separation(self):
        page=Path("pages/5_전시장_선행탐지.py").read_text()
        main=Path("rise_timing_watchlist_ui.py").read_text()
        self.assertIn("render_early_market_dashboard()",page)
        self.assertIn('st.page_link("pages/5_전시장_선행탐지.py"',main)
        self.assertNotIn('with st.expander("🔎 전 시장 선행 탐지 · 시험"',main)


if __name__=="__main__":
    unittest.main()
