from pathlib import Path
from copy import deepcopy
import unittest

from streamlit.testing.v1 import AppTest
from leader_roadmap_ui import stage_members

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

    def test_counts_and_names_use_the_same_cumulative_members(self):
        rows = [
            dict(ticker='held', sector_ok=None, stock_ok=None, ready=False),
            dict(ticker='stock_only', sector_ok=False, stock_ok=True, ready=False),
            dict(ticker='sector_only', sector_ok=True, stock_ok=False, ready=False),
            dict(ticker='both', sector_ok=True, stock_ok=True, ready=False),
            dict(ticker='ready', sector_ok=True, stock_ok=True, ready=True),
        ]
        before = deepcopy(rows)
        self.assertEqual([[x['ticker'] for x in group] for group in stage_members(rows)],
                         [['sector_only', 'both', 'ready'], ['both', 'ready'], ['ready']])
        self.assertEqual(rows, before)
        at = self.app()
        self.assertEqual(sum('반도체 | 가상 종목' == x.value for x in at.markdown), 3)
        self.assertIn('①과 ②는 같은 1종목', ' '.join(x.value for x in at.caption))

    def test_stage_filters_keep_matching_table_and_detail_without_saving(self):
        at = self.app()
        for choice in ('① 업종 우위', '② 업종·종목 모두 우위', '③ 진입 조건까지 충족', '전체'):
            at.radio(key='roadmap_filter_short').set_value(choice).run()
            self.assertEqual(len(at.exception), 0)
            self.assertEqual(at.dataframe[0].value['종목'].tolist(), ['가상 종목'])
            self.assertEqual(at.selectbox(key='roadmap_detail_short').value, '가상 종목 (000001)')
            self.assertEqual(at.session_state['fixture_revision'], 0)
        self.assertEqual(at.session_state['fixture_state']['projects']['short']['watch'], [])

    def test_zero_match_filter_does_not_fall_back_to_all_rows(self):
        at = self.app()
        at.radio(key='fixture_project').set_value('② 중기 · 1~3개월').run()
        at.radio(key='roadmap_filter_medium').set_value('③ 진입 조건까지 충족').run()
        self.assertEqual(len(at.exception), 0)
        self.assertTrue(any('선택한 단계에 해당하는 종목이 없습니다' in x.value for x in at.info))
        self.assertFalse(any('① 업종 > 시장' in x.value.columns for x in at.dataframe))
        self.assertFalse(any(x.key == 'roadmap_detail_medium' for x in at.selectbox))
        self.assertEqual(at.session_state['fixture_revision'], 0)
        at.radio(key='roadmap_filter_medium').set_value('전체').run()
        self.assertEqual(at.dataframe[0].value['종목'].tolist(), ['가상 종목'])

    def test_filter_is_independent_between_projects_and_survives_rerun(self):
        at = self.app()
        at.radio(key='roadmap_filter_short').set_value('② 업종·종목 모두 우위').run()
        at.run()
        self.assertEqual(at.radio(key='roadmap_filter_short').value, '② 업종·종목 모두 우위')
        at.radio(key='fixture_project').set_value('② 중기 · 1~3개월').run()
        self.assertEqual(at.radio(key='roadmap_filter_medium').value, '전체')
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
        self.assertFalse(any('① 업종 > 시장' in x.value.columns for x in at.dataframe))
        self.assertTrue(any('주도업종 자료 확인' in x.value for x in at.warning))
        self.assertEqual(at.session_state['fixture_revision'], 0)

    def test_mismatched_dates_hold_without_losing_technical_columns(self):
        at = self.app()
        at.session_state['fixture_sector_stale'] = True
        at.run()
        self.assertEqual(len(at.exception), 0)
        self.assertTrue(any('순위 확인 전' in x.value for x in at.warning))
        self.assertFalse(any('① 업종 > 시장' in x.value.columns for x in at.dataframe))
        self.assertEqual(at.session_state['fixture_revision'], 0)

    def test_rerun_and_detail_selection_do_not_write(self):
        at = self.app()
        at.selectbox(key='roadmap_detail_short').set_value('가상 종목 (000001)').run()
        at.run()
        self.assertEqual(len(at.exception), 0)
        self.assertEqual(at.session_state['fixture_revision'], 0)
        self.assertEqual(at.session_state['fixture_state']['projects']['short']['watch'], [])

    def test_phase_chart_and_original_ratio_chart_are_present(self):
        at = self.app()
        self.assertTrue(any('강세 전환·다음 상승 준비 시점' in x.value for x in at.markdown))
        self.assertTrue(any('현재 관찰 단계:' in x.value for x in at.markdown))
        self.assertTrue(any(x.label == '시점 차트 기준·한계' for x in at.expander))
        self.assertTrue(any(x.label == '기존 상대강도 비율 차트 · 시작값 100' for x in at.expander))
        self.assertEqual(at.session_state['fixture_revision'], 0)

    def test_unverified_sector_does_not_create_a_phase_chart(self):
        at = self.app()
        at.session_state['fixture_sector_missing'] = True
        at.run()
        self.assertEqual(len(at.exception), 0)
        self.assertTrue(any('주도업종 후보가 없습니다' in x.value for x in at.info))
        self.assertFalse(any('현재 관찰 단계:' in x.value for x in at.markdown))

    def test_default_focus_replaces_display_scope_not_saved_watch(self):
        at = self.app()
        next(x for x in at.button if x.label == '현재 후보 최대 20개로 관찰 시작').click().run()
        original = deepcopy(at.session_state['fixture_state'])
        at.session_state['fixture_focus_leader'] = '2차전지'
        at.run()
        self.assertEqual(len(at.exception), 0)
        self.assertEqual(at.radio(key='strategy_scope_short').value, '주도업종 후보')
        self.assertEqual(at.dataframe[0].value['종목'].tolist(), ['가상 2차전지'])
        self.assertEqual(at.session_state['fixture_state'], original)
        self.assertTrue(any(x.value == '2차전지 | 가상 2차전지' for x in at.markdown))
        self.assertFalse(any(x.value == '반도체 | 가상 종목' for x in at.markdown))
        at.radio(key='strategy_scope_short').set_value('기존 저장 관찰종목').run()
        self.assertEqual(at.dataframe[0].value['종목'].tolist(), ['가상 종목'])
        self.assertEqual(at.session_state['fixture_state'], original)

    def test_failed_focus_fetch_has_no_legacy_fallback(self):
        at = self.app()
        next(x for x in at.button if x.label == '현재 후보 최대 20개로 관찰 시작').click().run()
        original = deepcopy(at.session_state['fixture_state'])
        at.session_state['fixture_focus_fetch_fail'] = True
        at.run()
        self.assertEqual(len(at.exception), 0)
        self.assertFalse(any('① 업종 > 시장' in x.value.columns for x in at.dataframe))
        self.assertTrue(any('다른 업종으로 대체하지 않습니다' in x.value for x in at.warning))
        self.assertEqual(at.session_state['fixture_state'], original)

    def test_focus_is_same_sector_for_both_projects(self):
        at = self.app()
        at.session_state['fixture_focus_leader'] = '2차전지'
        for project in ('① 단기 · 며칠~몇 주', '② 중기 · 1~3개월'):
            at.radio(key='fixture_project').set_value(project).run()
            self.assertEqual(len(at.exception), 0)
            self.assertEqual(at.dataframe[0].value['비교 업종'].tolist(), ['2차전지'])
        self.assertEqual(at.session_state['fixture_revision'], 0)


if __name__ == '__main__': unittest.main()
