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

    def test_one_table_union_and_group_filter_keep_a_visible(self):
        app = self.app()
        self.assertEqual(len(app.dataframe), 1)
        frame = app.dataframe[0].value
        self.assertEqual(set(frame['그룹']), {'A', 'B'})
        self.assertEqual(set(frame['코드']), {'000001', '000002'})
        self.assertIn('조건 약화', set(frame['현재 분류(확정 일봉)']))
        app.radio(key='rise_watch_group_view').set_value('A 상승 초기').run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.dataframe), 1)
        self.assertEqual(list(app.dataframe[0].value['그룹']), ['A'])
        app.radio(key='rise_watch_group_view').set_value('B 기존 주도주').run()
        self.assertEqual(list(app.dataframe[0].value['그룹']), ['B'])
        app.radio(key='rise_watch_group_view').set_value('통합 A+B').run()
        self.assertEqual(len(app.dataframe[0].value), 2)

    def test_combined_forty_is_not_silently_truncated_to_twenty(self):
        app = self.app()
        app.checkbox(key='forty').check().run()
        self.assertEqual(len(app.exception), 0)
        frame = app.dataframe[0].value
        self.assertEqual(len(frame), 40)
        self.assertEqual(frame['코드'].nunique(), 40)
        self.assertEqual(frame['그룹'].value_counts().to_dict(), {'A': 20, 'B': 20})


if __name__ == '__main__':
    unittest.main()
