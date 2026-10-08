"""Recent discoveries must not mutate the stable cohort or send notifications."""
from copy import deepcopy
from datetime import timedelta
from concurrent.futures import Future
import ast
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from rise_new_candidates import recent_discoveries
import test_current_price_analysis as fixtures
from test_current_price_analysis import NOW, ROW, CONTEXT, bars, quote
from rise_live_analysis import evaluate_current


def discovery_data(count=10):
    rows = [dict(ROW, ticker=f'{i:06d}', source='ma_convergence_daily', added_asof='2026-10-05')
            for i in range(count)]
    scan = {'asof': '2026-10-05', 'complete': True, 'pending': None,
            'added_this_run': [x['ticker'] for x in rows]}
    return rows, scan


class DiscoveryModelTests(unittest.TestCase):
    def test_only_latest_new_records_not_all_saved_candidates(self):
        rows, scan = discovery_data()
        rows.append(dict(ROW, ticker='999999'))
        scan['candidates'] = rows
        original = deepcopy((rows, scan))
        result = recent_discoveries(rows, scan, {}, today='2026-10-06')
        self.assertEqual(len(result['rows']), 10)
        self.assertNotIn('999999', [x['ticker'] for x in result['rows']])
        self.assertEqual((rows, scan), original)

    def test_status_distinguishes_active_waiting_archived_unknown(self):
        rows, scan = discovery_data(3)
        cohort = {'active': [{'ticker': '000000'}], 'archived': {'000001': {}}}
        result = recent_discoveries(rows, scan, cohort, today='2026-10-06')
        self.assertEqual([x['discovery_status'] for x in result['rows']],
                         ['현재 관찰 중', '관찰 종료·보관', '새 후보·대기'])
        self.assertEqual(recent_discoveries(rows, scan, today='2026-10-06')['rows'][0]['discovery_status'], '관찰 여부 확인 중')

    def test_pending_unavailable_and_zero_new_are_distinct(self):
        rows, scan = discovery_data(0)
        self.assertEqual(recent_discoveries(rows, {}, today='2026-10-06')['state'], 'unavailable')
        for pending in ({**scan, 'complete': False}, {**scan, 'pending': {'tickers': []}}):
            self.assertEqual(recent_discoveries(rows, pending, today='2026-10-06')['state'], 'pending')
        self.assertEqual(recent_discoveries(rows, scan, today='2026-10-06')['state'], 'ready')

    def test_duplicate_and_deleted_records_do_not_reappear(self):
        rows, scan = discovery_data(3)
        scan['added_this_run'] *= 2
        result = recent_discoveries(rows[1:], scan, today='2026-10-06')
        self.assertEqual([x['ticker'] for x in result['rows']], ['000001', '000002'])

    def test_wrong_source_or_date_not_marked_new(self):
        rows, scan = discovery_data(2)
        rows[0]['added_asof'] = '2026-10-02'
        rows[1]['source'] = 'manual'
        result = recent_discoveries(rows, scan, today='2026-10-06')
        self.assertEqual(result['rows'], [])
        self.assertEqual(result['unverified'], 2)

    def test_future_bad_date_missing_additions_fail_closed(self):
        rows, scan = discovery_data()
        for altered in ({**scan, 'asof': '2026-10-07'}, {**scan, 'asof': 'invalid'}, {**scan, 'added_this_run': None}):
            self.assertEqual(recent_discoveries(rows, altered, today='2026-10-06')['state'], 'unavailable')

    def test_bounded_to_ten(self):
        rows, scan = discovery_data(15)
        self.assertEqual(len(recent_discoveries(rows, scan, today='2026-10-06')['rows']), 10)


class DiscoveryScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.DataAndScreenTests.setUpClass()
        cls.st, cls.ui = fixtures.DataAndScreenTests.st, fixtures.DataAndScreenTests.ui

    @classmethod
    def tearDownClass(cls):
        fixtures.DataAndScreenTests.tearDownClass()

    def setUp(self):
        self.st.session_state.clear()
        self.st.frames.clear()
        self.st.messages.clear()
        self.markdown = patch.object(self.st, 'markdown', self.st.info, create=True)
        self.markdown.start()
        self.addCleanup(self.markdown.stop)
        class Clock:
            @staticmethod
            def now(tz): return NOW
        self.clock = patch.object(self.ui, 'datetime', Clock)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def recent(self):
        rows, scan = discovery_data()
        return recent_discoveries(rows, scan, {}, today=NOW.date().isoformat())

    def test_primary_and_new_candidate_states_are_isolated(self):
        rows = self.recent()['rows']
        primary = self.ui._watch_state(rows)
        primary['sentinel'] = 'preserve'
        candidates = self.ui._watch_state(rows, namespace='rise_discovery_fast_state')
        self.assertIsNot(primary, candidates)
        changed = self.ui._watch_state(rows[:5], namespace='rise_discovery_fast_state')
        self.assertIsNot(candidates, changed)
        self.assertIs(self.ui._watch_state(rows), primary)
        self.assertEqual(primary['sentinel'], 'preserve')

    def test_initial_preparation_nonblocking_without_fake_prices(self):
        recent = self.recent()
        with patch.object(self.ui, '_schedule_watch_selection') as schedule, \
             patch.object(self.ui, '_evaluate_visible') as evaluate:
            self.ui.render_new_discoveries(lambda: recent)
        self.assertEqual(len(schedule.call_args.args[1]), 10)
        self.assertFalse(evaluate.called)
        self.assertFalse(self.st.frames)
        self.assertNotIn('rise_watch_fast_state', self.st.session_state)

    def test_current_price_conditions_refresh_without_membership_change(self):
        recent = self.recent()
        rows = recent['rows']
        state = self.ui._watch_state(rows, namespace='rise_discovery_fast_state')
        state['snapshot'] = {}
        state['histories'] = {row['ticker']: bars() for row in rows}
        with patch.object(self.ui, 'get_market_context', return_value=CONTEXT), \
             patch.object(self.ui, 'get_current_quotes', side_effect=[
                 {row['ticker']: quote(10200) for row in rows},
                 {row['ticker']: quote(9700) for row in rows}]) as quotes, \
             patch.object(self.ui, '_schedule_watch_selection'):
            self.ui.render_new_discoveries(lambda: recent)
            self.ui.render_new_discoveries(lambda: recent)
        frames = [frame for frame, config in self.st.frames if config.get('key') == 'rise_recent_discoveries']
        self.assertEqual(len(frames), 2)
        self.assertEqual(set(frames[0]['코드']), set(frames[1]['코드']))
        self.assertNotEqual(frames[0]['현재가(KIS)'].tolist(), frames[1]['현재가(KIS)'].tolist())
        self.assertNotEqual(frames[0]['필수조건'].tolist(), frames[1]['필수조건'].tolist())
        self.assertEqual(quotes.call_args.kwargs['refresh_seconds'], 10)
        self.assertNotIn('rise_watch_fast_state', self.st.session_state)
        self.assertTrue(any('오늘 발굴 결과가 아닙니다' in message for message in self.st.messages))

    def test_errors_and_no_new_candidates_do_not_query_market(self):
        states = [{'state': 'unavailable'}, {'state': 'pending'},
                  {'state': 'ready', 'rows': [], 'asof': NOW.date().isoformat(), 'unverified': 0}]
        with patch.object(self.ui, '_watch_state') as state:
            for value in states:
                self.ui.render_new_discoveries(lambda: value)
        self.assertFalse(state.called)
        self.assertFalse(self.st.frames)
        self.assertTrue(any('없다는 뜻은 아닙니다' in message for message in self.st.messages))

    def test_entry_has_panel_even_if_cohort_load_fails_and_no_write_callbacks(self):
        source = Path(self.ui.__file__).read_text(encoding='utf-8')
        tree = ast.parse(source)
        node = next(x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name == 'render_new_discoveries')
        code = ast.unparse(node)
        for forbidden in ('send_alerts', 'promote(', '_save_watchlist', 'reconcile_cohort', 'requests.put'):
            self.assertNotIn(forbidden, code)
        self.assertIn('run_every=WATCHLIST_REFRESH_SECONDS', code)
        entry = Path(self.ui.__file__).with_name('rise_timing_watchlist_ui.py').read_text(encoding='utf-8')
        self.assertIn('render_new_discoveries(_recent_discoveries)', entry)


if __name__ == '__main__':
    unittest.main()
