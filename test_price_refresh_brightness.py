"""Offline regression checks; never read credentials or request real quotes."""
import ast
import copy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import pandas as pd

ROOT = Path(__file__).parent


def function_from_file(filename, name, namespace):
    tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    node.decorator_list = []
    module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    exec(compile(module, filename, "exec"), namespace)
    return namespace[name]


class PriceRefreshTests(unittest.TestCase):
    def test_theme_removes_only_streamlit_stale_fade(self):
        st = SimpleNamespace(markdown=Mock())
        function_from_file("theme_styles.py", "inject_theme", {"st": st})()
        css = st.markdown.call_args.args[0]
        selector = '.stApp [data-testid="stElementContainer"][data-stale="true"]'
        rule = css.split(selector, 1)[1].split("}", 1)[0]
        self.assertIn("opacity: 1 !important", rule)
        self.assertIn("transition: none !important", rule)
        for unsafe in ("display:", "visibility:", "pointer-events:", "z-index:"):
            self.assertNotIn(unsafe, rule)
        self.assertTrue(st.markdown.call_args.kwargs["unsafe_allow_html"])

    def test_refresh_remains_a_ten_second_fragment(self):
        tree = ast.parse((ROOT / "rise_timing_watchlist_ui.py").read_text(encoding="utf-8"))
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_render_live_watchlist")
        fragment = node.decorator_list[0]
        self.assertEqual(ast.unparse(fragment.func), "st.fragment")
        self.assertEqual(next(k.value.value for k in fragment.keywords if k.arg == "run_every"), "10s")
        self.assertNotIn("st.rerun", ast.unparse(node))

    def setUp(self):
        self.st = SimpleNamespace(
            info=Mock(), caption=Mock(), warning=Mock(), dataframe=Mock(),
            column_config=SimpleNamespace(NumberColumn=lambda **kw: kw),
        )
        self.quote = {"000001": 10500.0, "000002": 20500.0}
        self.items = [self.item("000002", 20000), self.item("000001", 10000)]
        self.ns = {
            "st": self.st, "pd": pd, "ThreadPoolExecutor": ThreadPoolExecutor,
            "_mandatory_condition_count": lambda x: 4,
            "_stage_priority": lambda x: 0,
            "_buy1_distance": lambda x: x["price"],
            "get_live_price": lambda ticker, market, slot: self.quote[ticker],
            "_load_background_state": lambda: {},
            "_load_convergence_state": lambda: {},
            "_decision_action": lambda item, rank: ("관찰", "기존 조건", "필수 4/4", "보조 2/3"),
            "convergence_columns": lambda state, ticker: {"세 선 수렴": "수렴 관찰"},
            "_won": lambda value: f"{value:,.0f}원",
            "price_source_label": lambda: "한국투자증권 KIS 실시간 시세",
        }
        self.render = function_from_file("rise_timing_watchlist_ui.py", "_render_live_watchlist", self.ns)

    @staticmethod
    def item(ticker, price):
        return dict(ticker=ticker, name=ticker, market="KOSPI", price=price,
                    buy1=price, buy2=price * .99, stop=price * .9,
                    breakout=price * 1.01, volume_ratio=2, score=85,
                    gap20=1, action="관찰", label="상승초입")

    def frame(self):
        return self.st.dataframe.call_args.args[0].copy()

    def test_only_price_column_changes_when_quotes_change(self):
        original = copy.deepcopy(self.items)
        self.render(self.items)
        before = self.frame()
        self.quote.update({"000001": 10700, "000002": 20300})
        self.render(self.items)
        after = self.frame()
        self.assertFalse(before["실시간 현재가"].equals(after["실시간 현재가"]))
        pd.testing.assert_frame_equal(before.drop(columns="실시간 현재가"), after.drop(columns="실시간 현재가"))
        self.assertEqual(self.items, original)

    def test_dataframe_identity_stays_fixed_across_price_updates(self):
        for quote in (10500, 10600):
            self.quote["000001"] = quote
            self.render(self.items)
            self.assertEqual(self.st.dataframe.call_args.kwargs["key"], "rise_live_watchlist")

    def test_quote_failure_preserves_existing_fallback_behavior(self):
        self.quote["000001"] = None
        self.render(self.items)
        rows = self.frame().set_index("코드")
        self.assertEqual(rows.loc["000001", "실시간 현재가"], "10,000원")

    def test_price_timestamp_and_data_warning_remain_visible(self):
        self.ns["price_source_label"] = lambda: "시세 없음"
        self.render(self.items)
        captions = [c.args[0] for c in self.st.caption.call_args_list]
        self.assertTrue(any("현재가 조회" in s and "10초 갱신" in s for s in captions))
        self.assertTrue(any("연결에 실패" in c.args[0] for c in self.st.warning.call_args_list))


if __name__ == "__main__":
    unittest.main()
