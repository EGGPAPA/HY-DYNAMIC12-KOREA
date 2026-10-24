"""Regression tests for current UI/server parity; all feeds and delivery are fake."""
from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import pandas as pd

from rise_live_analysis import (KST, entry_signal, evaluate_batches, evaluate_current,
                                observe_persistence)
from rise_watch_policy import watch_groups, selection_error, monitor_health, reference_day
from scripts import monitor_rise_watchlist as monitor
from test_current_price_analysis import NOW, ROW, CONTEXT, bars, quote
from tests.test_rise_leaders import fixture


def inputs(count=3):
    saved, daily = fixture(count, day='2026-10-05')
    daily['items']['000001'].update(ma60=15000, ret60_pct=-1, volume_ratio=2)
    daily['items']['000002']['ret20_pct'] = -10
    trend = [saved[2]]
    cohort = {'version': 1, 'policy': 'leader_v1', 'active': trend,
              'archived': {}, 'last_review_asof': daily['asof']}
    return saved, daily, trend, cohort


class PolicyTests(unittest.TestCase):
    def test_union_includes_a_and_b_once_without_changing_membership(self):
        saved, daily, trend, _ = inputs()
        before = deepcopy((saved, daily, trend))
        groups = watch_groups(saved, trend, daily, NOW, '2026-10-05')
        self.assertEqual([r['ticker'] for r in groups['A']], ['000001'])
        self.assertEqual([r['ticker'] for r in groups['B']], ['000002'])
        self.assertEqual({r['ticker'] for r in groups['AB']}, {'000001', '000002'})
        self.assertEqual(groups['B'][0]['watch_status'], '조건 약화')
        self.assertEqual((saved, daily, trend), before)
        overlap = watch_groups(saved, trend + [saved[1]], daily, NOW)
        self.assertEqual(overlap['A'], [])
        self.assertEqual(len(overlap['AB']), 2)

    def test_b_status_distinguishes_weak_from_unverified_without_eviction(self):
        saved, daily, trend, _ = inputs()
        daily['items']['000002']['ret20_pct'] = 20
        self.assertEqual(watch_groups(saved, trend, daily, NOW)['B'][0]['watch_status'], '상승 지속')
        daily['items']['000002']['eligible'] = False
        self.assertEqual(watch_groups(saved, trend, daily, NOW)['B'][0]['watch_status'], '평가 보류')

    def test_stale_unfinished_future_and_wrong_reference_daily_inputs_hold_a(self):
        saved, daily, trend, _ = inputs()
        for changes, now, expected in [({}, NOW + timedelta(days=20), None),
                ({'asof': '2026-10-07'}, NOW, None),
                ({'asof': '2026-10-06'}, NOW, None),
                ({'pending': {'tickers': []}}, NOW, None),
                ({}, NOW, '2026-10-02')]:
            altered = dict(daily, **changes)
            groups = watch_groups(saved, trend, altered, now, expected)
            self.assertTrue(groups['error'])
            self.assertFalse(groups['A'])
            self.assertEqual(len(groups['B']), 1)
            self.assertEqual(groups['B'][0]['watch_status'], '평가 보류')

    def test_confirmed_holiday_reference_not_guessed_from_weekdays(self):
        _, daily, _, _ = inputs()
        self.assertEqual(selection_error(daily, NOW + timedelta(days=6), '2026-10-05'), '')
        self.assertEqual(reference_day(dict(CONTEXT, basis='quote')), '2026-10-05')
        self.assertEqual(reference_day(dict(CONTEXT, basis='close')), '2026-10-06')
        self.assertIsNone(reference_day({'error': 'calendar missing'}))

    def test_health_exposes_missing_delayed_partial_and_failed_delivery(self):
        self.assertEqual(monitor_health({}, NOW, CONTEXT)[0], 'warning')
        state = {'last_success_at': (NOW - timedelta(minutes=30)).isoformat()}
        self.assertEqual(monitor_health(state, NOW, CONTEXT)[0], 'warning')
        state['last_success_at'] = NOW.isoformat()
        self.assertEqual(monitor_health(state, NOW, CONTEXT)[0], 'caption')
        state['notification_error'] = 'failed'
        self.assertEqual(monitor_health(state, NOW, CONTEXT)[0], 'warning')


class EvaluationParityTests(unittest.TestCase):
    def test_prior_volume_spike_cannot_pass_today_volume_in_either_path(self):
        history = bars()
        history.iloc[-1, 1] = 100000
        q = quote(volume=200)
        screen = evaluate_current(ROW, history, q, CONTEXT, NOW)
        server = evaluate_batches([ROW], {ROW['ticker']: history}, CONTEXT,
            lambda batch: {ROW['ticker']: q}, lambda: NOW, {})[0]
        for key in ('price', 'score', 'volume_ratio', 'mandatory_count', 'action', 'alert_eligible'):
            self.assertEqual(screen[key], server[key])
        self.assertAlmostEqual(server['volume_ratio'], 200 / 5950)
        self.assertFalse(server['checks']['volume'])
        self.assertFalse(entry_signal(server))

    def test_old_dates_gap_failure_and_new_day_all_reset_persistence(self):
        def item(seconds=0, volume=2000):
            return evaluate_current(ROW, bars(), quote(seconds=seconds, volume=volume),
                                    CONTEXT, NOW + timedelta(seconds=seconds))
        legacy = {ROW['ticker']: {'consecutive': 0, 'dates': ['2026-09-01', '2026-09-03']}}
        a = item(); observe_persistence([a], legacy)
        self.assertFalse(a['checks']['persistence'])
        b = item(11); observe_persistence([b], legacy)
        self.assertTrue(b['checks']['persistence'])
        c = item(22, 200); observe_persistence([c], legacy)
        self.assertFalse(c['checks']['persistence'])
        d = item(33); observe_persistence([d], legacy)
        self.assertFalse(d['checks']['persistence'])
        observe_persistence([item(44)], legacy)
        gap = item(200); observe_persistence([gap], legacy)
        self.assertFalse(gap['checks']['persistence'])
        legacy[ROW['ticker']]['day'] = '2026-10-05'
        e = item(211); observe_persistence([e], legacy)
        self.assertFalse(e['checks']['persistence'])
        failed = evaluate_current(ROW, bars(), None, CONTEXT, NOW)
        observe_persistence([failed], legacy)
        self.assertNotIn(ROW['ticker'], legacy)

    def test_large_scan_evaluates_receipts_before_later_batches_expire_them(self):
        rows = [dict(ROW, ticker=f'{i:06d}') for i in range(91)]
        histories = {r['ticker']: bars() for r in rows}
        current = [NOW]
        calls = []
        def fetch(batch):
            calls.append(len(batch))
            current[0] += timedelta(seconds=35)
            return {r['ticker']: dict(quote(), received_at=current[0].isoformat()) for r in batch}
        result = evaluate_batches(rows, histories, CONTEXT, fetch, lambda: current[0], {})
        self.assertEqual(calls, [30, 30, 30, 1])
        self.assertTrue(all(r['valid'] for r in result))
        self.assertEqual(len(result), 91)
        self.assertGreater((current[0] - NOW).total_seconds(), 45)

    def test_mandatory_four_alone_and_red_or_closed_never_alert(self):
        x = evaluate_current(ROW, bars(), quote(), CONTEXT, NOW)
        x['checks'].update(stage=False, score=False, persistence=False)
        self.assertFalse(entry_signal(x))
        x['checks']['persistence'] = True
        self.assertTrue(entry_signal(x))
        for change in ({'label': '🔴 급등·추격주의'}, {'intraday': False}, {'valid': False}, {'basis': 'close'}):
            self.assertFalse(entry_signal(dict(x, **change)))


class DeliveryTests(unittest.TestCase):
    def candidate(self, code='000001'):
        x = evaluate_current(dict(ROW, ticker=code), bars(), quote(), CONTEXT, NOW)
        x['checks']['persistence'] = True
        return x

    def test_failed_delivery_is_not_marked_sent_and_retries_only_if_still_qualified(self):
        item = self.candidate()
        sent, error = monitor.deliver_candidates([item], [], Mock(side_effect=RuntimeError()), lambda: NOW)
        self.assertEqual(sent, [])
        self.assertTrue(error)
        send = Mock()
        sent, error = monitor.deliver_candidates([item], sent, send, lambda: NOW)
        self.assertEqual(sent, ['2026-10-06:000001'])
        self.assertFalse(error)
        monitor.deliver_candidates([item], sent, send, lambda: NOW)
        self.assertEqual(send.call_count, 1)
        item['checks']['volume'] = False
        fresh = Mock()
        monitor.deliver_candidates([item], [], fresh, lambda: NOW)
        fresh.assert_not_called()

    def test_partial_delivery_retains_only_confirmed_batches(self):
        items = [self.candidate(f'{i:06d}') for i in range(7)]
        sent, error = monitor.deliver_candidates(items, [], Mock(side_effect=[None, RuntimeError()]), lambda: NOW)
        self.assertEqual(len(sent), 5)
        self.assertTrue(error)
        send = Mock()
        new, error = monitor.deliver_candidates(items, sent, send, lambda: NOW)
        self.assertEqual(len(new), 7)
        self.assertEqual(len(send.call_args.args[0]), 2)

    def test_delayed_quotes_and_after_close_never_send(self):
        send = Mock()
        monitor.deliver_candidates([self.candidate()], [], send, lambda: NOW + timedelta(seconds=46))
        monitor.deliver_candidates([self.candidate()], [], send, lambda: NOW.replace(hour=16))
        send.assert_not_called()

    def test_kakao_http_and_application_success_are_both_required(self):
        env = {'KAKAO_REST_API_KEY': 'fake', 'KAKAO_REFRESH_TOKEN': 'fake'}
        token = SimpleNamespace(ok=True, json=lambda: {'access_token': 'fake'})
        for ok, code in [(False, 0), (True, -1), (True, None)]:
            response = SimpleNamespace(ok=ok, json=lambda: {'result_code': code})
            with patch.dict(monitor.os.environ, env, clear=True), \
                 patch.object(monitor.requests, 'post', side_effect=[token, response]):
                with self.assertRaises(RuntimeError):
                    monitor.send_kakao([self.candidate()])
        with patch.dict(monitor.os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                monitor.send_kakao([self.candidate()])


class MonitorRunTests(unittest.TestCase):
    def test_real_runner_uses_ab_union_shared_evaluator_and_verified_delivery(self):
        saved, daily, trend, cohort = inputs()
        current = [NOW]
        class Clock:
            fromisoformat = datetime.fromisoformat
            @staticmethod
            def now(tz): return current[0]
        feeds = SimpleNamespace(
            get_market_context=lambda: dict(CONTEXT, basis='quote'),
            get_histories=Mock(side_effect=lambda rows: {r['ticker']: bars() for r in rows}),
            get_current_quotes=lambda codes, **kw: {c: dict(quote(), received_at=current[0].isoformat()) for c in codes})
        old = {'history': {'000002': {'consecutive': 0, 'dates': ['2026-09-01', '2026-09-02']}}}
        with patch.dict('sys.modules', {'rise_live_data': feeds}), \
             patch.dict(monitor.os.environ, {'KIS_APP_KEY': 'fake', 'KIS_APP_SECRET': 'fake'}), \
             patch.object(monitor, 'datetime', Clock), \
             patch.object(monitor, 'sleep', side_effect=lambda seconds: current.__setitem__(0, current[0] + timedelta(seconds=seconds))), \
             patch.object(monitor, 'load_state', return_value=(old, 'sha')), \
             patch.object(monitor, 'load_group_inputs', return_value=(saved, trend, daily)), \
             patch.object(monitor, 'save_state') as save, \
             patch.object(monitor, 'send_kakao') as send:
            state = monitor.run()
        self.assertEqual(state['target_count'], 2)
        self.assertEqual(state['group_counts'], {'A': 1, 'B': 1})
        self.assertEqual({r['ticker'] for r in feeds.get_histories.call_args.args[0]}, {'000001', '000002'})
        self.assertEqual(state['status'], 'ok')
        self.assertEqual(len(state['sent']), 2)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(save.call_count, 1)
        self.assertTrue(all(x['checks']['persistence'] for x in state['items']))
        self.assertNotIn('dates', state['history']['000002'])
        self.assertTrue(all('chart' not in x for x in state['items']))

    def test_missing_kis_configuration_is_visible_and_never_fetches_or_notifies(self):
        feeds = SimpleNamespace(get_current_quotes=Mock(), get_histories=Mock(),
                                get_market_context=Mock())
        old = {'sent': ['2026-10-06:000001'], 'last_success_at': NOW.isoformat()}
        with patch.dict('sys.modules', {'rise_live_data': feeds}), \
             patch.dict(monitor.os.environ, {}, clear=True), \
             patch.object(monitor, 'load_state', return_value=(old, 'sha')), \
             patch.object(monitor, 'save_state') as save, \
             patch.object(monitor, 'send_kakao') as send:
            with self.assertRaises(RuntimeError):
                monitor.run(force=True)
        state = save.call_args.args[0]
        self.assertEqual(state['status'], 'error')
        self.assertIn('KIS_APP_KEY', state['error'])
        self.assertIn('KIS_APP_SECRET', state['error'])
        self.assertEqual(state['sent'], old['sent'])
        self.assertEqual(state['last_success_at'], old['last_success_at'])
        self.assertEqual(monitor_health(state, NOW, CONTEXT)[0], 'warning')
        feeds.get_market_context.assert_not_called()
        feeds.get_current_quotes.assert_not_called()
        send.assert_not_called()

    def test_dry_run_has_no_messages_or_remote_writes_even_on_failure(self):
        feeds = SimpleNamespace(get_current_quotes=Mock(), get_histories=Mock(),
                                get_market_context=lambda: {'error': 'auth failure'})
        with patch.dict('sys.modules', {'rise_live_data': feeds}), \
             patch.dict(monitor.os.environ, {'KIS_APP_KEY': 'fake', 'KIS_APP_SECRET': 'fake'}), \
             patch.object(monitor, 'load_state') as load, \
             patch.object(monitor, 'save_state') as save, \
             patch.object(monitor, 'send_kakao') as send:
            with self.assertRaises(RuntimeError):
                monitor.run(dry_run=True, force=True)
        load.assert_not_called(); save.assert_not_called(); send.assert_not_called()


if __name__ == '__main__':
    unittest.main()
