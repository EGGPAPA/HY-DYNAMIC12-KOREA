from copy import deepcopy
from datetime import timedelta
import unittest
from unittest.mock import patch

import pandas as pd

from leader_roadmap import comparison_group, relative_strength, entry_position, build_roadmap
from sector_observation import build_snapshot
from strategy_projects import technical_view
from test_strategy_projects import history, NOW, CTX

ROW = {'ticker': '403870', 'name': '가상 비교 종목', 'market': 'KOSDAQ'}


def sector_fixture(h=None, group='반도체'):
    h = history() if h is None else h
    dates = h.index.strftime('%Y-%m-%d').tolist()
    def bars(rate):
        return [{'date': d, 'close': 100 * (1 + rate) ** i} for i, d in enumerate(dates)]
    # Deliberately synthetic data, not real Korean market observations.
    return build_snapshot({'KOSPI': bars(.00005), 'sample': bars(.0001)},
                          dates[-1], {group: {'sample': '가상 업종 표본'}})


def technical(project='short', review=None):
    item = technical_view(project, ROW, history(), None, CTX, NOW, review)
    item['display_price'] = item['price']
    return item


class RelativeStrengthTests(unittest.TestCase):
    def rs(self, h=None, snap=None, ctx=None, now=NOW):
        return relative_strength(ROW['ticker'], history() if h is None else h,
            sector_fixture() if snap is None else snap, CTX if ctx is None else ctx, now)

    def test_existing_representatives_and_reviewed_map_only(self):
        self.assertEqual(comparison_group('247540'), '2차전지')
        self.assertEqual(comparison_group('005930'), '반도체')
        self.assertEqual(comparison_group('403870'), '반도체')
        self.assertIsNone(comparison_group('011930'))
        self.assertIsNone(comparison_group('999999'))

    def test_exact_same_window_return_differences(self):
        result = self.rs()
        self.assertTrue(result['valid'], result)
        close = history().Close
        for n in (20, 60):
            stock = (close.iloc[-1] / close.iloc[-n-1] - 1) * 100
            sector = ((1.0001) ** n - 1) * 100
            market = ((1.00005) ** n - 1) * 100
            self.assertAlmostEqual(result[f'stock_edge{n}'], stock - sector)
            self.assertAlmostEqual(result[f'sector_edge{n}'], sector - market)
        self.assertEqual(result['relative_chart'].iloc[0].tolist(), [100., 100.])
        self.assertTrue((result['relative_chart'].iloc[-1] > 100).all())

    def test_holiday_and_intraday_use_same_confirmed_strength(self):
        live = {'day':'20261009', 'previous':'20261008', 'basis':'quote', 'intraday':True}
        a, b = self.rs(), self.rs(ctx=live, now=NOW.replace(day=9))
        self.assertTrue(b['valid'])
        self.assertEqual(a['stock_edge20'], b['stock_edge20'])

    def test_missing_middle_day_no_forward_fill(self):
        self.assertFalse(self.rs(h=history().drop(history().index[-20]))['valid'])

    def test_duplicate_day_rejected(self):
        h = history()
        self.assertFalse(self.rs(h=pd.concat([h, h.iloc[-2:-1]]))['valid'])

    def test_missing_latest_day_rejected(self):
        self.assertFalse(self.rs(h=history().iloc[:-1])['valid'])

    def test_future_bars_do_not_leak(self):
        h = history()
        h.loc[pd.Timestamp('2026-10-12')] = [999999, 999999]
        self.assertEqual(self.rs()['stock_edge20'], self.rs(h=h)['stock_edge20'])

    def test_different_sector_asof_held(self):
        snap = sector_fixture()
        snap['asof'] = '2026-10-07'
        self.assertFalse(self.rs(snap=snap)['valid'])

    def test_unclosed_stale_and_future_dates_held(self):
        self.assertFalse(self.rs(now=NOW.replace(day=8,hour=12))['valid'])
        self.assertFalse(self.rs(now=NOW + timedelta(days=15))['valid'])
        self.assertFalse(self.rs(now=NOW.replace(day=7))['valid'])

    def test_failed_market_or_sector_context_held(self):
        self.assertFalse(self.rs(ctx={'error':'수신 실패'})['valid'])
        self.assertFalse(relative_strength(ROW['ticker'], history(), None, CTX, NOW)['valid'])
        snap = sector_fixture()
        snap['rows'] = []
        self.assertFalse(self.rs(snap=snap)['valid'])

    def test_bad_values_and_corporate_jump_held(self):
        for value in (0, -1, float('nan'), float('inf'), 20000):
            h = history()
            h.loc[h.index[-1], 'Close'] = value
            self.assertFalse(self.rs(h=h)['valid'], value)

    def test_missing_or_duplicate_sector_dates_held(self):
        snap = sector_fixture()
        snap['trend'].pop()
        self.assertFalse(self.rs(snap=snap)['valid'])
        snap = sector_fixture()
        snap['trend'].append(deepcopy(snap['trend'][-1]))
        self.assertFalse(self.rs(snap=snap)['valid'])

    def test_inputs_are_not_mutated(self):
        h, snap = history(), sector_fixture()
        before_h, before_s = h.copy(), deepcopy(snap)
        self.rs(h=h, snap=snap)
        pd.testing.assert_frame_equal(before_h, h)
        self.assertEqual(before_s, snap)


class EntryRoadmapTests(unittest.TestCase):
    def roadmap(self, project='short', item=None, snap=None, eligible=True):
        return build_roadmap(project, [technical(project) if item is None else item],
            {ROW['ticker']:history()}, sector_fixture() if snap is None else snap,
            CTX, NOW, {ROW['ticker']} if eligible else set())[0]

    def test_full_short_chain_requires_existing_eligibility(self):
        self.assertTrue(self.roadmap()['ready'])
        self.assertFalse(self.roadmap(eligible=False)['ready'])
        self.assertEqual(self.roadmap(eligible=False)['verdict'], '주도력 재점검')

    def test_entry_prices_are_existing_rule_not_new_model(self):
        item = technical()
        prior = history().Close.iloc[:-1]
        self.assertEqual(item['breakout_floor'], prior.tail(20).max())
        self.assertEqual(item['breakout_ceiling'], prior.tail(20).max() * 1.03)
        self.assertEqual(item['stop_reference'], prior.tail(10).min() * .99)
        self.assertTrue(entry_position('short', item)['passed'])

    def test_medium_requires_manual_fundamentals(self):
        before = self.roadmap('medium')
        self.assertFalse(before['ready'])
        self.assertIn('실적', before['entry']['label'])
        review = {'fundamental_ok':True, 'thesis':'가상 근거', 'source':'https://example.com', 'checked_on':'2026-10-10'}
        after = self.roadmap('medium', technical('medium', review))
        self.assertTrue(after['ready'])

    def test_hot_or_wide_risk_never_green(self):
        for values in ({'overheated':True}, {'stop_distance_pct':8}, {'stop_distance_pct':0}):
            item = technical()
            item.update(values)
            self.assertFalse(self.roadmap(item=item)['ready'])

    def test_failed_price_never_ready(self):
        item = technical()
        item.update(valid=False, reason='시세 지연', price=None)
        self.assertFalse(self.roadmap(item=item)['ready'])

    def test_missing_classification_is_hold_not_weakness(self):
        item = technical()
        item['ticker'] = '011930'
        result = self.roadmap(item=item)
        self.assertIsNone(result['sector_ok'])
        self.assertIsNone(result['stock_ok'])
        self.assertFalse(result['ready'])
        self.assertIn('미지정', result['reason'])

    def test_all_four_strength_combinations_and_horizon(self):
        for sector20, sector60, stock20, stock60 in ((1,1,1,1), (1,-1,1,1),
                (1,1,1,-1), (-1,1,1,1), (1,1,-1,1), (0,1,1,1)):
            strength = dict(valid=True, reason='', group='반도체', asof='2026-10-08',
                sector_edge20=sector20, sector_edge60=sector60, stock_edge20=stock20, stock_edge60=stock60)
            with patch('leader_roadmap.relative_strength', return_value=strength):
                short = self.roadmap()
                mid = self.roadmap('medium')
                self.assertEqual(short['sector_ok'], sector20 > 0)
                self.assertEqual(mid['sector_ok'], sector20 > 0 and sector60 > 0)
                self.assertEqual(short['stock_ok'], stock20 > 0)
                self.assertEqual(mid['stock_ok'], stock20 > 0 and stock60 > 0)

    def test_metadata_missing_or_nonfinite_is_hold(self):
        for values in ({'breakout_floor':None}, {'price':float('nan')}, {'breakout_checks':{}}):
            item = technical()
            item.update(values)
            self.assertFalse(entry_position('short', item)['valid'])

    def test_no_forced_rank_or_saved_row_change(self):
        item = technical()
        old = deepcopy(item)
        self.roadmap(item=item)
        pd.testing.assert_frame_equal(old.pop('chart'), item['chart'])
        self.assertEqual(old, {k:v for k,v in item.items() if k != 'chart'})


if __name__ == '__main__': unittest.main()
