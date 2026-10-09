import ast
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from pathlib import Path
import unittest

from sector_rotation import analyze_rotation
from watch_sector_context import business_context, sector_flow, leader_comparison, BUSINESSES
from rise_watch_cohort import replace_leader
from rise_leaders import leader_entry, leader_candidates


def fixture():
    saved = [{'ticker': f'{i:06d}', 'name': f'stock{i}', 'market': 'KOSPI'} for i in range(1, 26)]
    items = {x['ticker']: {**x, 'ordinary': True, 'tradable': True, 'eligible': True,
        'strength_version': 1, 'date': '2026-10-08', 'close': 12000, 'ma20': 11000, 'ma60': 10000,
        'ma20_prev5': 10500, 'ret10_pct': 5, 'ret20_pct': 10+i, 'ret60_pct': 20+i,
        'mean_value20': 3_000_000_000, 'span_pct': 9, 'cluster_now': False}
        for i, x in enumerate(saved)}
    snapshot = {'complete': True, 'pending': None, 'leader_version': 1, 'asof': '2026-10-08',
        'items': items, 'benchmarks': {m: {'eligible': True, 'date': '2026-10-08',
        'strength_version': 1, 'ret20_pct': 3, 'ret60_pct': 5} for m in ('KOSPI', 'KOSDAQ')}}
    ranked = leader_candidates(snapshot, saved)
    cohort = {'version': 1, 'policy': 'leader_v1', 'active': [leader_entry(x, snapshot['asof']) for x in ranked[:20]],
              'archived': {}, 'last_review_asof': snapshot['asof']}
    return saved, cohort, snapshot


def sectors(values):
    rows = [{'date': f'2026-10-{5+i:02d}', 'strengths': dict(zip(['반도체', '2차전지', '헬스케어'], v))}
            for i, v in enumerate(values)]
    return {'asof': rows[-1]['date'], 'rotation': analyze_rotation(rows)}


class SectorContextTests(unittest.TestCase):
    def test_twenty_reviewed_businesses_have_sources(self):
        self.assertEqual(len(BUSINESSES), 20)
        for code in BUSINESSES:
            self.assertTrue(business_context(code)['source'].startswith('https://'))

    def test_mixed_businesses_not_forced_into_battery_theme(self):
        for code in ['011930', '001120', '011790', '009830', '089010']:
            self.assertIsNone(business_context(code)['group'])
            self.assertEqual(sector_flow(code, {}, '2026-10-09'), '— 비교업종 미지정')

    def test_unknown_is_not_inferred_from_name(self):
        self.assertEqual(business_context('999999')['primary'], '분류 확인 중')

    def test_sector_leader_requires_positive_strength_and_three_closes(self):
        s = sectors([(1, 2, 0), (3, 2, 0)])
        self.assertEqual(sector_flow('403870', s, '2026-10-09'), '반도체 · 주도 후보')
        s = sectors([(1, 2, 0), (3, 2, 0), (4, 2, 0), (5, 2, 0)])
        self.assertEqual(sector_flow('403870', s, '2026-10-09'), '반도체 · 주도')

    def test_closing_gap_is_not_automatically_called_leader(self):
        s = sectors([(10, 1, 0), (10, 2, 0), (10, 3, 0), (10, 4, 0)])
        self.assertEqual(sector_flow('450080', s, '2026-10-09'), '2차전지 · 추격')

    def test_tie_negative_and_neutral_not_mislabeled(self):
        s = sectors([(2, 2, -1), (2, 2, -1)])
        self.assertEqual(sector_flow('403870', s, '2026-10-09'), '반도체 · 공동 1위')
        self.assertEqual(sector_flow('067630', s, '2026-10-09'), '헬스케어 · 시장 하회')
        s = sectors([(-1, -2, -3), (-1, -2, -3)])
        self.assertIn('시장 하회', sector_flow('403870', s, '2026-10-09'))

    def test_missing_stale_future_partial_or_wrong_date_holds(self):
        s = sectors([(3, 2, 1), (3, 2, 1)])
        for snapshot, day in [(None, '2026-10-09'), ({}, '2026-10-09'),
                              (s, '2026-11-09'), (s, '2026-10-01')]:
            self.assertEqual(sector_flow('403870', snapshot, day), '— 평가 없음')
        s['rotation']['valid'] = False
        self.assertEqual(sector_flow('403870', s, '2026-10-09'), '— 평가 없음')
        s['rotation']['valid'] = True
        s['rotation']['asof'] = '2026-10-01'
        self.assertEqual(sector_flow('403870', s, '2026-10-09'), '— 평가 없음')


class ComparisonTests(unittest.TestCase):
    def test_no_mutation_and_exactly_five_outside_twenty(self):
        rows, cohort, snapshot = fixture()
        before = deepcopy((rows, cohort, snapshot))
        result = leader_comparison(rows, cohort, snapshot, '2026-10-09')
        self.assertTrue(result['ready'])
        self.assertEqual(len(result['rows']), 5)
        self.assertEqual((rows, cohort, snapshot), before)

    def test_retired_and_unsaved_excluded(self):
        rows, cohort, snapshot = fixture()
        cohort['archived']['000001'] = {'ticker': '000001'}
        rows = rows[1:]
        result = leader_comparison(rows, cohort, snapshot, '2026-10-09')
        self.assertEqual(len(result['rows']), 4)

    def test_missing_old_future_or_incomplete_is_not_no_candidates(self):
        for field, value in [('leader_version', None), ('pending', 'pending'), ('complete', False),
                             ('asof', '2026-10-10'), ('asof', '2026-09-01')]:
            rows, cohort, snapshot = fixture()
            snapshot[field] = value
            self.assertFalse(leader_comparison(rows, cohort, snapshot, '2026-10-09')['ready'])
        self.assertFalse(leader_comparison([], None, {}, '2026-10-09')['ready'])

    def test_manual_swap_preserves_count_saved_rows_and_history(self):
        rows, cohort, snapshot = fixture()
        before = deepcopy((rows, cohort, snapshot))
        old = cohort['active'][0]['ticker']
        result = replace_leader(cohort, rows, snapshot, old, '000001', '2026-10-09T16:01:00+09:00', '2026-10-08')
        self.assertEqual(len(result['active']), 20)
        self.assertEqual(result['active'][0]['ticker'], '000001')
        self.assertIn(old, result['archived'])
        self.assertEqual(result['selection_history'][-1]['active'], cohort['active'])
        self.assertEqual((rows, cohort, snapshot), before)

    def test_swap_rejects_changed_date_duplicate_unsaved_or_changed_membership(self):
        rows, cohort, snapshot = fixture()
        old = cohort['active'][0]['ticker']
        for outgoing, incoming, day in [(old, '000001', '2026-10-07'), (old, old, '2026-10-08'),
                                      ('999999', '000001', '2026-10-08'), (old, '999999', '2026-10-08')]:
            with self.assertRaises(ValueError):
                replace_leader(cohort, rows, snapshot, outgoing, incoming, '2026-10-09T16:00:00+09:00', day)

    def test_timers_never_write_and_sector_context_never_fetches(self):
        root = Path(__file__).parent
        pure = (root/'watch_sector_context.py').read_text(encoding='utf8')
        self.assertNotIn('requests.', pure)
        self.assertNotIn('urlopen', pure)
        tree = ast.parse((root/'rise_current_price_ui.py').read_text(encoding='utf8'))
        node = next(x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name == '_render_live_watchlist')
        self.assertNotIn('_daily_snapshot', ast.unparse(node))
        self.assertNotIn('replace_leader', ast.unparse(node))

    def test_swap_requires_checkbox_button_and_server_side_revalidation(self):
        root = Path(__file__).parent
        source = (root/'watch_leader_comparison_ui.py').read_text(encoding='utf8')
        self.assertIn('disabled=not (allowed and confirmed)', source)
        tree = ast.parse((root/'rise_timing_watchlist_ui.py').read_text(encoding='utf8'))
        node = next(x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name == '_replace_watch_leader')
        code = ast.unparse(node)
        self.assertIn('replacement_window(now)', code)
        self.assertIn('_load_convergence_state.clear()', code)
        self.assertIn("'sha': sha", code)
        self.assertNotIn('_save_watchlist', code)

    def test_no_intraday_swaps_including_weekday_holidays(self):
        root = Path(__file__).parent
        tree = ast.parse((root/'watch_leader_comparison_ui.py').read_text(encoding='utf8'))
        node = next(x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name == 'replacement_window')
        from datetime import time
        ns = {'KST': timezone(timedelta(hours=9)), 'time': time}
        exec(compile(ast.Module(body=[node], type_ignores=[]), '<window>', 'exec'), ns)
        for hour, allowed in [(8, True), (9, False), (15, False), (16, True)]:
            self.assertEqual(ns['replacement_window'](datetime(2026,10,9,hour,tzinfo=ns['KST'])), allowed)
        self.assertTrue(ns['replacement_window'](datetime(2026,10,10,10,tzinfo=ns['KST'])))


if __name__ == '__main__':
    unittest.main()
