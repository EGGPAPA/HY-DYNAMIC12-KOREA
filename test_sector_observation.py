import ast
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import unittest

from sector_observation import KST, SECTORS, build_snapshot, closed_cutoff, parse_bars


def fixture():
    days = []
    day = date(2026, 7, 1)
    while len(days) < 71:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    def bars(rate):
        return [{'date': day, 'close': 100 * (1 + rate) ** i} for i, day in enumerate(days)]
    return {'KOSPI': bars(.001), 'a': bars(.002), 'b': bars(.004), 'c': bars(-.001)}, days


class ClosedSectorTests(unittest.TestCase):
    def setUp(self):
        self.data, self.days = fixture()
        self.sectors = {'강한 업종': {'a': '가', 'b': '나'}, '약한 업종': {'c': '다'}}
        self.cutoff = self.days[-1]

    def build(self):
        return build_snapshot(self.data, self.cutoff, self.sectors)

    def test_before_close_excludes_today(self):
        self.assertEqual(closed_cutoff(datetime(2026, 10, 8, 15, 59, tzinfo=KST)), '2026-10-07')

    def test_after_close_allows_today(self):
        self.assertEqual(closed_cutoff(datetime(2026, 10, 8, 16, tzinfo=KST)), '2026-10-08')

    def test_timezone_conversion(self):
        self.assertEqual(closed_cutoff(datetime(2026, 10, 8, 7, tzinfo=timezone.utc)), '2026-10-08')

    def test_naive_means_korean_time(self):
        self.assertEqual(closed_cutoff(datetime(2026, 10, 9, 9)), '2026-10-08')

    def test_holiday_uses_last_actual_bar(self):
        self.cutoff = (date.fromisoformat(self.cutoff) + timedelta(days=3)).isoformat()
        result = self.build()
        self.assertEqual(result['asof'], self.days[-1])
        self.assertEqual(len([x for x in result['trend'] if x['sector'] == 'KOSPI']), 61)

    def test_stale_benchmark_refused(self):
        self.cutoff = (date.fromisoformat(self.cutoff) + timedelta(days=15)).isoformat()
        with self.assertRaises(ValueError): self.build()

    def test_future_bars_not_used(self):
        original = self.build()
        for bars in self.data.values():
            bars.append({'date': '2099-01-01', 'close': 999999})
        self.assertEqual(original, self.build())

    def test_insufficient_benchmark_refused(self):
        self.data['KOSPI'] = self.data['KOSPI'][-60:]
        with self.assertRaises(ValueError): self.build()

    def test_equal_weight_compounding_matches_bars_and_line(self):
        result = self.build()
        row = result['rows'][0]
        self.assertAlmostEqual(row['r20'], ((1.003) ** 20 - 1) * 100)
        self.assertAlmostEqual(row['r60'], ((1.003) ** 60 - 1) * 100)
        line = [x['index'] for x in result['trend'] if x['sector'] == '강한 업종']
        self.assertEqual(line[0], 100)
        self.assertAlmostEqual(line[-1] / line[-21] * 100 - 100, row['r20'])
        self.assertAlmostEqual(row['excess20'], row['r20'] - result['benchmark20'])

    def test_sort_and_member_sort(self):
        result = self.build()
        self.assertEqual([x['sector'] for x in result['rows']], ['강한 업종', '약한 업종'])
        self.assertEqual(result['rows'][0]['stocks'][0]['ticker'], 'b')

    def test_missing_one_member_excludes_whole_sector(self):
        self.data['a'].pop()
        result = self.build()
        self.assertEqual(len(result['rows']), 1)
        self.assertEqual(result['excluded'][0]['sector'], '강한 업종')

    def test_missing_middle_date_not_forward_filled(self):
        self.data['a'].pop(-20)
        self.assertEqual(len(self.build()['rows']), 1)

    def test_duplicate_refused(self):
        self.data['a'].append(deepcopy(self.data['a'][-1]))
        self.assertEqual(len(self.build()['rows']), 1)

    def test_invalid_prices_refused(self):
        for price in (0, -5, float('nan'), float('inf')):
            with self.subTest(price=price):
                self.data['a'][-1]['close'] = price
                self.assertEqual(len(self.build()['rows']), 1)

    def test_abnormal_corporate_action_excludes_sector(self):
        self.data['a'][-1]['close'] *= .5
        self.assertIn('권리변동', self.build()['excluded'][0]['reason'])

    def test_no_valid_sectors_refused(self):
        self.data = {'KOSPI': self.data['KOSPI']}
        with self.assertRaises(ValueError): self.build()

    def test_inputs_not_modified(self):
        original = deepcopy(self.data)
        self.build()
        self.assertEqual(self.data, original)

    def test_fixed_coverage(self):
        self.assertEqual(len(SECTORS), 10)
        self.assertTrue(all(len(x) == 3 for x in SECTORS.values()))

    def test_xml_feed(self):
        raw = '<?xml version="1.0" encoding="euc-kr"?><chart><item data="20261008|1|2|1|1234|100" /></chart>'
        self.assertEqual(parse_bars(raw.encode('euc-kr')), [{'date': '2026-10-08', 'close': 1234.0}])

    def test_observation_only_integration(self):
        app = Path(__file__).with_name('app.py').read_text(encoding='utf-8-sig')
        tree = ast.parse(app)
        parent = next(n for n in ast.walk(tree) if isinstance(n, ast.If)
                      and 'selected_view ==' in ast.unparse(n.test) and '상승시점 관찰' in ast.unparse(n.test))
        calls = [n.value.func.id for n in parent.body if isinstance(n, ast.Expr)
                 and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Name)]
        self.assertLess(calls.index('render_sector_observation'), calls.index('render_rise_timing_watchlist'))

    def test_sector_cannot_mutate_watchlist_or_send_alerts(self):
        ui = Path(__file__).with_name('sector_observation_ui.py').read_text(encoding='utf-8')
        self.assertNotIn('rise_watch', ui)
        self.assertNotIn('kakao', ui.lower())
        self.assertNotIn('st.secrets', ui)
        self.assertIn("run_every='1h'", ui)
        self.assertNotIn("run_every='10s'", ui)


if __name__ == '__main__':
    unittest.main()
