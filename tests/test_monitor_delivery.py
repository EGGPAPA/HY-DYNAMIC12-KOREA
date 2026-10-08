"""Offline regression coverage: no real credentials, requests, or notifications."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))


class NetworkError(Exception):
    pass


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.requests = types.SimpleNamespace(post=Mock(), RequestException=NetworkError)
        self.modules = patch.dict(sys.modules, {'requests': self.requests, 'yfinance': types.SimpleNamespace()})
        self.modules.start()
        for name in ('monitor_kakao', 'monitor_holdings'):
            sys.modules.pop(name, None)
        import monitor_kakao
        import monitor_holdings
        self.kakao, self.holdings = monitor_kakao, monitor_holdings
        self.env = patch.dict(os.environ, {'KAKAO_REST_API_KEY': 'fake', 'KAKAO_REFRESH_TOKEN': 'fake'})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.modules.stop()
        for name in ('monitor_kakao', 'monitor_holdings'):
            sys.modules.pop(name, None)

    def response(self, data, ok=True, status=200):
        return types.SimpleNamespace(ok=ok, status_code=status, json=lambda: data)

    def test_auth_error_safe_and_prevents_send(self):
        self.requests.post.return_value = self.response({'error_code': 'KOE322', 'error_description': 'SECRET'}, False, 400)
        with self.assertRaises(self.kakao.KakaoError) as caught:
            self.kakao.send_text('alert', 'https://example.com', 'open')
        self.assertIn('KOE322', str(caught.exception))
        self.assertNotIn('SECRET', str(caught.exception))
        self.assertEqual(self.requests.post.call_count, 1)

    def test_delivery_rejection_is_failure(self):
        self.requests.post.side_effect = [self.response({'access_token': 'fake'}), self.response({'code': -402}, False, 403)]
        with self.assertRaises(self.kakao.KakaoError):
            self.kakao.send_text('alert', 'https://example.com', 'open')

    def test_missing_credentials_are_failure(self):
        with patch.dict(os.environ, {'KAKAO_REFRESH_TOKEN': ''}):
            with self.assertRaises(self.kakao.KakaoError):
                self.kakao.access_token()
        self.requests.post.assert_not_called()

    def test_network_exception_never_exposes_request(self):
        self.requests.post.side_effect = NetworkError('SECRET')
        with self.assertRaises(self.kakao.KakaoError) as caught:
            self.kakao.access_token()
        self.assertNotIn('SECRET', str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)

    def test_success_checks_delivery_response(self):
        self.requests.post.side_effect = [self.response({'access_token': 'fake'}), self.response({'result_code': 0})]
        self.kakao.send_text('alert', 'https://example.com', 'open')
        self.assertEqual(self.requests.post.call_count, 2)

    def test_targets_and_sent_deduplication(self):
        row = {'ticker': '005930', 'average_price': 100, 'quantity': 1}
        self.assertEqual([x['id'] for x in self.holdings.pending(row, 120, set())],
                         ['005930:reference5', '005930:take10', '005930:take15', '005930:take20'])
        self.assertEqual(self.holdings.pending(row, 120, {'005930:reference5', '005930:take10', '005930:take15', '005930:take20'}), [])
        self.assertEqual(self.holdings.pending(dict(row, enabled=False), 120, set()), [])

    def test_multiday_history_rejects_stale_and_future_bars(self):
        import pandas as pd
        from datetime import datetime, timedelta
        now = datetime(2026, 10, 7, 14, 0, tzinfo=self.holdings.KST)
        ticker = Mock()
        self.holdings.yf.Ticker = Mock(return_value=ticker)
        for age, expected in [(10, 120), (21, None), (24 * 60, None), (-1, None)]:
            ticker.history.return_value = pd.DataFrame({'Close': [120]}, index=[now - timedelta(minutes=age)])
            self.assertEqual(self.holdings.fresh_price({'ticker': '005930'}, now), expected)
        self.assertEqual(ticker.history.call_args.kwargs['period'], '5d')


if __name__ == '__main__':
    unittest.main()
