import ast
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
import unittest

from sector_observation import build_snapshot
from sector_rotation import analyze_rotation, build_rotation


def trading_days(count):
    days = []
    day = date(2026, 5, 1)
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    return days


def observations(values):
    return [{'date': day, 'strengths': dict(zip('ABC', row))}
            for day, row in zip(trading_days(len(values)), values)]


class RotationTests(unittest.TestCase):
    def test_cross_then_three_closes_confirmed(self):
        rows = observations([(3, 2, 0), (1, 2, 0), (1, 3, 0), (1, 4, 0)])
        result = analyze_rotation(rows)
        self.assertEqual(result['leader'], 'B')
        self.assertEqual(result['first_cross_date'], rows[1]['date'])
        self.assertEqual(result['confirmed_date'], rows[3]['date'])
        self.assertEqual(result['streak'], 3)
        self.assertEqual(result['previous_leader'], 'A')

    def test_two_days_not_confirmed(self):
        result = analyze_rotation(observations([(3, 2, 0), (1, 2, 0), (1, 3, 0)]))
        self.assertIsNone(result['confirmed_date'])
        self.assertIn('확인 중', result['status'])

    def test_explicit_two_day_rule(self):
        rows = observations([(3, 2, 0), (1, 2, 0), (1, 3, 0)])
        result = analyze_rotation(rows, confirmation_days=2)
        self.assertEqual(result['confirmed_date'], rows[2]['date'])
        self.assertIn('2거래일', result['status'])

    def test_one_day_false_start_does_not_confirm(self):
        result = analyze_rotation(observations([(3, 2, 0), (1, 2, 0), (3, 2, 0)]))
        self.assertEqual(len(result['events']), 2)
        self.assertTrue(all(x['confirmed_date'] is None for x in result['events']))
        self.assertEqual(result['streak'], 1)

    def test_history_event_duration_and_ongoing_status(self):
        result = analyze_rotation(observations([(3, 2, 0), (1, 2, 0), (1, 2, 0), (1, 2, 0), (4, 2, 0)]))
        event = result['events'][1]
        self.assertFalse(event['ongoing'])
        self.assertEqual(event['days'], 3)
        self.assertIsNotNone(event['confirmed_date'])
        self.assertTrue(result['events'][0]['ongoing'])

    def test_left_boundary_not_invented_as_first_cross(self):
        result = analyze_rotation(observations([(3, 2, 0)] * 4))
        self.assertTrue(result['left_censored'])
        self.assertEqual(result['streak'], 4)
        self.assertEqual(result['events'], [])
        self.assertIsNone(result['first_cross_date'])
        self.assertIsNone(result['confirmed_date'])

    def test_tie_resets_streak_and_is_not_a_cross(self):
        result = analyze_rotation(observations([(3, 2, 0), (2, 2, 0), (1, 2, 0)]))
        self.assertEqual(result['events'][0]['kind'], 'tie_resolved')
        self.assertIsNone(result['first_cross_date'])
        self.assertEqual(result['streak'], 1)

    def test_latest_tie_suspends_unique_leader(self):
        result = analyze_rotation(observations([(3, 2, 0), (2, 2, 0)]))
        self.assertIsNone(result['leader'])
        self.assertEqual(result['leaders'], ['A', 'B'])
        self.assertEqual(result['streak'], 0)

    def test_near_machine_precision_is_tied(self):
        result = analyze_rotation(observations([(3, 2, 0), (2, 2 + 1e-10, 0)]))
        self.assertIsNone(result['leader'])

    def test_display_rounding_does_not_make_tie(self):
        result = analyze_rotation(observations([(3, 2, 0), (1.004, 1.003, 0)]))
        self.assertEqual(result['leader'], 'A')

    def test_nonleader_pair_cross_does_not_change_leader(self):
        result = analyze_rotation(observations([(5, 2, 1), (5, 1, 2)]))
        self.assertEqual(result['events'], [])

    def test_closing_gap_three_consecutive_sessions(self):
        result = analyze_rotation(observations([(10, 2, 0), (10, 4, 0), (10, 6, 0), (10, 8, 0)]))
        self.assertEqual(result['closing_days'], 3)
        self.assertEqual(result['gap'], 2)
        self.assertEqual(result['gap_change'], -2)

    def test_gap_compares_same_pair_even_if_runner_up_changes(self):
        result = analyze_rotation(observations([(10, 5, 9), (10, 9, 8)]))
        self.assertEqual(result['challenger'], 'B')
        self.assertEqual(result['gap_change'], -4)

    def test_flat_gap_breaks_closing_streak(self):
        result = analyze_rotation(observations([(10, 2, 0), (10, 4, 0), (10, 4, 0), (10, 6, 0)]))
        self.assertEqual(result['closing_days'], 1)

    def test_weekend_does_not_add_to_duration(self):
        rows = observations([(3, 2, 0), (1, 2, 0), (1, 2, 0), (1, 2, 0)])
        for row, day in zip(rows, ['2026-10-01', '2026-10-02', '2026-10-05', '2026-10-06']):
            row['date'] = day
        result = analyze_rotation(rows)
        self.assertEqual(result['streak'], 3)
        self.assertEqual(result['confirmed_date'], '2026-10-06')

    def test_all_negative_still_relative_not_absolute_leader(self):
        result = analyze_rotation(observations([(-1, -2, -3)] * 3))
        self.assertEqual(result['leader'], 'A')
        self.assertLess(result['daily'][-1]['strengths']['A'], 0)

    def test_changing_coverage_rejected(self):
        rows = observations([(3, 2, 0), (1, 2, 0)])
        del rows[-1]['strengths']['A']
        with self.assertRaises(ValueError): analyze_rotation(rows)

    def test_duplicate_and_unsorted_dates_rejected(self):
        rows = observations([(3, 2, 0), (1, 2, 0)])
        with self.assertRaises(ValueError): analyze_rotation(list(reversed(rows)))
        rows[-1]['date'] = rows[0]['date']
        with self.assertRaises(ValueError): analyze_rotation(rows)

    def test_repeated_read_does_not_duplicate_events_or_streak(self):
        rows = observations([(3, 2, 0), (1, 2, 0), (1, 3, 0)])
        before = deepcopy(rows)
        self.assertEqual(analyze_rotation(rows), analyze_rotation(rows))
        self.assertEqual(rows, before)


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.days = trading_days(100)
        self.sectors = {'A': {'a': '가'}, 'B': {'b': '나'}}
        self.histories = {code: [{'date': day, 'close': 100 * (1 + rate) ** i}
                                for i, day in enumerate(self.days)]
                          for code, rate in [('KOSPI', .001), ('a', .002), ('b', .003)]}
        self.cutoff = self.days[-1]

    def build(self):
        return build_rotation(self.histories, self.cutoff, self.sectors)

    def test_rolls_61_evaluations_from_81_bars(self):
        result = self.build()
        self.assertTrue(result['valid'])
        self.assertEqual(len(result['daily']), 61)
        self.assertEqual(len(result['series']), 122)
        self.assertEqual(result['daily'][0]['date'], self.days[-61])

    def test_latest_strength_matches_existing_chart(self):
        result = self.build()
        overview = build_snapshot(self.histories, self.cutoff, self.sectors)
        for row in overview['rows']:
            self.assertAlmostEqual(result['daily'][-1]['strengths'][row['sector']], row['excess20'], places=8)

    def test_missing_member_suspends_all_rotation_not_just_one_sector(self):
        self.histories['b'].pop()
        result = self.build()
        self.assertFalse(result['valid'])
        self.assertEqual(result['events'], [])
        self.assertEqual(result['series'], [])

    def test_missing_older_bar_suspends_rotation_but_preserves_overview(self):
        self.histories['a'].pop(-75)
        self.assertFalse(self.build()['valid'])
        self.assertEqual(len(build_snapshot(self.histories, self.cutoff, self.sectors)['rows']), 2)

    def test_not_enough_history(self):
        self.histories['KOSPI'] = self.histories['KOSPI'][-80:]
        self.assertFalse(self.build()['valid'])

    def test_stale_reference(self):
        self.cutoff = (date.fromisoformat(self.cutoff) + timedelta(days=15)).isoformat()
        self.assertFalse(self.build()['valid'])

    def test_holiday_does_not_change_streak(self):
        before = self.build()
        self.cutoff = (date.fromisoformat(self.cutoff) + timedelta(days=3)).isoformat()
        self.assertEqual(self.build(), before)

    def test_future_bar_is_ignored(self):
        before = self.build()
        for rows in self.histories.values():
            rows.append({'date': '2099-01-01', 'close': 10000})
        self.assertEqual(self.build(), before)

    def test_invalid_price_and_corporate_action(self):
        for value in (0, float('nan'), self.histories['a'][-2]['close'] * .5):
            self.histories['a'][-1]['close'] = value
            self.assertFalse(self.build()['valid'])

    def test_no_fake_historical_detection_timestamps(self):
        result = self.build()
        self.assertTrue(all('received_at' not in event and 'detected_at' not in event for event in result['events']))

    def test_no_app_or_watchlist_side_effects(self):
        root = Path(__file__).parent
        source = (root / 'sector_rotation.py').read_text(encoding='utf-8')
        ast.parse(source)
        for forbidden in ['streamlit', 'st.secrets', 'write_text', 'requests', 'kakao', 'rise_watch']:
            self.assertNotIn(forbidden, source)


if __name__ == '__main__':
    unittest.main()
