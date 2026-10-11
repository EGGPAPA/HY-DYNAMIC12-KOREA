from copy import deepcopy
from datetime import timedelta
import unittest

from leader_sector_focus import sector_focus, sector_codes, focused_candidates
from leader_sector_data import build_focus_source
from sector_observation import SECTORS, build_snapshot
from sector_rotation import build_rotation
from test_strategy_projects import NOW, history, snapshot


def focus_fixture(leader='2차전지', tie=False, weak=False):
    days = history().index.strftime('%Y-%m-%d').tolist()
    def bars(rate):
        return [{'date': d, 'close': 100 * (1 + rate) ** i} for i, d in enumerate(days)]
    groups = {name: {f'sample_{i}': '가상 업종 표본'} for i, name in enumerate(SECTORS)}
    histories = {'KOSPI': bars(.00005)}
    for i, name in enumerate(groups):
        rate = .0001 if name == leader or (tie and name == '반도체') else .00006
        histories[f'sample_{i}'] = bars(rate if not weak else -.0001 - i * .00001)
    result = build_snapshot(histories, days[-1], groups)
    result['rotation'] = build_rotation(histories, days[-1], groups)
    return result


def source_fixture():
    source = snapshot()
    item = source['items'].pop('000001')
    for code, name, market, gain in [('373220', '가상 전지 A', 'KOSPI', 5),
                                    ('006400', '가상 전지 B', 'KOSPI', 4),
                                    ('005930', '가상 반도체', 'KOSPI', 100)]:
        source['items'][code] = {**item, 'ticker': code, 'name': name, 'market': market, 'ret20_pct': gain}
    return source


class FocusTests(unittest.TestCase):
    def test_current_first_sector_not_all_positive_sectors(self):
        result = sector_focus(focus_fixture(), NOW)
        self.assertTrue(result['valid'], result)
        self.assertEqual(result['groups'], ['2차전지'])
        self.assertIn('반도체', [r['sector'] for r in result['others']])

    def test_unconfirmed_leader_is_labeled_not_replaced_by_previous_leader(self):
        s = focus_fixture()
        s['rotation']['streak'] = 2
        f = sector_focus(s, NOW)
        self.assertEqual(f['groups'], ['2차전지'])
        self.assertIn('확인 중', f['status'])

    def test_tied_first_includes_only_tied_winners(self):
        f = sector_focus(focus_fixture(tie=True), NOW)
        self.assertEqual(set(f['groups']), {'반도체', '2차전지'})
        self.assertEqual(f['status'], '공동 1위')

    def test_weak_market_leader_is_not_admitted(self):
        self.assertFalse(sector_focus(focus_fixture(weak=True), NOW)['valid'])

    def test_incomplete_mismatch_and_stale_do_not_fall_back(self):
        base = focus_fixture()
        variants = [None, {}, {**base, 'rows': base['rows'][:-1]},
                    {**base, 'rotation': {'valid': False}}, {**base, 'asof': '2026-10-07'}]
        bad = deepcopy(base)
        bad['rows'][0]['excess20'] = float('nan')
        variants.append(bad)
        bad = deepcopy(base)
        bad['rotation']['daily'][-1]['strengths'].pop('반도체')
        variants.append(bad)
        for s in variants:
            self.assertFalse(sector_focus(s, NOW)['valid'])
        self.assertFalse(sector_focus(base, NOW + timedelta(days=20))['valid'])
        self.assertFalse(sector_focus(base, NOW.replace(day=8, hour=10))['valid'])

    def test_scope_uses_confirmed_mapping_and_includes_unsaved_representatives(self):
        codes = sector_codes(['2차전지'])
        self.assertTrue({'373220', '006400', '247540', '450080'}.issubset(codes))
        self.assertNotIn('005930', codes)
        self.assertNotIn('011930', codes)  # mixed/theme only, never guessed
        self.assertNotIn('086520', codes)

    def test_selection_filters_sector_before_ranking_even_with_stronger_other_stock(self):
        s, f = source_fixture(), sector_focus(focus_fixture(), NOW)
        before = deepcopy(s)
        for project in ('short', 'medium'):
            report = focused_candidates(s, f, project, NOW)
            self.assertEqual({x['ticker'] for x in report['rows']}, {'373220', '006400'})
            self.assertTrue(all(x['focus_group'] == '2차전지' for x in report['rows']))
        self.assertEqual(s, before)

    def test_no_fill_when_no_sector_candidate_or_wrong_date(self):
        s, f = source_fixture(), sector_focus(focus_fixture(), NOW)
        s['items'] = {'005930': s['items']['005930']}
        self.assertEqual(focused_candidates(s, f, 'short', NOW)['rows'], [])
        s['asof'] = '2026-10-07'
        self.assertFalse(focused_candidates(s, f, 'short', NOW)['ready'])

    def test_focus_rotation_changes_scope_without_mutating_input(self):
        s = source_fixture()
        report = focused_candidates(s, sector_focus(focus_fixture('반도체'), NOW), 'short', NOW)
        self.assertEqual([r['ticker'] for r in report['rows']], ['005930'])


class FocusDataTests(unittest.TestCase):
    def source(self, mutate=None):
        h = history()
        bars = [{'date': d.strftime('%Y-%m-%d'), 'close': float(r.Close), 'volume': 400000}
                for d, r in h.iterrows()]
        listing = {'373220': {'ticker': '373220', 'name': '가상 전지', 'market': 'KOSPI',
                             'ordinary': True, 'tradable': True}}
        histories = {'373220': bars}
        benchmarks = snapshot()['benchmarks']
        if mutate:
            mutate(listing, histories, benchmarks)
        return build_focus_source(('373220',), '2026-10-08', listing, histories, benchmarks)

    def test_builds_metrics_for_unstored_stock_and_ignores_future_bar(self):
        s = self.source()
        self.assertIn('373220', s['items'])
        later = self.source(lambda l, h, b: h['373220'].append({'date':'2026-10-12','close':999999,'volume':1}))
        self.assertEqual(s, later)

    def test_unknown_or_halted_listing_excluded(self):
        for change in (lambda l,h,b: l.clear(),
                       lambda l,h,b: l['373220'].update(tradable=False),
                       lambda l,h,b: l['373220'].update(ordinary=False)):
            result = self.source(change)
            self.assertEqual(result['items'], {})
            self.assertEqual(len(result['excluded']), 1)

    def test_invalid_daily_data_not_filled_from_old_snapshot(self):
        for change in (lambda l,h,b: h['373220'].pop(),
                       lambda l,h,b: h['373220'].append(h['373220'][-1]),
                       lambda l,h,b: h['373220'][-1].update(volume=0),
                       lambda l,h,b: h['373220'][-1].update(close=999999)):
            self.assertEqual(self.source(change)['items'], {})

    def test_benchmark_mismatch_holds(self):
        with self.assertRaises(ValueError):
            self.source(lambda l,h,b: b['KOSPI'].update(date='2026-10-07'))


if __name__ == '__main__': unittest.main()
