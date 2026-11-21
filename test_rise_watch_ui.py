"""Rendered UI integration checks with offline KIS feeds and fixed dates."""
from pathlib import Path
import unittest
from streamlit.testing.v1 import AppTest


class WatchScreenTests(unittest.TestCase):
    def app(self):
        path = Path(__file__).parent / 'tests/fixtures/rise_watch_app.py'
        app = AppTest.from_file(str(path), default_timeout=20).run()
        self.assertEqual(len(app.exception), 0)
        return app

    @staticmethod
    def _evaluated_frame(app):
        """There are now two independent tables: saved names and KIS evaluations."""
        return next(item.value for item in app.dataframe
                    if '7조건 충족' in item.value.columns)

    def test_one_table_union_and_group_filter_keep_a_visible(self):
        app = self.app()
        self.assertEqual(len(app.dataframe), 2)
        self.assertIn('종목명', app.dataframe[0].value.columns)
        frame = self._evaluated_frame(app)
        self.assertIn('7조건 충족', frame.columns)
        self.assertIn('5-of-7 판정', frame.columns)
        self.assertTrue(frame['7조건 충족'].str.contains('/7').all())
        self.assertEqual(set(frame['그룹']), {'A', 'B'})
        self.assertEqual(set(frame['코드']), {'000001', '000002'})
        self.assertIn('조건 약화', set(frame['현재 분류(확정 일봉)']))
        app.radio(key='rise_watch_group_view').set_value('A 상승 초기').run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(list(self._evaluated_frame(app)['그룹']), ['A'])
        app.radio(key='rise_watch_group_view').set_value('B 기존 주도주').run()
        self.assertEqual(list(self._evaluated_frame(app)['그룹']), ['B'])
        app.radio(key='rise_watch_group_view').set_value('통합 A+B').run()
        self.assertEqual(len(self._evaluated_frame(app)), 2)

    def test_combined_forty_is_not_silently_truncated_to_twenty(self):
        app = self.app()
        app.checkbox(key='forty').check().run()
        self.assertEqual(len(app.exception), 0)
        frame = self._evaluated_frame(app)
        self.assertEqual(len(frame), 40)
        self.assertEqual(frame['코드'].nunique(), 40)
        self.assertEqual(frame['그룹'].value_counts().to_dict(), {'A': 20, 'B': 20})


    def test_seven_condition_gate_and_reference_are_not_confused(self):
        from rise_current_price_ui import _five_of_seven_summary
        mandatory = {'gap': True, 'volume': True, 'close': True, 'risk': True}
        aux = {'stage': True, 'score': False, 'persistence': False}
        intraday = {'valid': True, 'basis': 'quote', 'intraday': True,
                    'label': '🟢 상승초입', 'checks': {**mandatory, **aux}}
        count, verdict = _five_of_seven_summary(intraday)
        self.assertIn('5/7', count)
        self.assertIn('필수 4/4', count)
        self.assertIn('장중 잠정', verdict)
        reference = dict(intraday, basis='close', intraday=False)
        count, verdict = _five_of_seven_summary(reference)
        self.assertIn('5/7', count)
        self.assertIn('종가 참고', verdict)
        self.assertNotIn('장중 잠정', verdict)
        not_mandatory = dict(intraday, checks={
            'gap': False, 'volume': True, 'close': True, 'risk': True,
            'stage': True, 'score': True, 'persistence': True})
        count, verdict = _five_of_seven_summary(not_mandatory)
        self.assertIn('6/7', count)
        self.assertIn('조건 미충족', verdict)
        self.assertEqual(_five_of_seven_summary({'valid': False}), ('평가 보류', '평가 보류'))
        self.assertEqual(_five_of_seven_summary({'valid': True, 'checks': {}}),
                         ('평가 보류', '평가 보류'))

    def test_existing_watchlist_is_default_in_current_page(self):
        from pathlib import Path
        source = (Path(__file__).parent / 'rise_timing_watchlist_ui.py').read_text()
        before = source.index("['📊 기존 5-of-7 · 개별종목'")
        after = source.index("'① 단기 · 며칠~몇 주'", before)
        self.assertLess(before, after)
        self.assertIn("key='rise_investment_project_v2'", source)
        self.assertIn("render_current_price_screen(universe, selected_rows", source)
        live_source = (Path(__file__).parent / 'rise_current_price_ui.py').read_text()
        self.assertIn("key='rise_saved_members_immediate'", live_source)
        self.assertLess(live_source.index('roster = saved_member_frame(watchlist)'),
                        live_source.index('if universe is None or universe.empty:'))


    def test_saved_individual_roster_is_available_without_kis(self):
        from rise_current_price_ui import saved_member_frame
        rows = [
            {'ticker':f'{i:06d}','name':f'종목{i}','market':'KOSPI' if i%2 else 'KOSDAQ',
             'watch_entry':{'entry_asof':'2026-10-08'}}
            for i in range(1,21)
        ]
        frame = saved_member_frame(rows)
        self.assertEqual(len(frame),20)
        self.assertEqual(frame.iloc[0]['종목명'],'종목1')
        self.assertEqual(frame.iloc[-1]['종목코드'],'000020')
        self.assertTrue((frame['최초 선정일']=='2026-10-08').all())
        self.assertNotIn('현재가',frame.columns)
        self.assertNotIn('5-of-7 판정',frame.columns)
        self.assertEqual(len(saved_member_frame(rows+[rows[0]])),20)


if __name__ == '__main__':
    unittest.main()
