"""Top-20 display tests; no credentials, quotes or saved records are changed."""
import ast
import copy
from datetime import datetime
from pathlib import Path
import unittest

import test_price_refresh_brightness as brightness

ROOT = Path(__file__).parent


class WatchlistTop20Tests(unittest.TestCase):
    def setUp(self):
        tree = ast.parse((ROOT / "rise_timing_watchlist_ui.py").read_text(encoding="utf-8"))
        limit = next(n.value.value for n in tree.body if isinstance(n, ast.Assign)
                     and any(isinstance(t, ast.Name) and t.id == "WATCHLIST_DISPLAY_LIMIT" for t in n.targets))
        self.assertEqual(limit, 20)
        self.history = {}
        self.ns = {"WATCHLIST_DISPLAY_LIMIT": limit, "datetime": datetime,
                   "_buy_decision_history": lambda: self.history}
        for name in ("_mandatory_condition_count", "_stage_priority", "_buy1_distance",
                     "_decision_action", "_watchlist_condition_snapshot", "_watchlist_tiebreak_key",
                     "_watchlist_priority_key", "_select_watchlist_results"):
            brightness.function_from_file("rise_timing_watchlist_ui.py", name, self.ns)
        self.select = self.ns["_select_watchlist_results"]

    def records(self, count):
        return [dict(brightness.PriceRefreshTests.item(f"{i:06d}", 10000), score=i)
                for i in range(1, count + 1)]

    def test_178_records_show_exactly_twenty_without_deleting_input(self):
        rows = self.records(178)
        original = copy.deepcopy(rows)
        result = self.select(rows)
        self.assertEqual(len(result), 20)
        self.assertEqual(rows, original)
        result[0]["name"] = "display copy"
        self.assertEqual(rows, original)

    def test_sort_all_candidates_before_truncating(self):
        rows = self.records(178)
        result = self.select(rows)
        self.assertEqual([x["score"] for x in result], list(range(178, 158, -1)))
        self.assertEqual([x["ticker"] for x in self.select(list(reversed(rows)))],
                         [x["ticker"] for x in result])

    def test_existing_mandatory_conditions_still_take_priority_over_score(self):
        weak = self.records(25)
        strongest = dict(self.records(1)[0], ticker="999999", breakout=9990,
                         stop=9500, score=1)
        self.assertEqual(self.select(weak + [strongest])[0]["ticker"], "999999")

    def test_zero_fewer_and_exactly_twenty_are_supported(self):
        for count in (0, 1, 7, 19, 20):
            with self.subTest(count=count):
                self.assertEqual(len(self.select(self.records(count))), count)

    def test_live_quotes_are_requested_only_for_the_selected_twenty(self):
        harness = brightness.PriceRefreshTests()
        harness.setUp()
        queried = []
        def quote(ticker, market, slot):
            queried.append(ticker)
            return 11000
        harness.ns["get_live_price"] = quote
        rows = self.records(178)
        harness.render(rows)
        displayed = harness.frame()
        self.assertEqual(len(queried), 20)
        self.assertEqual(set(queried), set(displayed["코드"]))
        self.assertEqual(displayed["관찰 우선순위"].tolist(), list(range(1, 21)))

    def test_table_and_detail_share_the_same_display_subset(self):
        tree = ast.parse((ROOT / "rise_timing_watchlist_ui.py").read_text(encoding="utf-8"))
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name == "render_rise_timing_watchlist")
        calls = [n for n in ast.walk(node) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
        for name in ("_render_live_watchlist", "_render_watchlist_detail"):
            call = next(c for c in calls if c.func.id == name)
            self.assertEqual(ast.unparse(call.args[0]), "display_results")
        source = ast.unparse(node)
        self.assertIn("display_results = _select_watchlist_results(results, _load_background_state())", source)
        selector_source = ast.unparse(next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                                          and n.name == "_select_watchlist_results"))
        self.assertNotIn("_save_watchlist", selector_source)


if __name__ == "__main__":
    unittest.main()
