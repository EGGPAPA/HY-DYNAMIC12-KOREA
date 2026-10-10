from pathlib import Path
import ast
import unittest

ROOT = Path(__file__).resolve().parents[2]


class SeparationTests(unittest.TestCase):
    def test_original_screen_has_no_project_routing(self):
        source = (ROOT / 'rise_timing_watchlist_ui.py').read_text(encoding='utf-8')
        ast.parse(source)
        self.assertNotIn('rise_investment_project', source)
        self.assertNotIn('render_strategy_project', source)
        self.assertIn('render_leader_comparison', source)
        self.assertIn('_load_watch_cohort()', source)

    def test_separate_entry_does_not_start_on_import(self):
        source = (Path(__file__).parent / 'app.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        self.assertTrue(any(isinstance(node, ast.FunctionDef) and node.name == 'main' for node in tree.body))
        self.assertIn("if __name__ == '__main__':", source)

    def test_ledger_location_is_preserved(self):
        from projects.leader_investment.strategy_projects import PROJECT_PATH
        from projects.leader_investment.strategy_project_store import BRANCH
        self.assertEqual(PROJECT_PATH, 'data/strategy_projects.json')
        self.assertEqual(BRANCH, 'monitor-state')


if __name__ == '__main__':
    unittest.main()
