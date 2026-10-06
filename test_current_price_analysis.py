import copy
import importlib
import sys
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from rise_live_analysis import KST, evaluate_current, market_context, observe_persistence, priority_key


NOW = datetime(2026, 10, 6, 11, 0, tzinfo=KST)
ROW = {'ticker': '000001', 'name': '테스트', 'market': 'KOSPI'}
CONTEXT = {'day': '20261006', 'previous': '20261005', 'intraday': True, 'mode': '장중 잠정'}


def bars():
    index = pd.bdate_range(end='2026-10-05', periods=90)
    values = [10000 + i * 2 for i in range(90)]
    return pd.DataFrame({'Close': values, 'Volume': [1000] * 90}, index=index)


def quote(price=10200, volume=2000, seconds=0):
    return {'ok': True, 'price': price, 'volume': volume, 'previous_close': 10178,
            'received_at': (NOW + timedelta(seconds=seconds)).isoformat()}


class EvaluationTests(unittest.TestCase):
    def evaluate(self, history=None, q=None, context=None, now=NOW):
        return evaluate_current(ROW, bars() if history is None else history,
                                quote() if q is None else q, context or CONTEXT, now)

    def test_current_price_drives_chart_ma_gap_risk_breakout_score(self):
        for price in (9700, 10100, 10200, 11000, 14000):
            with self.subTest(price=price):
                item = self.evaluate(q=quote(price))
                self.assertTrue(item['valid'])
                self.assertEqual(item['price'], price)
                self.assertEqual(item['chart'].iloc[-1, 0], price)
                self.assertAlmostEqual(item['ma20'], (bars().Close.tail(19).sum() + price) / 20)
                self.assertAlmostEqual(item['gap20'], (price / item['ma20'] - 1) * 100)
                self.assertAlmostEqual(item['gap'], (price / item['buy1'] - 1) * 100)
                self.assertAlmostEqual(item['risk'], (price - item['stop']) / price * 100)
                self.assertEqual(item['checks']['close'], price >= item['breakout'])
        low, high = self.evaluate(q=quote(9700)), self.evaluate(q=quote(10200))
        self.assertNotEqual(low['score'], high['score'])
        self.assertNotEqual(low['mandatory_count'], high['mandatory_count'])
        self.assertNotEqual(low['span'], high['span'])

    def test_volume_uses_latest_cumulative_not_old_three_day_max(self):
        history = bars()
        history.iloc[-1, 1] = 100000
        item = self.evaluate(history, quote(volume=200))
        self.assertAlmostEqual(item['volume_ratio'], 200 / history.Volume.tail(20).mean())
        self.assertFalse(item['checks']['volume'])
        low = self.evaluate(q=quote(volume=100))
        high = self.evaluate(q=quote(volume=2000))
        self.assertGreater(high['score'], low['score'])
        self.assertGreater(high['mandatory_count'], low['mandatory_count'])

    def test_replace_today_bar_not_append_twice_and_no_future_leak(self):
        original = self.evaluate()
        history = bars()
        history.loc[pd.Timestamp('2026-10-06')] = [999999, 999999]
        history.loc[pd.Timestamp('2026-10-07')] = [9999999, 9999999]
        item = self.evaluate(history)
        pd.testing.assert_frame_equal(item['chart'], original['chart'])
        self.assertEqual(item['score'], original['score'])

    def test_no_source_mutation(self):
        history, q = bars(), quote()
        before, qb = history.copy(), copy.deepcopy(q)
        self.evaluate(history, q)
        pd.testing.assert_frame_equal(history, before)
        self.assertEqual(q, qb)

    def test_failures_never_keep_old_price_or_verdict(self):
        cases = [{'ok': False, 'error': '통신 실패'}, quote(seconds=-46),
                 quote(price=0), quote(price=float('nan')), quote(price=float('inf')),
                 {'ok': True, 'price': 10000}]
        for q in cases:
            with self.subTest(q=q):
                result = self.evaluate(q=q)
                self.assertFalse(result['valid'])
                self.assertIsNone(result['price'])
                self.assertIsNone(result['score'])
                self.assertNotIn('buy1', result)
                self.assertEqual(result['checks'], {})

    def test_history_or_calendar_failure_can_show_quote_but_not_verdict(self):
        short = bars().tail(50)
        stale = bars().iloc[:-1]
        mismatch = bars()
        mismatch.iloc[-1, 0] = 11111
        duplicate = pd.concat([bars(), bars().tail(1)])
        invalid = bars()
        invalid.iloc[-4, 0] = float('nan')
        for history in (short, stale, mismatch, duplicate, invalid, pd.DataFrame()):
            with self.subTest(history=len(history)):
                result = self.evaluate(history=history)
                self.assertFalse(result['valid'])
                self.assertEqual(result['price'], 10200)
                self.assertIsNone(result['score'])
        result = self.evaluate(context={'error': '달력 없음'})
        self.assertFalse(result['valid'])
        self.assertEqual(result['price'], 10200)

    def test_missing_and_zero_volume_and_previous_close_hold(self):
        for key, value in [('volume', None), ('volume', 0), ('volume', -1), ('previous_close', None)]:
            q = quote()
            q[key] = value
            self.assertFalse(self.evaluate(q=q)['valid'])

    def test_weekend_and_holiday_do_not_add_fake_today_bar(self):
        context = dict(CONTEXT, day='20261002', previous='20261001', intraday=False, mode='최근 거래일 참고')
        history = bars().loc[:'2026-10-01']
        q = quote()
        q['previous_close'] = float(history.Close.iloc[-1])
        result = self.evaluate(history, q, context)
        self.assertTrue(result['valid'])
        self.assertEqual(result['chart'].index[-1], pd.Timestamp('2026-10-02'))
        self.assertEqual(len(result['chart']), len(history) + 1)

    def test_conditions_exact_counts_and_ranking_change_with_price(self):
        a = self.evaluate(q=quote(10200))
        b = self.evaluate(q=quote(9700))
        b['ticker'] = '000002'
        self.assertEqual(sorted([b, a], key=priority_key)[0]['ticker'], '000001')
        for item in (a, b):
            self.assertEqual(item['mandatory_count'], sum(item['checks'][k] for k in ('gap', 'volume', 'close', 'risk')))
            self.assertEqual(item['auxiliary_count'], sum(item['checks'][k] for k in ('stage', 'score', 'persistence')))
        a2 = self.evaluate(q=quote(9500))
        b2 = self.evaluate(q=quote(10200))
        b2['ticker'] = '000002'
        self.assertEqual(sorted([a2, b2], key=priority_key)[0]['ticker'], '000002')

    def test_persistence_dedup_failure_reset_and_no_closed_market_accumulation(self):
        states = {}
        first = self.evaluate()
        observe_persistence([first], states)
        observe_persistence([self.evaluate()], states)
        self.assertEqual(states['000001']['count'], 1)
        next_row = self.evaluate(q=quote(seconds=10), now=NOW + timedelta(seconds=10))
        observe_persistence([next_row], states)
        self.assertTrue(next_row['checks']['persistence'])
        fail = self.evaluate(q={'ok': False})
        observe_persistence([fail], states)
        self.assertNotIn('000001', states)
        closed = self.evaluate(context=dict(CONTEXT, intraday=False, mode='최근 거래일 참고'))
        observe_persistence([closed], states)
        self.assertFalse(closed['checks']['persistence'])


class CalendarTests(unittest.TestCase):
    def setUp(self):
        self.calendar = [{'bass_dt': f'202610{d:02}', 'opnd_yn': 'Y' if d in (1, 2, 6) else 'N'} for d in range(1, 7)]

    def test_long_weekend_and_opening(self):
        before = market_context(self.calendar, NOW.replace(hour=8))
        self.assertEqual((before['day'], before['previous']), ('20261002', '20261001'))
        opened = market_context(self.calendar, NOW)
        self.assertEqual((opened['day'], opened['previous']), ('20261006', '20261002'))
        self.assertTrue(opened['intraday'])
        self.assertFalse(market_context(self.calendar, NOW.replace(hour=16))['intraday'])

    def test_missing_today_fails_closed(self):
        self.assertIn('error', market_context(self.calendar[:-1], NOW))


class FakeStreamlit(types.ModuleType):
    def __init__(self):
        super().__init__('streamlit')
        self.session_state = {}
        self.frames = []
        self.messages = []
        self.details = []
    def cache_data(self, *args, **kwargs):
        return lambda fn: fn
    cache_resource = cache_data
    fragment = cache_data
    def empty(self):
        return self
    def info(self, text):
        self.messages.append(text)
    caption = info
    warning = info
    def expander(self, *args, **kwargs):
        return self
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def dataframe(self, frame, **kwargs):
        self.frames.append((frame, kwargs))
    @property
    def column_config(self):
        return types.SimpleNamespace(NumberColumn=lambda **kwargs: kwargs)


class DataAndScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.st = FakeStreamlit()
        cls.pricing = types.ModuleType('korea_live_price')
        cls.pricing.KIS_BASE_URL = 'https://openapi.koreainvestment.com:9443'
        cls.pricing._secret = lambda key: 'test-only'
        cls.pricing.get_kis_access_token = lambda: 'test-only'
        cls.requests = types.ModuleType('requests')
        cls.requests.RequestException = RuntimeError
        cls.requests.get = lambda *a, **kw: None
        cls.modules = patch.dict(sys.modules, {'streamlit': cls.st, 'korea_live_price': cls.pricing, 'requests': cls.requests})
        cls.modules.start()
        cls.data = importlib.import_module('rise_live_data')
        cls.ui = importlib.import_module('rise_current_price_ui')

    @classmethod
    def tearDownClass(cls):
        cls.modules.stop()
        sys.modules.pop('rise_live_data', None)
        sys.modules.pop('rise_current_price_ui', None)

    def setUp(self):
        self.st.session_state.clear()
        self.st.frames.clear()
        self.st.messages.clear()

    def test_batch_quotes_join_by_code_not_response_order(self):
        raw = [{'inter_shrn_iscd': code, 'inter2_prpr': price, 'acml_vol': '2000', 'inter2_prdy_clpr': '10000'}
               for code, price in [('000002', '12000'), ('000001', '11000'), ('000999', '20000')]]
        parsed = self.data.parse_quotes(raw, ['000001', '000002'], NOW.isoformat())
        self.assertEqual(parsed['000001']['price'], 11000)
        self.assertEqual(parsed['000002']['price'], 12000)
        self.assertNotIn('000999', parsed)

    def test_batch_max_thirty_and_all_178_queried(self):
        calls = []
        def chunk(codes, slot):
            calls.append(codes)
            return {c: quote() for c in codes}
        codes = [str(i).zfill(6) for i in range(178)]
        with patch.object(self.data, '_quote_chunk', chunk):
            result = self.data.get_current_quotes(codes)
        self.assertEqual(len(result), 178)
        self.assertEqual(len(calls), 6)
        self.assertLessEqual(max(map(len, calls)), 30)

    def test_http_success_missing_item_is_failure_not_old_price(self):
        with patch.object(self.data, '_kis_get', return_value=({'output': []}, '')):
            result = self.data._quote_chunk(('000001',), 1)
        self.assertFalse(result['000001']['ok'])
        self.assertNotIn('price', result['000001'])

    def test_calendar_denied_uses_only_confirmed_today_index_dates(self):
        class Clock:
            @staticmethod
            def now(tz):
                return NOW
        with patch.object(self.data, 'datetime', Clock), \
             patch.object(self.data, '_calendar', return_value=([], 'calendar denied')), \
             patch.object(self.data, '_index_trading_dates', return_value=(['20261001', '20261002', '20261006'], '')):
            result = self.data.get_market_context()
        self.assertEqual(result['day'], '20261006')
        self.assertEqual(result['previous'], '20261002')
        self.assertIn('notice', result)
        with patch.object(self.data, 'datetime', Clock), \
             patch.object(self.data, '_calendar', return_value=([], 'calendar denied')), \
             patch.object(self.data, '_index_trading_dates', return_value=(['20261001', '20261002'], '')):
            stale = self.data.get_market_context()
        self.assertIn('error', stale)

    def test_index_context_rejects_zero_volume_future_and_invalid_dates(self):
        data = {'output2': [{'stck_bsop_date': date, 'acml_vol': vol} for date, vol in
                           [('20261001', '10'), ('20261002', '10'), ('20261006', '0'), ('20261007', '99'), ('20260999', '88')]]}
        with patch.object(self.data, '_kis_get', return_value=(data, '')):
            dates, error = self.data._index_trading_dates('20261006', 0)
        self.assertEqual(dates, ['20261001', '20261002'])

    def test_quote_partial_bad_payload(self):
        for price in ('NaN', 'Infinity', '0', None):
            parsed = self.data.parse_quotes([{'inter_shrn_iscd': '000001', 'inter2_prpr': price}], ['000001'], NOW.isoformat())
            self.assertFalse(parsed['000001']['ok'])

    def test_daily_uses_adjusted_krx_and_dates(self):
        payload = {'output2': [{'stck_bsop_date': '20261005', 'stck_clpr': '10178', 'acml_vol': '1000'}]}
        with patch.object(self.data, '_kis_get', return_value=(payload, '')) as call:
            frame = self.data.get_daily_history('000001', '20261006')
        params = call.call_args.args[2]
        self.assertEqual(params['FID_ORG_ADJ_PRC'], '0')
        self.assertEqual(params['FID_COND_MRKT_DIV_CODE'], 'J')
        self.assertEqual(frame.index[-1], pd.Timestamp('2026-10-05'))

    def test_full_watchlist_evaluated_before_cap_and_detail_uses_same_snapshot(self):
        results = []
        for i in range(25):
            item = evaluate_current(dict(ROW, ticker=str(i).zfill(6)), bars(), quote(10200), CONTEXT, NOW)
            results.append(item)
        details = []
        with patch.object(self.ui, '_evaluate', return_value=(results, CONTEXT)) as evaluate, \
             patch.object(self.ui, '_detail', lambda selected: details.extend(selected)):
            self.ui._render_live_watchlist([dict(ROW, ticker=str(i).zfill(6)) for i in range(25)])
        self.assertEqual(len(evaluate.call_args.args[0]), 25)
        frame, config = self.st.frames[0]
        self.assertEqual(len(frame), 20)
        self.assertEqual(config['key'], 'rise_live_watchlist')
        self.assertEqual(len(details), 20)
        self.assertIs(details[0], results[0])

    def test_table_uses_live_price_and_holds_hide_score_and_targets(self):
        good = evaluate_current(ROW, bars(), quote(10200), CONTEXT, NOW)
        failed = evaluate_current(ROW, bars(), {'ok': False}, CONTEXT, NOW)
        frame = self.ui._frame([good, failed])
        self.assertEqual(frame.iloc[0]['현재가(KIS)'], '10,200원')
        self.assertEqual(frame.iloc[0]['1차가 거리'], f"{good['gap']:+.1f}%")
        self.assertEqual(frame.iloc[1]['현재가(KIS)'], '—')
        self.assertEqual(frame.iloc[1]['1차 매수 참고'], '—')
        self.assertTrue(pd.isna(frame.iloc[1]['시점점수']))

    def test_active_entry_routes_to_new_screen(self):
        import ast
        source = Path(__file__).with_name('rise_timing_watchlist_ui.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        entry = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'render_rise_timing_watchlist')
        body = ast.get_source_segment(source, entry)
        self.assertIn('render_current_price_screen(universe, rows', body)
        self.assertNotIn('_select_watchlist_results', body)
        self.assertNotIn('_scan_all_market', body)
        self.assertIn('관찰종목 추가·삭제', body)

    def test_active_evaluator_requotes_every_call_without_old_server_verdicts(self):
        class Clock:
            @staticmethod
            def now(tz):
                return NOW
        with patch.object(self.ui, 'datetime', Clock), \
             patch.object(self.ui, 'get_histories', return_value={'000001': bars()}) as history, \
             patch.object(self.ui, 'get_market_context', return_value=CONTEXT), \
             patch.object(self.ui, 'get_current_quotes', side_effect=[{'000001': quote(10200)}, {'000001': quote(9700)}]) as quotes:
            first, _ = self.ui._evaluate([ROW], 'test')
            second, _ = self.ui._evaluate([ROW], 'test')
        self.assertEqual(history.call_count, 1)
        self.assertEqual(quotes.call_count, 2)
        self.assertEqual(first[0]['price'], 10200)
        self.assertEqual(second[0]['price'], 9700)
        self.assertNotEqual(first[0]['score'], second[0]['score'])
        self.assertNotEqual(first[0]['mandatory_count'], second[0]['mandatory_count'])

    def test_live_screens_do_not_reuse_daily_scan_or_server_actions(self):
        source = Path(__file__).with_name('rise_current_price_ui.py').read_text(encoding='utf-8')
        for forbidden in ('_load_background_state', 'rise_all_scan', 'get_live_price', 'price_source_label'):
            self.assertNotIn(forbidden, source)
        self.assertIn("@st.fragment(run_every='10s')", source)
        self.assertIn("@st.fragment(run_every='30s')", source)
        self.assertIn('KOSPI·KOSDAQ 전종목 분석이 아닙니다', source)


if __name__ == '__main__':
    unittest.main()
