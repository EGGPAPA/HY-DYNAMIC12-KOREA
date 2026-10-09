"""Holiday reference evaluation: no orders, credentials, real network or remote writes."""
from copy import deepcopy
from datetime import datetime, timedelta
import types
import unittest
from unittest.mock import Mock, patch

import pandas as pd
import test_current_price_analysis as fixtures
from rise_live_analysis import (KST, market_context, closed_context, index_market_context,
                                 evaluate_current, observe_persistence)

NOW = datetime(2026, 10, 9, 9, 30, tzinfo=KST)
ROW = fixtures.ROW
CLOSE = {'day': '20261008', 'previous': '20261007', 'intraday': False,
         'basis': 'close', 'mode': '휴장일 · 종가 기준'}


def history():
    frame = pd.DataFrame({'Close': [10000 + i * 2 for i in range(90)],
                          'Volume': [1000] * 89 + [1800]},
                         index=pd.bdate_range(end='2026-10-08', periods=90))
    frame.attrs['received_at'] = NOW.isoformat()
    return frame


def result(hist=None, context=None, q=None, now=NOW):
    return evaluate_current(ROW, history() if hist is None else hist, q, context or CLOSE, now)


class HolidayModelTests(unittest.TestCase):
    def test_calendar_confirmed_holiday_and_weekend_use_last_open_day(self):
        cal = [{'bass_dt': f'202610{x:02}', 'opnd_yn': 'Y' if x in (7, 8) else 'N'} for x in range(7, 12)]
        for day in (9, 10, 11):
            ctx = market_context(cal, NOW.replace(day=day))
            self.assertEqual((ctx['day'], ctx['previous'], ctx['basis']), ('20261008','20261007','close'))
            self.assertIn('휴장일', ctx['mode'])

    def test_before_open_and_after_close_are_references_but_settlement_window_is_not(self):
        cal = [{'bass_dt': f'202610{x:02}', 'opnd_yn': 'Y'} for x in (7, 8, 9)]
        self.assertEqual(market_context(cal, NOW.replace(hour=8))['day'], '20261008')
        self.assertEqual(market_context(cal, NOW.replace(hour=16))['basis'], 'close')
        self.assertEqual(market_context(cal, NOW.replace(hour=15, minute=35))['basis'], 'quote')
        self.assertEqual(market_context(cal, NOW)['basis'], 'quote')

    def test_missing_today_index_is_not_claimed_as_verified_holiday_or_live(self):
        ctx = index_market_context(['20261007','20261008'], NOW)
        self.assertEqual(ctx['basis'], 'close')
        self.assertFalse(ctx['intraday'])
        self.assertNotIn('휴장일', ctx['mode'])
        self.assertIn('오늘의 휴장 여부를 추정', ctx['notice'])

    def test_index_live_date_switches_back_to_quote_mode(self):
        ctx = index_market_context(['20261007','20261008','20261009'], NOW)
        self.assertEqual((ctx['day'],ctx['previous'],ctx['basis']), ('20261009','20261008','quote'))
        self.assertTrue(ctx['intraday'])

    def test_bad_old_duplicate_future_or_insufficient_dates_do_not_fabricate_session(self):
        for dates in ([], ['20261008'], ['20261008','20261008'], ['20260901','20260902'],
                      ['invalid','20261008'], ['20261009','20261010']):
            self.assertIn('error', closed_context(dates, NOW, 'reference'))

    def test_close_evaluates_without_quote_and_ignores_holiday_quote(self):
        item = result()
        self.assertTrue(item['valid'])
        self.assertEqual(item['price'], history().Close.iloc[-1])
        self.assertIsNone(item['quote_received_at'])
        for q in ({'ok': False}, {'ok': True,'price': 999999,'volume': 0}, fixtures.quote(seconds=-100000)):
            other = result(q=q)
            self.assertEqual(other['score'], item['score'])
            self.assertEqual(other['price'], item['price'])

    def test_all_fields_use_same_closed_price_and_full_session_volume(self):
        item = result()
        past = history().iloc[:-1]
        self.assertAlmostEqual(item['ma20'], history().Close.tail(20).mean())
        self.assertAlmostEqual(item['volume_ratio'], 1800 / past.Volume.tail(20).mean())
        self.assertAlmostEqual(item['gap'], (item['price']/item['buy1']-1)*100)
        self.assertAlmostEqual(item['risk'], (item['price']-item['stop'])/item['price']*100)
        self.assertEqual(item['checks']['close'], item['price'] >= item['breakout'])
        live_ctx = dict(CLOSE, basis='quote', intraday=True, mode='장중 잠정')
        live_now = NOW.replace(day=8)
        q = {'ok': True,'price': float(history().Close.iloc[-1]), 'volume': 1800,
             'previous_close': float(past.Close.iloc[-1]), 'received_at': live_now.isoformat()}
        live = result(context=live_ctx, q=q, now=live_now)
        for key in ('score','mandatory_count','auxiliary_count','buy1','buy2','stop','ma20','volume_ratio'):
            self.assertEqual(item[key], live[key], key)

    def test_no_fake_holiday_bar_duplicate_close_or_future_leak(self):
        h = history()
        original = result(h)
        h.loc[pd.Timestamp('2026-10-09')] = [999999,999999]
        h.loc[pd.Timestamp('2026-10-10')] = [9999999,9999999]
        item = result(h)
        self.assertEqual(item['chart'].index[-1], pd.Timestamp('2026-10-08'))
        self.assertEqual(len(item['chart']), 90)
        self.assertEqual(item['chart'].index.nunique(), 90)
        pd.testing.assert_frame_equal(item['chart'], original['chart'])

    def test_missing_reference_or_previous_bar_remains_on_hold(self):
        for h in (history().iloc[:-1], history().drop(pd.Timestamp('2026-10-07')), history().tail(50), pd.DataFrame()):
            item = result(h)
            self.assertFalse(item['valid'])
            self.assertIsNone(item['score'])
            self.assertNotIn('buy1', item)

    def test_bad_closed_values_and_duplicate_dates_are_not_replaced_with_quote(self):
        for col, val in (('Close',0),('Close',float('nan')),('Volume',0),('Volume',-1),('Volume',float('inf'))):
            h = history().astype(float); h.iloc[-1, h.columns.get_loc(col)] = val
            self.assertFalse(result(h, q=fixtures.quote())['valid'])
        self.assertFalse(result(pd.concat([history(), history().tail(1)]))['valid'])

    def test_unsettled_today_future_old_and_intraday_close_contexts_fail_closed(self):
        for ctx in (dict(CLOSE, day='20261009'), dict(CLOSE,day='20261010'),
                    dict(CLOSE, day='20260901'), dict(CLOSE,intraday=True)):
            self.assertFalse(result(context=ctx)['valid'])

    def test_no_mutation_or_fabricated_quote_receipt(self):
        h, ctx = history(), deepcopy(CLOSE)
        before = h.copy(deep=True)
        item = result(h,ctx)
        pd.testing.assert_frame_equal(h,before)
        self.assertEqual(ctx,CLOSE)
        self.assertIsNone(item['quote_received_at'])
        self.assertEqual(item['history_received_at'], h.attrs['received_at'])

    def test_repeated_holiday_refresh_does_not_create_persistence(self):
        states = {ROW['ticker']: {'count': 99}}
        item = result()
        observe_persistence([item],states)
        observe_persistence([item],states)
        self.assertFalse(item['checks']['persistence'])
        self.assertNotIn(ROW['ticker'],states)

    def test_live_failure_does_not_silently_become_historical_evaluation(self):
        ctx = dict(CLOSE, day='20261009', previous='20261008', basis='quote', intraday=True)
        self.assertFalse(result(context=ctx,q={'ok':False})['valid'])


class HolidayScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.DataAndScreenTests.setUpClass()
        cls.st, cls.ui, cls.data = fixtures.DataAndScreenTests.st, fixtures.DataAndScreenTests.ui, fixtures.DataAndScreenTests.data

    @classmethod
    def tearDownClass(cls):
        fixtures.DataAndScreenTests.tearDownClass()

    def setUp(self):
        self.st.session_state.clear(); self.st.frames.clear(); self.st.messages.clear()
        class Clock:
            @staticmethod
            def now(tz): return NOW
        self.clock = patch.object(self.ui, 'datetime', Clock)
        self.clock.start(); self.addCleanup(self.clock.stop)

    def test_calendar_failure_with_prior_index_bars_returns_reference(self):
        class Clock:
            @staticmethod
            def now(tz): return NOW
        with patch.object(self.data,'datetime',Clock), patch.object(self.data,'_calendar',return_value=([],'HTTP 500')), \
             patch.object(self.data,'_index_trading_dates',return_value=(['20261007','20261008'],'')):
            ctx = self.data.get_market_context()
        self.assertEqual(ctx['basis'],'close')
        self.assertNotIn('error',ctx)

    def test_calendar_and_index_failure_stays_on_hold(self):
        with patch.object(self.data,'_calendar',return_value=([],'HTTP 500')), \
             patch.object(self.data,'_index_trading_dates',return_value=([],'HTTP 500')):
            self.assertIn('error', self.data.get_market_context())

    def test_primary_closed_table_uses_no_quotes_then_live_restarts_ten_second_quotes(self):
        rows = [ROW]
        state = {'revision': 0, 'histories': {ROW['ticker']: history()},'observations': {},'observed_at': {}}
        live = dict(CLOSE, day='20261009',previous='20261008',basis='quote',intraday=True,mode='장중 잠정')
        q = {'ok':True,'price':10300,'volume':2500,'previous_close':float(history().Close.iloc[-1]),'received_at':NOW.isoformat()}
        with patch.object(self.ui,'get_market_context',side_effect=[CLOSE,live]), \
             patch.object(self.ui,'get_current_quotes',return_value={ROW['ticker']:q}) as quotes:
            first, _ = self.ui._evaluate_visible(rows,state)
            second, _ = self.ui._evaluate_visible(rows,state)
        self.assertTrue(first[0]['valid']); self.assertTrue(second[0]['valid'])
        self.assertEqual(first[0]['basis'],'close'); self.assertEqual(second[0]['basis'],'quote')
        self.assertEqual(quotes.call_count,1)
        self.assertEqual(quotes.call_args.kwargs['refresh_seconds'],10)
        self.assertEqual(first[0]['ticker'],second[0]['ticker'])
        self.assertNotEqual(first[0]['price'],second[0]['price'])

    def test_closed_upper_scan_never_queries_quotes(self):
        with patch.object(self.ui,'get_histories',return_value={ROW['ticker']:history()}), \
             patch.object(self.ui,'get_market_context',return_value=CLOSE), \
             patch.object(self.ui,'get_current_quotes') as quotes:
            items, _ = self.ui._evaluate([ROW],'holiday_test')
        self.assertTrue(items[0]['valid'])
        self.assertFalse(quotes.called)

    def test_closed_headers_and_status_are_not_labeled_live(self):
        item = result()
        frame = self.ui._frame([item],compact=True)
        self.assertEqual(len(frame.columns),17)
        self.assertIn('기준가(KIS 종가)',frame.columns)
        self.assertIn('마감거래량/20일평균',frame.columns)
        self.assertNotIn('현재가(KIS)',frame.columns)
        self.ui._status([item],CLOSE,10)
        self.assertTrue(any('2026-10-08' in s and '실시간 시세가 아닙니다' in s for s in self.st.messages))
        self.assertFalse(any('실제 시세 수신시각' in s or '수신 성공 없음' in s for s in self.st.messages))

    def test_reference_search_does_not_send_alerts_or_promote_even_if_all_conditions_pass(self):
        item = result(); item.update(mandatory_count=4,average_value=1e10,label='🟢 상승초입')
        self.st.columns = lambda n: [types.SimpleNamespace(metric=lambda *a: None) for _ in range(n)]
        self.st.session_state['rise_current_notify_once'] = True
        send, promote = Mock(), Mock()
        with patch.object(self.ui,'_evaluate',return_value=([item],CLOSE)),patch.object(self.ui,'_status'):
            self.ui._render_live_scan([ROW],send,promote)
        self.assertFalse(send.called); self.assertFalse(promote.called)
        self.assertNotIn('rise_current_notify_once',self.st.session_state)

    def test_new_candidate_panel_supports_closed_price_column_without_membership_changes(self):
        recent = {'state':'ready','asof':'2026-10-08','unverified':0,
                  'rows':[{**ROW,'discovery_status':'새 후보·대기'}]}
        state = self.ui._watch_state(recent['rows'],namespace='rise_discovery_fast_state')
        state.update(snapshot={},histories={ROW['ticker']:history()})
        with patch.object(self.st,'markdown',self.st.info,create=True), \
             patch.object(self.ui,'get_market_context',return_value=CLOSE), \
             patch.object(self.ui,'get_current_quotes') as quotes,patch.object(self.ui,'_schedule_watch_selection'):
            self.ui.render_new_discoveries(lambda:recent)
        frame = self.st.frames[0][0]
        self.assertEqual(len(frame.columns),9)
        self.assertIn('기준가(KIS 종가)',frame.columns)
        self.assertFalse(quotes.called)
        self.assertNotIn('rise_watch_fast_state',self.st.session_state)


if __name__ == '__main__':
    unittest.main()


