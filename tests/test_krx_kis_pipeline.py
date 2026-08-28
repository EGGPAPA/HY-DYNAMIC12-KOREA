import unittest
from datetime import datetime, timedelta

import pandas as pd

from krx_kis_pipeline import build_first_pass_screen, collect_krx_ohlcv


class FakeStock:
    def get_market_ohlcv_by_ticker(self, date_text, market="KOSPI"):
        if market == "KOSDAQ":
            return pd.DataFrame()
        day = datetime.strptime(date_text, "%Y%m%d")
        if day.weekday() >= 5:
            return pd.DataFrame()
        offset = (day - datetime(2026, 1, 1)).days
        return pd.DataFrame(
            {
                "종가": [10000 + offset * 10, 500],
                "거래량": [500000, 10],
                "거래대금": [5_000_000_000, 5_000],
            },
            index=["000001", "000002"],
        )


class KrxKisPipelineTest(unittest.TestCase):
    def test_collects_market_wide_sessions(self):
        history, as_of = collect_krx_ohlcv(
            FakeStock(), end_date="20260130", sessions=3, max_calendar_days=7
        )
        self.assertEqual(history["기준일"].nunique(), 3)
        self.assertEqual(as_of, "20260130")
        self.assertIn("000001", set(history["종목코드"]))

    def test_builds_and_filters_first_pass_candidates(self):
        dates = pd.date_range("2026-01-01", periods=22, freq="B")
        records = []
        for index, date in enumerate(dates):
            records.extend(
                [
                    {
                        "종목코드": "000001",
                        "시장": "KOSPI",
                        "기준일": date,
                        "종가": 10000 + index * 100,
                        "거래량": 500000,
                        "거래대금": 5_000_000_000,
                    },
                    {
                        "종목코드": "000002",
                        "시장": "KOSPI",
                        "기준일": date,
                        "종가": 500,
                        "거래량": 10,
                        "거래대금": 5_000,
                    },
                ]
            )
        universe = pd.DataFrame(
            [
                {"종목코드": "000001", "종목명": "정상종목", "시장": "KOSPI"},
                {"종목코드": "000002", "종목명": "저유동성", "시장": "KOSPI"},
            ]
        )
        screen = build_first_pass_screen(
            pd.DataFrame(records),
            universe,
            {"000001": {"외국인순매수": 100, "기관순매수": 50}},
        )
        self.assertEqual(screen["종목코드"].tolist(), ["000001"])
        self.assertGreater(float(screen.iloc[0]["1차점수"]), 0)


if __name__ == "__main__":
    unittest.main()
