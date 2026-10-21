import ast
from datetime import datetime
import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pandas as pd

from market_update_data import bounded_call, fetch_market_universe, require_full_universe
from krx_kis_pipeline import collect_krx_ohlcv


def function(name, namespace):
    source = ast.parse(Path('app.py').read_text(encoding='utf-8'))
    node = next(n for n in source.body if isinstance(n, ast.FunctionDef) and n.name == name)
    node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'app.py', 'exec'), namespace)
    return namespace[name]


def universe(size=2000):
    return pd.DataFrame({'종목코드': [f'{i:06d}' for i in range(size)],
                         '시장': ['KOSPI' if i < size // 2 else 'KOSDAQ' for i in range(size)]})


class MarketDataTests(unittest.TestCase):
    def test_fallback_thirty_two_names_cannot_replace_full_analysis(self):
        with self.assertRaisesRegex(RuntimeError, '비상 후보군'):
            require_full_universe(universe(32))
        require_full_universe(universe())
        with self.assertRaises(RuntimeError):
            require_full_universe(universe().assign(시장='KOSPI'))

    def test_provider_timeout_reuses_worker_and_recovers(self):
        release, started = Event(), Event()
        count = []
        def blocked():
            count.append(1)
            started.set()
            release.wait(2)
            return 'ok'
        try:
            with self.assertRaises(TimeoutError):
                bounded_call('test-blocked', blocked, seconds=.01)
            self.assertTrue(started.wait(.2))
            with self.assertRaises(TimeoutError):
                bounded_call('test-blocked', blocked, seconds=.01)
            self.assertEqual(len(count), 1)
        finally:
            release.set()
        self.assertEqual(bounded_call('test-new', lambda: 'recovered', seconds=1), 'recovered')

    def test_empty_krx_stops_after_three_weekdays_and_never_returns_partial_history(self):
        provider = MagicMock()
        provider.get_market_ohlcv_by_ticker.return_value = pd.DataFrame()
        history, day = collect_krx_ohlcv(provider, '20261021', sessions=22,
                                        max_seconds=30, max_empty_weekdays=3)
        self.assertTrue(history.empty)
        self.assertIsNone(day)
        self.assertEqual(provider.get_market_ohlcv_by_ticker.call_count, 9)

    def test_krx_deadline_does_not_wait_for_all_fifty_dates(self):
        with patch('krx_kis_pipeline.monotonic', side_effect=[0, 31]):
            provider = MagicMock()
            history, day = collect_krx_ohlcv(provider, '20261021', max_seconds=30)
            self.assertTrue(history.empty)
            provider.get_market_ohlcv_by_ticker.assert_not_called()

    @patch('market_update_data.urlopen')
    def test_complete_two_market_pagination_and_no_partial_result(self, get):
        def page(request, **kwargs):
            market = 'KOSPI' if '/KOSPI?' in request.full_url else 'KOSDAQ'
            number = int(request.full_url.split('page=')[1].split('&')[0])
            offset = (0 if market == 'KOSPI' else 2000) + (number - 1) * 100
            body = {'totalCount': 1000, 'stocks': [dict(itemCode=f'{offset+i:06d}',
                    stockEndType='stock', stockName=f'Stock {offset+i}') for i in range(100)]}
            response = MagicMock()
            response.__enter__.return_value.read.return_value = json.dumps(body).encode()
            return response
        get.side_effect = page
        progress = MagicMock()
        result = fetch_market_universe(progress)
        self.assertEqual(len(result), 2000)
        self.assertEqual({r['시장'] for r in result}, {'KOSPI', 'KOSDAQ'})
        self.assertEqual(progress.call_count, 20)
        get.side_effect = OSError('offline')
        with self.assertRaises(OSError):
            fetch_market_universe()


class UpdateFlowTests(unittest.TestCase):
    def namespace(self, names=2000):
        state = {'kr_rows': ['previous'], 'screen_source': 'test',
                 'candidate_count': 120, 'kis_candidate_count': 0}
        ns = {'st': SimpleNamespace(session_state=state), 'datetime': datetime,
              'SEOUL': ZoneInfo('Asia/Seoul'), 'FINAL_TOP_N': 12,
              'require_full_universe': require_full_universe}
        for name in ('get_auto_flow', 'download_chunk', 'get_single_history', 'yf_history',
                     '_monthly_batch', '_auto_price', 'get_krx_batch_history',
                     'get_candidate_kis_prices', 'fundamental_score', 'remember_daily_analysis',
                     'refresh_wealth_jump_status'):
            ns[name] = MagicMock()
        ns['get_full_universe'] = MagicMock(return_value=(universe(names), 'test'))
        ns['run_market_analysis'] = MagicMock(return_value=([{'ticker': '000001'}], '중립장', 75, 10))
        ns['scan_monthly_ma5'] = MagicMock(return_value=[])
        ns['_load_pension'] = MagicMock(return_value=({}, None))
        return ns

    def test_full_update_clears_stale_inputs_and_persists_only_final_metadata(self):
        ns = self.namespace()
        run = function('run_full_update', ns)
        stamp, regime = run(MagicMock())
        for name in ('get_full_universe', 'get_krx_batch_history', 'get_auto_flow',
                     'get_candidate_kis_prices', 'fundamental_score'):
            ns[name].clear.assert_called_once()
        self.assertEqual(regime, '중립장')
        self.assertFalse(ns['run_market_analysis'].call_args.kwargs['persist'])
        self.assertEqual(ns['st'].session_state['full_update_at'], stamp)
        self.assertEqual(ns['st'].session_state['full_update_mode'], '정밀 전체 업데이트')
        ns['remember_daily_analysis'].assert_called_once()

    def test_incomplete_universe_keeps_existing_results_and_timestamp(self):
        ns = self.namespace(32)
        ns['st'].session_state['full_update_at'] = 'old'
        run = function('run_full_update', ns)
        with self.assertRaisesRegex(RuntimeError, '기존 분석은 유지'):
            run(MagicMock())
        ns['run_market_analysis'].assert_not_called()
        ns['remember_daily_analysis'].assert_not_called()
        self.assertEqual(ns['st'].session_state['kr_rows'], ['previous'])
        self.assertEqual(ns['st'].session_state['full_update_at'], 'old')

    def test_failed_later_stage_does_not_claim_full_completion(self):
        ns = self.namespace()
        ns['st'].session_state['full_update_at'] = 'old'
        ns['scan_monthly_ma5'].side_effect = RuntimeError('monthly offline')
        run = function('run_full_update', ns)
        with self.assertRaises(RuntimeError):
            run(MagicMock())
        self.assertEqual(ns['st'].session_state['full_update_at'], 'old')
        ns['remember_daily_analysis'].assert_not_called()

    def test_navigation_is_rendered_disabled_before_update_then_reenabled(self):
        source = Path('app.py').read_text(encoding='utf-8')
        block = source[source.index('quick_col, full_col = st.columns'):source.index('if selected_view == "🏠 시작":')]
        class Rerun(BaseException):
            pass
        for pressed in (True, False):
            with self.subTest(pressed=pressed):
                state = {}
                st = MagicMock()
                st.session_state = state
                quick, full = MagicMock(), MagicMock()
                quick.button.return_value = False
                full.button.return_value = pressed
                st.columns.return_value = (quick, full)
                st.rerun.side_effect = Rerun()
                def run(status):
                    self.assertTrue(st.radio.call_args.kwargs['disabled'])
                    return 'completed', '중립장'
                ns = dict(st=st, run_full_update=MagicMock(side_effect=run),
                          run_fast_update=MagicMock(), show_update_result=MagicMock())
                if pressed:
                    with self.assertRaises(Rerun):
                        exec(block, ns)
                    self.assertEqual(state['hy_update_outcome']['completed_at'], 'completed')
                else:
                    exec(block, ns)
                    self.assertFalse(st.radio.call_args.kwargs['disabled'])
                    ns['run_full_update'].assert_not_called()


if __name__ == '__main__':
    unittest.main()
