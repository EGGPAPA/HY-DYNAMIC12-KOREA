"""Offline regression tests for sanitized KIS market-context failures."""
import unittest
from unittest.mock import patch

from rise_live_data import get_market_context


class MarketContextDiagnosticsTests(unittest.TestCase):
    @patch("rise_live_data._index_trading_dates", return_value=([], "KIS 인증 실패 또는 설정 없음"))
    @patch("rise_live_data._calendar", return_value=([], "KIS 인증 실패 또는 설정 없음"))
    def test_token_failure_reports_both_paths(self, calendar, index):
        result = get_market_context()
        self.assertIn("거래일 달력: KIS 인증 실패", result["error"])
        self.assertIn("시장지수 일봉: KIS 인증 실패", result["error"])
        self.assertNotIn("Bearer", result["error"])

    @patch("rise_live_data.index_market_context", return_value={"error": "직전 거래일 확인 실패"})
    @patch("rise_live_data._index_trading_dates", return_value=(["20261118"], ""))
    @patch("rise_live_data._calendar", return_value=([], "KIS 조회 실패 HTTP 403 (API403)"))
    def test_calendar_api_restricted_and_index_missing(self, calendar, index, fallback):
        result = get_market_context()
        self.assertIn("KIS 조회 실패 HTTP 403 (API403)", result["error"])
        self.assertIn("직전 거래일 확인 실패", result["error"])

    @patch("rise_live_data.index_market_context", return_value={
        "day": "20261118", "previous": "20261117", "intraday": False, "basis": "close"})
    @patch("rise_live_data._index_trading_dates", return_value=(["20261117", "20261118"], ""))
    @patch("rise_live_data._calendar", return_value=([], "KIS 조회 실패 HTTP 403 (API403)"))
    def test_index_success_still_allows_fallback(self, calendar, index, fallback):
        result = get_market_context()
        self.assertEqual(result["day"], "20261118")
        self.assertNotIn("error", result)


if __name__ == "__main__":
    unittest.main()
