"""Regression tests for HY simplified homepage navigation.
No market access, no orders and no Streamlit session mutation required.
"""
import ast
from pathlib import Path
import unittest

from main_navigation import NAV_GROUPS, DEFAULT_GROUP, GROUP_KEYS, selection_from_legacy, current_subview


class MainNavigationTests(unittest.TestCase):
    def test_only_four_primary_groups(self):
        self.assertEqual(list(NAV_GROUPS), [
            "🌐 시장 분석", "🏆 상승 후보", "📍 매수 시점", "📈 성과 검증"])
        self.assertEqual(len(NAV_GROUPS), 4)
        self.assertEqual({v for items in NAV_GROUPS.values() for v in items}, {
            "🔎 전체시장 분석", "🌐 시장환경", "🏆 TOP12", "🚀 부의 점프",
            "🔥 현재 5개월선 돌파", "📍 상승시점 관찰", "📈 과거 성과 검증"})

    def test_each_existing_view_preserved_in_correct_group(self):
        for group, items in NAV_GROUPS.items():
            for child in items:
                self.assertEqual(selection_from_legacy(child), (group,child))
                self.assertEqual(current_subview(group,child),child)

    def test_old_start_and_invalid_states_are_safe(self):
        self.assertEqual(selection_from_legacy("🏠 시작"), (DEFAULT_GROUP,"🔎 전체시장 분석"))
        self.assertEqual(selection_from_legacy(None), (DEFAULT_GROUP,"🔎 전체시장 분석"))
        self.assertEqual(current_subview("unknown","🏠 시작"),"🔎 전체시장 분석")
        self.assertEqual(current_subview("🏆 상승 후보","missing"),"🏆 TOP12")
        self.assertEqual(set(GROUP_KEYS),set(g for g,items in NAV_GROUPS.items() if len(items)>1))

    def test_homepage_does_not_delete_existing_renderers_or_update_controls(self):
        source=Path("app.py").read_text(encoding="utf-8")
        ast.parse(source)
        for call in [
            "render_market_environment(", "run_market_analysis(",
            "render_wealth_jump_tab(", "render_monthly_ma5_tab(",
            "render_rise_timing_watchlist(", "render_individual_stock_ma5_backtest(",
            'key="hy_fast_update"', 'key="hy_full_update"']:
            self.assertIn(call,source,call)
        self.assertIn("selection_from_legacy(legacy_view)",source)
        self.assertNotIn('if selected_view == "🏠 시작"',source)
        self.assertNotIn('st.info("① 시장 데이터 확인',source)
        self.assertNotIn('역할 분리: TOP12=개별주식 선별',source)

if __name__=="__main__":
    unittest.main()
