"""No network, credentials, orders or real saved-data writes."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rise_watch_cohort import (COHORT_PATH, active_rows, eligible_candidates,
                               reconcile_cohort, retire_member, validate_cohort)

spec = importlib.util.spec_from_file_location('cohort_daily', Path(__file__).resolve().parents[1] / 'scripts/refresh_ma_convergence.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def sample(count=25, day='2026-10-07'):
    rows = [{'ticker': f'{i:06d}', 'name': f'stock{i}', 'market': 'KOSPI', 'custom': i}
            for i in range(count)]
    candidates = [{**x, 'candidate': True, 'eligible': True, 'cluster_now': True,
                   'tradable': True, 'date': day, 'span_pct': 1.0 + i / 100,
                   'close': 10000, 'mean_value20': 1000000000, 'narrowing': True}
                  for i, x in enumerate(rows)]
    return rows, {'complete': True, 'pending': None, 'asof': day, 'candidates': candidates}


class CohortTests(unittest.TestCase):
    def test_initial_twenty_preserve_all_198_records(self):
        rows, daily = sample(198)
        before = deepcopy(rows)
        cohort = reconcile_cohort({}, rows, daily)
        self.assertEqual(len(cohort['active']), 20)
        self.assertEqual(rows, before)
        self.assertEqual(len(active_rows(rows, cohort)), 20)
        self.assertEqual(active_rows(rows, cohort)[0]['custom'], 0)
        self.assertIn('entry_reason', cohort['active'][0])

    def test_next_day_stronger_candidates_do_not_replace_incumbents(self):
        rows, daily = sample()
        cohort = reconcile_cohort({}, rows, daily)
        before = deepcopy(cohort['active'])
        daily['asof'] = '2026-10-08'
        for item in daily['candidates']:
            item['date'] = daily['asof']
            item['span_pct'] = 2 - int(item['ticker']) / 100
        result = reconcile_cohort(cohort, rows, daily)
        self.assertEqual(result['active'], before)

    def test_breakout_and_loss_of_convergence_do_not_evict(self):
        rows, daily = sample()
        cohort = reconcile_cohort({}, rows, daily)
        daily.update(asof='2026-10-08', candidates=[])
        self.assertEqual(reconcile_cohort(cohort, rows, daily)['active'], cohort['active'])

    def test_failed_incomplete_pending_and_old_scan_never_change_state(self):
        rows, daily = sample()
        cohort = reconcile_cohort({}, rows, daily)
        for changed in ({}, {**daily, 'complete': False}, {**daily, 'pending': {'tickers': ['000099']}},
                        {**daily, 'asof': '2026-10-06'}):
            self.assertEqual(reconcile_cohort(cohort, rows, changed), cohort)

    def test_manual_retirement_is_durable_and_refilled_only_next_day(self):
        rows, daily = sample()
        cohort = reconcile_cohort({}, rows, daily)
        retired = retire_member(cohort, '000000', '2026-10-08T10:00:00+09:00')
        self.assertEqual(len(retired['active']), 19)
        self.assertEqual(reconcile_cohort(retired, rows, daily), retired)
        daily['asof'] = '2026-10-08'
        for item in daily['candidates']: item['date'] = daily['asof']
        updated = reconcile_cohort(retired, rows, daily)
        self.assertEqual(len(updated['active']), 20)
        self.assertNotIn('000000', [x['ticker'] for x in updated['active']])
        self.assertEqual(updated['archived'], retired['archived'])
        self.assertEqual(len(rows), 25)

    def test_explicit_saved_deletion_archives_at_next_daily_scan(self):
        rows, daily = sample()
        cohort = reconcile_cohort({}, rows, daily)
        self.assertEqual(len(active_rows(rows[1:], cohort)), 19)
        daily['asof'] = '2026-10-08'
        result = reconcile_cohort(cohort, rows[1:], daily)
        self.assertIn('000000', result['archived'])

    def test_missing_candidates_never_admits_etf_from_items_or_saved_rows(self):
        rows, daily = sample(1)
        rows.append({'ticker': '499660', 'name': 'TIGER CD금리'})
        daily['items'] = {'499660': {**daily['candidates'][0], 'ticker': '499660'}}
        admitted = reconcile_cohort({}, rows, daily)['active']
        self.assertEqual([x['ticker'] for x in admitted], ['000000'])

    def test_rejects_bad_and_stale_and_halted_candidates(self):
        rows, daily = sample(1)
        row = daily['candidates'][0]
        for changes in ({'span_pct': 3.1}, {'span_pct': float('nan')}, {'span_pct': -1},
                        {'close': 999}, {'mean_value20': 1}, {'tradable': False},
                        {'eligible': False}, {'date': '2026-10-06'}, {'cluster_now': False}):
            snapshot = {**daily, 'candidates': [{**row, **changes}]}
            self.assertEqual(eligible_candidates(snapshot, rows), [])

    def test_unsaved_and_duplicate_candidates_do_not_get_admitted(self):
        rows, daily = sample(2)
        daily['candidates'] *= 2
        result = reconcile_cohort({}, rows[:1], daily)
        self.assertEqual(len(result['active']), 1)

    def test_missing_scan_does_not_fabricate_selection(self):
        rows, _ = sample()
        self.assertEqual(reconcile_cohort({}, rows, {})['active'], [])

    def test_bad_cohort_fails_closed(self):
        for value in ({}, {'version': 1, 'active': [{}]}, {'version': 1, 'active': [{}] * 21},
                      {'version': 1, 'active': [{'ticker': '000001'}] * 2}):
            with self.assertRaises(ValueError): validate_cohort(value)

    def test_retire_conflict_does_not_mutate_input(self):
        rows, daily = sample()
        cohort = reconcile_cohort({}, rows, daily)
        original = deepcopy(cohort)
        retire_member(cohort, '000000', '2026-10-08')
        self.assertEqual(cohort, original)
        with self.assertRaises(ValueError): retire_member(cohort, '999999', '2026-10-08')

    def test_daily_publication_retries_without_overwriting_manual_retirement(self):
        rows, daily = sample()
        initial = reconcile_cohort({}, rows, daily)
        daily['asof'] = '2026-10-08'
        for item in daily['candidates']: item['date'] = daily['asof']
        state = [deepcopy(initial)]
        calls = []
        def read(path, branch, default=None):
            return (deepcopy(state[0]), 'sha') if path == COHORT_PATH else (deepcopy(rows), 'watch-sha')
        def write(path, branch, value, sha, message):
            self.assertEqual(path, COHORT_PATH)
            calls.append(value)
            if len(calls) == 1:
                state[0] = retire_member(state[0], '000000', '2026-10-08')
                raise urllib.error.HTTPError('https://example.invalid', 409, 'conflict', {}, None)
            state[0] = deepcopy(value)
        with patch.object(runner, 'github_read', side_effect=read), patch.object(runner, 'github_write', side_effect=write), patch.object(runner.time, 'sleep'):
            runner.publish_cohort(daily)
        self.assertEqual(len(calls), 2)
        self.assertIn('000000', state[0]['archived'])
        self.assertNotIn('000000', [x['ticker'] for x in state[0]['active']])

    def test_same_day_retry_does_not_write_again(self):
        rows, daily = sample()
        cohort = reconcile_cohort({}, rows, daily)
        def read(path, branch, default=None):
            return (cohort, 'sha') if path == COHORT_PATH else (rows, 'sha')
        with patch.object(runner, 'github_read', side_effect=read), patch.object(runner, 'github_write') as write:
            runner.publish_cohort(daily)
        self.assertFalse(write.called)


if __name__ == '__main__':
    unittest.main()
