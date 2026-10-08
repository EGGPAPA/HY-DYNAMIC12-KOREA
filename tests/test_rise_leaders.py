"""Deterministic leader selection; no market or account access."""
from copy import deepcopy
from datetime import datetime, timedelta
import unittest
from unittest.mock import patch

from ma_convergence import analyze_bars
from rise_leaders import LEADER_POLICY, leader_candidates, leader_snapshot_ready
from rise_watch_cohort import reselect_leaders, reconcile_cohort, retire_member, active_rows
from scripts import refresh_ma_convergence as runner


def fixture(count=25, day='2026-10-08'):
    saved = [{'ticker': f'{i:06d}', 'name': f'stock{i}', 'market': 'KOSPI', 'custom': i}
             for i in range(count)]
    items = {x['ticker']: {**x, 'ordinary': True, 'tradable': True, 'eligible': True,
        'strength_version': 1, 'date': day, 'close': 12000, 'ma20': 11000, 'ma60': 10000,
        'ma20_prev5': 10500, 'ret10_pct': 5, 'ret20_pct': 10 + i / 10,
        'ret60_pct': 20 + i / 10, 'mean_value20': 3_000_000_000 + i * 1_000_000,
        'span_pct': 9, 'cluster_now': False} for i, x in enumerate(saved)}
    benchmarks = {m: {'eligible': True, 'date': day, 'strength_version': 1,
                      'ret20_pct': 3, 'ret60_pct': 5} for m in ('KOSPI', 'KOSDAQ')}
    return saved, {'complete': True, 'pending': None, 'leader_version': 1,
                   'asof': day, 'benchmarks': benchmarks, 'items': items}


def old_cohort():
    return {'version': 1, 'active': [{'ticker': '000000', 'name': 'stock0', 'market': 'KOSPI',
             'entry_asof': '2026-10-07', 'entry_reason': 'convergence', 'entry_span_pct': 1}],
            'archived': {}, 'last_review_asof': '2026-10-07'}


class LeaderTests(unittest.TestCase):
    def test_returns_use_twenty_and_sixty_intervals_and_ignore_future(self):
        rows = [{'date': (datetime(2026, 1, 1) + timedelta(days=i)).date().isoformat(),
                 'close': 10000 + i * 100, 'volume': 300000} for i in range(100)]
        day = rows[-1]['date']
        result = analyze_bars(rows + [{'date': '2027-01-01', 'close': 999999, 'volume': 1}], day)
        self.assertAlmostEqual(result['ret20_pct'], (19900 / 17900 - 1) * 100)
        self.assertAlmostEqual(result['ret60_pct'], (19900 / 13900 - 1) * 100)
        self.assertAlmostEqual(result['ma20_prev5'], sum(x['close'] for x in rows[-25:-5]) / 20)

    def test_nonconverged_leaders_are_admitted_and_inputs_unchanged(self):
        saved, snapshot = fixture()
        before = deepcopy((saved, snapshot))
        selected = leader_candidates(snapshot, saved)
        self.assertEqual(len(selected), 25)
        self.assertTrue(all(not x['cluster_now'] for x in selected))
        self.assertEqual(selected[0]['ticker'], '000024')
        self.assertEqual((saved, snapshot), before)

    def test_convergence_does_not_affect_score_or_order(self):
        saved, snapshot = fixture()
        original = [(x['ticker'], x['leader_score']) for x in leader_candidates(snapshot, saved)]
        for x in snapshot['items'].values(): x.update(cluster_now=True, span_pct=0)
        self.assertEqual([(x['ticker'], x['leader_score']) for x in leader_candidates(snapshot, saved)], original)

    def test_matching_market_benchmark_is_required(self):
        saved, snapshot = fixture(1)
        snapshot['items']['000000']['market'] = 'KOSDAQ'
        snapshot['benchmarks']['KOSDAQ']['ret20_pct'] = 20
        self.assertEqual(leader_candidates(snapshot, saved), [])
        snapshot['items']['000000']['market'] = 'KOSPI'
        self.assertEqual(len(leader_candidates(snapshot, saved)), 1)

    def test_liquidity_trend_and_both_relative_returns_are_mandatory(self):
        saved, snapshot = fixture(1)
        for change in ({'mean_value20': 1_999_999_999}, {'close': 999}, {'close': 10900},
                       {'ma60': 11000}, {'ma20_prev5': 11000}, {'ret20_pct': 0},
                       {'ret60_pct': 0}, {'ret20_pct': 3}, {'ret60_pct': 5}):
            altered = deepcopy(snapshot)
            altered['items']['000000'].update(change)
            self.assertEqual(leader_candidates(altered, saved), [], change)
        snapshot['items']['000000']['mean_value20'] = 2_000_000_000
        self.assertEqual(len(leader_candidates(snapshot, saved)), 1)

    def test_etf_unknown_market_halted_stale_invalid_and_unsaved_cannot_enter(self):
        saved, snapshot = fixture(1)
        for change in ({'ordinary': False}, {'ordinary': None}, {'tradable': False},
                       {'eligible': False}, {'market': 'unknown'}, {'date': '2026-10-07'},
                       {'ret20_pct': float('nan')}, {'ma20': 0}, {'mean_value20': float('inf')},
                       {'strength_version': None}, {'ticker': '999999'}):
            altered = deepcopy(snapshot)
            altered['items']['000000'].update(change)
            self.assertEqual(leader_candidates(altered, saved), [], change)
        self.assertEqual(leader_candidates(snapshot, []), [])
        self.assertEqual(len(leader_candidates(snapshot, saved * 3)), 1)

    def test_failed_pending_old_format_and_stale_benchmarks_fail_closed(self):
        saved, snapshot = fixture()
        for change in ({'complete': False}, {'pending': {'tickers': []}}, {'leader_version': 0},
                       {'benchmarks': {}}, {'asof': '2026-10-09'}, {'items': None}):
            altered = {**snapshot, **change}
            self.assertFalse(leader_snapshot_ready(altered))
            self.assertEqual(leader_candidates(altered, saved), [])

    def test_ties_are_deterministic_and_single_candidate_is_not_fake_one_hundred(self):
        saved, snapshot = fixture(1)
        self.assertEqual(leader_candidates(snapshot, saved)[0]['leader_score'], 50)
        saved, snapshot = fixture(2)
        template = snapshot['items']['000000']
        snapshot['items']['000001'].update({k: template[k] for k in ('ret20_pct','ret60_pct','mean_value20')})
        self.assertEqual([x['ticker'] for x in leader_candidates(snapshot, list(reversed(saved)))], ['000000','000001'])

    def test_overheat_is_reference_not_false_buy_confirmation(self):
        saved, snapshot = fixture(1)
        snapshot['items']['000000']['ret20_pct'] = 50
        self.assertTrue(leader_candidates(snapshot, saved)[0]['leader_overheated'])

    def test_recomposition_preserves_all_208_rows_and_previous_membership(self):
        saved, snapshot = fixture(208)
        previous = old_cohort()
        before = deepcopy((saved, snapshot, previous))
        result = reselect_leaders(previous, saved, snapshot)
        self.assertEqual(len(result['active']), 20)
        self.assertEqual(result['policy'], LEADER_POLICY)
        self.assertEqual(result['selection_history'][0]['active'], previous['active'])
        self.assertEqual(len(active_rows(saved, result)), 20)
        self.assertEqual((saved, snapshot, previous), before)
        self.assertEqual(reselect_leaders(result, saved, snapshot), result)

    def test_fewer_qualifiers_not_padded_with_nonleaders(self):
        saved, snapshot = fixture(5)
        self.assertEqual(len(reselect_leaders(old_cohort(), saved, snapshot)['active']), 5)

    def test_explicit_retirements_are_not_reintroduced_by_recomposition(self):
        saved, snapshot = fixture(25)
        previous = old_cohort()
        previous['archived']['000024'] = {'reason': 'user retired'}
        result = reselect_leaders(previous, saved, snapshot)
        self.assertNotIn('000024', [x['ticker'] for x in result['active']])
        self.assertEqual(result['archived'], previous['archived'])

    def test_unavailable_zero_or_older_data_cannot_erase_previous(self):
        saved, snapshot = fixture()
        for changed in ({**snapshot, 'complete': False}, {**snapshot, 'items': {}},
                        {**snapshot, 'asof': '2026-10-01'}):
            previous = old_cohort()
            before = deepcopy(previous)
            with self.assertRaises(ValueError): reselect_leaders(previous, saved, changed)
            self.assertEqual(previous, before)

    def test_daily_maintenance_preserves_members_and_fills_only_retired_vacancy(self):
        saved, snapshot = fixture()
        result = reselect_leaders(old_cohort(), saved, snapshot)
        saved, next_day = fixture(day='2026-10-09')
        self.assertEqual(reconcile_cohort(result, saved, next_day)['active'], result['active'])
        code = result['active'][0]['ticker']
        retired = retire_member(result, code, '2026-10-09')
        self.assertEqual(reconcile_cohort(retired, saved, snapshot), retired)
        filled = reconcile_cohort(retired, saved, next_day)
        self.assertEqual(len(filled['active']), 20)
        self.assertNotIn(code, [x['ticker'] for x in filled['active']])
        self.assertTrue(all(x['entry_policy'] == LEADER_POLICY for x in filled['active']))

    def test_missing_benchmarks_do_not_fallback_to_convergence_or_advance_review(self):
        saved, snapshot = fixture()
        result = reselect_leaders(old_cohort(), saved, snapshot)
        result = retire_member(result, result['active'][0]['ticker'], '2026-10-09')
        self.assertEqual(reconcile_cohort(result, saved, {'complete': True, 'asof': '2026-10-09', 'candidates': saved}), result)

    def test_weakening_incumbents_remain_under_observation(self):
        saved, snapshot = fixture()
        result = reselect_leaders(old_cohort(), saved, snapshot)
        saved, next_day = fixture(day='2026-10-09')
        for x in next_day['items'].values(): x['ret20_pct'] = -30
        self.assertEqual(reconcile_cohort(result, saved, next_day)['active'], result['active'])

    def test_benchmark_fetch_failure_is_not_zero_return(self):
        with patch.object(runner, 'fetch_bars', side_effect=ValueError('unavailable')):
            result = runner.fetch_benchmarks('2026-10-08')
        self.assertTrue(all(not x['eligible'] for x in result.values()))
        self.assertTrue(all('ret20_pct' not in x for x in result.values()))


if __name__ == '__main__':
    unittest.main()

