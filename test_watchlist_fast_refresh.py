"""Isolated live-20 refresh tests. No credentials, network, or saved-data writes."""
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import timedelta
from threading import Event
import unittest
from unittest.mock import Mock, patch

import pandas as pd

import test_current_price_analysis as fixtures
from test_current_price_analysis import NOW, ROW, CONTEXT, bars, quote
from rise_live_analysis import evaluate_current


class Clock:
    value = NOW

    @classmethod
    def now(cls, tz):
        return cls.value


class FastRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.DataAndScreenTests.setUpClass()
        cls.st, cls.ui, cls.data = fixtures.DataAndScreenTests.st, fixtures.DataAndScreenTests.ui, fixtures.DataAndScreenTests.data

    @classmethod
    def tearDownClass(cls):
        fixtures.DataAndScreenTests.tearDownClass()

    def setUp(self):
        self.st.session_state.clear()
        self.st.frames.clear()
        self.st.messages.clear()
        Clock.value = NOW
        self.clock = patch.object(self.ui, 'datetime', Clock)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def snapshot(self, count=25):
        rows = [dict(ROW, ticker=f'{i:06d}') for i in range(count)]
        histories = {row['ticker']: bars() for row in rows}
        results = [evaluate_current(row, histories[row['ticker']], quote(), CONTEXT, NOW) for row in rows]
        return rows, {'results': results, 'histories': histories, 'history_hour': NOW.strftime('%Y%m%d%H'),
                      'context': CONTEXT, 'observations': {}, 'completed_at': NOW.isoformat()}

    def ready_state(self, rows, snapshot):
        state = self.ui._watch_state(rows)
        future = Future()
        future.set_result(snapshot)
        state['future'] = future
        return self.ui._watch_state(rows)

    def test_initial_selection_is_async_and_does_not_show_unverified_prices(self):
        rows, snapshot = self.snapshot()
        future = Future()
        pool = Mock()
        pool.submit.return_value = future
        with patch.object(self.ui, '_watchlist_pool', return_value=pool), \
             patch.object(self.ui, '_evaluate') as full, \
             patch.object(self.ui, '_evaluate_visible') as visible:
            self.ui._render_live_watchlist(rows)
            self.ui._render_live_watchlist(rows)
        self.assertEqual(pool.submit.call_count, 1)
        self.assertEqual(len(pool.submit.call_args.args[1]), 20)
        self.assertFalse(full.called)
        self.assertFalse(visible.called)
        self.assertFalse(self.st.frames)

    def test_pending_background_worker_does_not_block_live_twenty(self):
        rows, snapshot = self.snapshot(20)
        state = self.ready_state(rows, snapshot)
        gate = Event()
        with ThreadPoolExecutor(max_workers=1) as pool:
            state['future'] = pool.submit(lambda: gate.wait(3))
            try:
                with patch.object(self.ui, '_evaluate_visible', return_value=(snapshot['results'][:20], CONTEXT)) as fast, \
                     patch.object(self.ui, '_detail'), patch.object(self.ui, '_evaluate') as full:
                    self.ui._render_live_watchlist(rows)
                self.assertFalse(state['future'].done())
                self.assertEqual(len(fast.call_args.args[0]), 20)
                self.assertFalse(full.called)
                self.assertEqual(len(self.st.frames[0][0]), 20)
            finally:
                gate.set()

    def test_visible_price_and_all_evaluation_fields_recompute_without_history_fetch(self):
        rows, snapshot = self.snapshot(1)
        state = self.ready_state(rows, snapshot)
        code = rows[0]['ticker']
        with patch.object(self.ui, 'get_market_context', return_value=CONTEXT), \
             patch.object(self.ui, 'get_current_quotes', side_effect=[{code: quote(10200)}, {code: quote(9700)}]) as fetch, \
             patch.object(self.ui, 'get_histories') as daily:
            first, _ = self.ui._evaluate_visible(rows, state)
            second, _ = self.ui._evaluate_visible(rows, state)
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(fetch.call_args.kwargs['refresh_seconds'], 10)
        self.assertFalse(daily.called)
        self.assertEqual(second[0]['price'], 9700)
        self.assertEqual(second[0]['chart'].iloc[-1, 0], 9700)
        self.assertNotEqual(first[0]['score'], second[0]['score'])
        self.assertNotEqual(first[0]['mandatory_count'], second[0]['mandatory_count'])

    def test_live_quote_failure_never_reuses_selection_price(self):
        rows, snapshot = self.snapshot(1)
        state = self.ready_state(rows, snapshot)
        with patch.object(self.ui, 'get_market_context', return_value=CONTEXT), \
             patch.object(self.ui, 'get_current_quotes', return_value={}):
            results, _ = self.ui._evaluate_visible(rows, state)
        self.assertIsNone(results[0]['price'])
        self.assertIsNone(results[0]['score'])
        self.assertFalse(results[0]['valid'])

    def test_one_batch_per_ten_second_visible_refresh(self):
        codes = [f'{i:06d}' for i in range(20)]
        with patch.object(self.data, 'datetime', Clock), patch.object(self.data, '_quote_chunk', return_value={}) as chunk:
            self.data.get_current_quotes(codes, refresh_seconds=self.ui.WATCHLIST_REFRESH_SECONDS)
            Clock.value = NOW + timedelta(seconds=10)
            self.data.get_current_quotes(codes, refresh_seconds=self.ui.WATCHLIST_REFRESH_SECONDS)
        self.assertEqual(chunk.call_count, 2)
        self.assertEqual(len(chunk.call_args.args[0]), 20)
        self.assertNotEqual(chunk.call_args_list[0].args[1], chunk.call_args_list[1].args[1])

    def test_timer_request_and_caption_share_ten_second_setting(self):
        from pathlib import Path
        import ast
        source = Path(self.ui.__file__).read_text(encoding='utf-8')
        tree = ast.parse(source)
        renderer = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == '_render_live_watchlist')
        timer = next(item for item in renderer.decorator_list if isinstance(item, ast.Call))
        interval = next(item.value for item in timer.keywords if item.arg == 'run_every')
        self.assertEqual(ast.unparse(interval), 'WATCHLIST_REFRESH_SECONDS')
        self.assertEqual(self.ui.WATCHLIST_REFRESH_SECONDS, 10)
        self.assertEqual(self.ui.WATCHLIST_SELECTION_SECONDS, 60)
        self.assertNotIn('5초', source)
        rows, snapshot = self.snapshot(1)
        self.ready_state(rows, snapshot)
        with patch.object(self.ui, '_evaluate_visible', return_value=(snapshot['results'], CONTEXT)), \
             patch.object(self.ui, '_detail'), patch.object(self.ui, '_schedule_watch_selection'):
            self.ui._render_live_watchlist(rows)
        self.assertTrue(any('가격·조건 10초 간격 조회' in message for message in self.st.messages))
        self.assertTrue(any('10초 간격 조회 요청' in message for message in self.st.messages))

    def test_full_worker_still_evaluates_every_saved_row_and_only_retries_missing_history(self):
        rows, snapshot = self.snapshot(25)
        histories = dict(snapshot['histories'])
        histories[rows[-1]['ticker']] = pd.DataFrame()
        with patch.object(self.ui, 'get_histories', return_value={rows[-1]['ticker']: bars()}) as daily, \
             patch.object(self.ui, 'get_market_context', return_value=CONTEXT), \
             patch.object(self.ui, 'get_current_quotes', return_value={row['ticker']: quote() for row in rows}) as fetch:
            result = self.ui._build_watch_selection(rows, 0, histories, snapshot['history_hour'], {})
        self.assertEqual(len(fetch.call_args.args[0]), 25)
        self.assertEqual(len(result['results']), 25)
        self.assertEqual(daily.call_args.args[0], [rows[-1]])
        self.assertTrue(histories[rows[-1]['ticker']].empty)
        self.assertTrue(all(item['valid'] for item in result['results']))

    def test_next_selection_not_repeated_before_sixty_seconds(self):
        rows, snapshot = self.snapshot()
        state = self.ready_state(rows, snapshot)
        pool = Mock()
        pool.submit.return_value = Future()
        with patch.object(self.ui, '_watchlist_pool', return_value=pool), \
             patch.object(self.ui, 'monotonic', return_value=state['next_selection'] - 1):
            self.ui._schedule_watch_selection(state, rows)
        self.assertFalse(pool.submit.called)
        with patch.object(self.ui, '_watchlist_pool', return_value=pool), \
             patch.object(self.ui, 'monotonic', return_value=state['next_selection']):
            self.ui._schedule_watch_selection(state, rows)
            self.ui._schedule_watch_selection(state, rows)
        self.assertEqual(pool.submit.call_count, 1)

    def test_background_failure_keeps_live_path_available_and_sanitizes_error(self):
        rows, snapshot = self.snapshot()
        state = self.ready_state(rows, snapshot)
        future = Future()
        future.set_exception(RuntimeError('private-payload'))
        state['future'] = future
        updated = self.ui._watch_state(rows)
        self.assertIs(updated['snapshot'], snapshot)
        self.assertIsNone(updated['future'])
        self.assertIn('일봉 확인 실패', updated['error'])
        self.assertNotIn('private', updated['error'])

    def test_revision_membership_and_day_changes_discard_inflight_selection(self):
        rows, snapshot = self.snapshot()
        for change in ('revision', 'membership', 'day'):
            self.st.session_state.clear()
            Clock.value = NOW
            state = self.ui._watch_state(rows)
            old = Future()
            state['future'] = old
            changed_rows = rows
            if change == 'revision':
                self.st.session_state['rise_live_revision'] = 1
            elif change == 'membership':
                changed_rows = rows[:-1]
            else:
                Clock.value = NOW + timedelta(days=1)
            updated = self.ui._watch_state(changed_rows)
            self.assertIsNot(updated, state)
            self.assertIsNone(updated['snapshot'])
            self.assertTrue(old.cancelled())

    def test_background_observations_cannot_roll_back_newer_visible_observation(self):
        rows, snapshot = self.snapshot(1)
        code = rows[0]['ticker']
        state = self.ui._watch_state(rows)
        state['observed_at'][code] = (NOW + timedelta(seconds=5)).isoformat()
        state['observations'][code] = {'count': 9}
        snapshot['observations'][code] = {'count': 1}
        future = Future()
        future.set_result(snapshot)
        state['future'] = future
        self.ui._watch_state(rows)
        self.assertEqual(state['observations'][code]['count'], 9)

    def test_daily_cache_migration_reuses_only_matching_hour_revision_and_membership(self):
        rows, snapshot = self.snapshot(1)
        self.st.session_state['rise_current_watch_histories'] = {
            'key': (NOW.strftime('%Y%m%d%H'), 0, (rows[0]['ticker'],)), 'histories': snapshot['histories']}
        state = self.ui._watch_state(rows)
        self.assertEqual(set(state['histories']), {rows[0]['ticker']})
        self.st.session_state['rise_live_revision'] = 1
        self.assertFalse(self.ui._watch_state(rows)['histories'])

    def test_background_rank_changes_never_replace_saved_members(self):
        rows, snapshot = self.snapshot(25)
        active = rows[:20]
        # Simulate even an old hot-deployment background snapshot containing outsiders.
        snapshot['results'] = list(reversed(snapshot['results']))
        self.ready_state(active, snapshot)
        calls = []
        def visible(chosen, state):
            calls.append([x['ticker'] for x in chosen])
            return ([evaluate_current(row, bars(), quote(), CONTEXT, NOW) for row in chosen], CONTEXT)
        with patch.object(self.ui, '_evaluate_visible', side_effect=visible), \
             patch.object(self.ui, '_detail'), patch.object(self.ui, '_schedule_watch_selection'):
            self.ui._render_live_watchlist(active)
            snapshot['results'].reverse()
            self.ui._render_live_watchlist(active)
        expected = [x['ticker'] for x in active]
        self.assertEqual(calls, [expected, expected])

    def test_entry_reason_survives_live_recalculation_and_price_failure(self):
        rows, snapshot = self.snapshot(1)
        entry = {'entry_asof': '2026-10-02', 'entry_span_pct': 1.5, 'entry_reason': 'closed convergence'}
        rows[0]['watch_entry'] = entry
        state = self.ready_state(rows, snapshot)
        code = rows[0]['ticker']
        with patch.object(self.ui, 'get_market_context', return_value=CONTEXT), \
             patch.object(self.ui, 'get_current_quotes', side_effect=[{code: quote(14000)}, {}]):
            rising, _ = self.ui._evaluate_visible(rows, state)
            failed, _ = self.ui._evaluate_visible(rows, state)
        self.assertEqual(rising[0]['watch_entry'], entry)
        self.assertEqual(failed[0]['watch_entry'], entry)
        self.assertEqual(rising[0]['ticker'], failed[0]['ticker'])
        self.assertFalse(failed[0]['valid'])


if __name__ == '__main__':
    unittest.main()
