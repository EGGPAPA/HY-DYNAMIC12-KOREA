from pathlib import Path
import unittest

from streamlit.testing.v1 import AppTest

FIXTURE = Path(__file__).parent / 'fixtures' / 'strategy_app.py'


class RoadmapUITests(unittest.TestCase):
    def app(self):
        at = AppTest.from_file(str(FIXTURE), default_timeout=20).run()
        self.assertEqual(len(at.exception), 0)
        return at

    def test_three_stages_and_named_columns(self):
        at = self.app()
        self.assertEqual([x.value for x in at.metric[:3]], ['1종목', '1종목', '1종목'])
        columns = at.dataframe[0].value.columns.tolist()
        self.assertIn('① 업종 > 시장', columns)
        self.assertIn('② 종목 > 업종', columns)
        self.assertIn('③ 진입 위치', columns)
        self.assertIn('종합 판단', columns)
        self.assertEqual(at.session_state['fixture_revision'], 0)

    def test_medium_shows_closed_price_and_manual_review_wait(self):
        at = self.app()
        at.radio(key='fixture_project').set_value('② 중기 · 1~3개월').run()
        self.assertEqual(len(at.exception), 0)
        frame = at.dataframe[0].value
        self.assertIn('중기 평가종가', frame.columns)
        self.assertIn('실적·공시', frame.iloc[0]['③ 진입 위치'])
        self.assertEqual(at.metric[2].value, '0종목')
        self.assertEqual(at.session_state['fixture_revision'], 0)

    def test_missing_sector_never_looks_like_pass(self):
        at = self.app()
        at.session_state['fixture_sector_missing'] = True
        at.run()
        self.assertEqual(len(at.exception), 0)
        self.assertEqual(at.metric[2].value, '0종목')
        self.assertIn('비교 보류', at.dataframe[0].value.iloc[0]['종합 판단'])

    def test_mismatched_dates_hold_without_losing_technical_columns(self):
        at = self.app()
        at.session_state['fixture_sector_stale'] = True
        at.run()
        self.assertEqual(len(at.exception), 0)
        self.assertIn('기준일 불일치', ' '.join(x.value for x in at.markdown))
        self.assertEqual(at.metric[2].value, '0종목')
        self.assertIn('조건 충족', at.dataframe[0].value.iloc[0]['③ 진입 위치'])

    def test_rerun_and_detail_selection_do_not_write(self):
        at = self.app()
        at.selectbox(key='roadmap_detail_short').set_value('가상 종목 (000001)').run()
        at.run()
        self.assertEqual(len(at.exception), 0)
        self.assertEqual(at.session_state['fixture_revision'], 0)
        self.assertEqual(at.session_state['fixture_state']['projects']['short']['watch'], [])


if __name__ == '__main__': unittest.main()
