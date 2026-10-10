from copy import deepcopy
import unittest

import numpy as np
import pandas as pd

from leader_roadmap_phase import phase_history, EARLY, SETUP, RESTART, WEAK, WATCH, WARMUP
from leader_roadmap_phase_ui import phase_chart
from leader_roadmap import relative_strength
from test_leader_roadmap import sector_fixture, ROW
from test_strategy_projects import history, CTX, NOW


def sample(close, sector=None, market=None):
    n = len(close)
    return pd.DataFrame({'Close': close,
        'Sector': sector if sector is not None else [100 + .15 * i for i in range(n)],
        'Market': market if market is not None else [100 + .05 * i for i in range(n)]},
        index=pd.bdate_range('2026-06-01', periods=n), dtype=float)


def prepared():
    return sample([100 + .5 * i for i in range(40)] + [119, 119.3, 119.2, 119.4, 119.1, 119.3, 121])


def recovering():
    close = [100 * .997 ** i for i in range(35)]
    sector = [100 * .999 ** i for i in range(35)]
    for _ in range(26):
        close.append(close[-1] * 1.008)
        sector.append(sector[-1] * 1.004)
    return sample(close, sector, [100] * len(close))


class PhaseTests(unittest.TestCase):
    def test_early_can_appear_before_twenty_day_leadership(self):
        r = phase_history(recovering())
        self.assertTrue(r['valid'])
        early = r['frame'].query('phase == @EARLY')
        self.assertGreater(len(early), 0)
        self.assertTrue(((early.stock20 <= 0) | (early.sector20 <= 0)).any())

    def test_setup_requires_three_closes_and_restart_is_after_setup(self):
        f = phase_history(prepared())['frame']
        self.assertNotEqual(f.phase.iloc[40], SETUP)
        self.assertNotEqual(f.phase.iloc[41], SETUP)
        self.assertEqual(f.phase.iloc[42], SETUP)
        self.assertTrue(f.change.iloc[42])
        self.assertEqual(f.phase.iloc[-2], SETUP)
        self.assertEqual(f.phase.iloc[-1], RESTART)
        self.assertTrue(f.change.iloc[-1])

    def test_constant_new_highs_do_not_look_like_consolidation(self):
        r = phase_history(sample([100 + .5 * i for i in range(61)]))
        self.assertNotIn(SETUP, r['frame'].phase.tolist())
        self.assertNotIn(RESTART, r['frame'].phase.tolist())

    def test_flat_or_equal_relative_returns_are_not_leadership(self):
        p = sample([100] * 61, [100] * 61, [100] * 61)
        f = phase_history(p)['frame']
        self.assertEqual(f.phase.iloc[-1], WATCH)
        self.assertFalse(f.change.any())

    def test_weakness_needs_two_closes_and_not_a_sell_command(self):
        p = sample([120 - .3 * i for i in range(61)])
        f = phase_history(p)['frame']
        self.assertEqual(f.phase.iloc[25], WATCH)
        self.assertEqual(f.phase.iloc[26], WEAK)
        self.assertEqual(phase_history(p)['current'], WEAK)

    def test_first_twenty_five_points_are_warmup(self):
        f = phase_history(prepared())['frame']
        self.assertTrue(f.phase.iloc[:25].eq(WARMUP).all())
        self.assertFalse(f.change.iloc[:25].any())

    def test_adding_future_data_does_not_change_past_classification(self):
        for p in (recovering(), prepared()):
            full = phase_history(p)['frame']
            for end in range(28, len(p)):
                prefix = phase_history(p.iloc[:end])['frame']
                pd.testing.assert_frame_equal(prefix, full.iloc[:end])

    def test_reference_high_excludes_today(self):
        p = prepared()
        f = phase_history(p)['frame']
        self.assertEqual(f.prior_high5.iloc[-1], p.Close.iloc[-6:-1].max())
        self.assertLess(f.prior_high5.iloc[-1], f.close.iloc[-1])

    def test_metrics_are_return_differences_not_ratio_values(self):
        p = prepared()
        f = phase_history(p)['frame']
        for n in (5, 20):
            returns = (p.iloc[-1] / p.iloc[-n-1] - 1) * 100
            self.assertAlmostEqual(f[f'stock{n}'].iloc[-1], returns.Close - returns.Sector)
            self.assertAlmostEqual(f[f'sector{n}'].iloc[-1], returns.Sector - returns.Market)

    def test_bad_inputs_hold(self):
        p = prepared()
        variants = [None, p.iloc[:27], p.iloc[::-1], p.drop(columns='Market'),
                    pd.concat([p, p.iloc[-1:]])]
        for bad in (0, -1, np.nan, np.inf, 999999):
            q = p.copy()
            q.iloc[-1, 0] = bad
            variants.append(q)
        for q in variants:
            self.assertFalse(phase_history(q)['valid'])

    def test_inputs_and_stage_start_are_preserved(self):
        p = prepared().iloc[:-1]
        old = p.copy(deep=True)
        r = phase_history(p)
        pd.testing.assert_frame_equal(p, old)
        self.assertEqual(r['since'], p.index[42].strftime('%Y-%m-%d'))
        self.assertEqual(r['sessions'], 4)

    def test_chart_has_three_panels_shapes_and_common_time_axis(self):
        spec = phase_chart(phase_history(prepared())).to_dict(validate=True)
        self.assertEqual(len(spec['vconcat']), 3)
        self.assertEqual(spec['resolve']['scale']['x'], 'shared')
        self.assertEqual(spec['vconcat'][0]['layer'][1]['encoding']['shape']['field'], 'phase')
        self.assertIn('tooltip', spec['vconcat'][0]['layer'][0]['encoding'])

    def test_closed_alignment_excludes_future_and_holds_bad_sector(self):
        h = history()
        snap = sector_fixture()
        r = relative_strength(ROW['ticker'], h, snap, CTX, NOW)
        expected = phase_history(r['closed_prices'])['frame']
        h.loc[pd.Timestamp('2026-10-12')] = [999999, 999999]
        future = relative_strength(ROW['ticker'], h, snap, CTX, NOW)
        pd.testing.assert_frame_equal(expected, phase_history(future['closed_prices'])['frame'], check_freq=False)
        bad = deepcopy(snap)
        bad['asof'] = '2026-10-07'
        self.assertFalse(relative_strength(ROW['ticker'], h, bad, CTX, NOW)['valid'])


if __name__ == '__main__':
    unittest.main()
