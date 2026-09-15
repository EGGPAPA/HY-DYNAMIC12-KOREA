from datetime import datetime, timedelta
import copy
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import urllib.error
import ast

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ma_convergence import (KST, closed_date_limit, ordinary_stock, actively_trading,
    analyze_bars, merge_candidates, convergence_columns)

spec = importlib.util.spec_from_file_location("daily_scanner", Path(__file__).resolve().parents[1] / "scripts/refresh_ma_convergence.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def bars(prices=None):
    prices = prices or [10000] * 100
    start = datetime(2026, 1, 1)
    return [{"date": (start + timedelta(days=i)).date().isoformat(), "close": x, "volume": 100000} for i, x in enumerate(prices)]


def candidate(code):
    return {"ticker": code, "name": f"name{code}", "market": "KOSPI", "date": "2026-09-14",
            "span_pct": 1.0, "mean_value20": 1000000000, "narrowing": True}


class ConvergenceTests(unittest.TestCase):
    def test_flat_lines_converge(self):
        data = bars()
        x = analyze_bars(data, data[-1]["date"])
        self.assertEqual(x["span_pct"], 0)
        self.assertEqual(x["ma60"], 10000)
        self.assertTrue(x["candidate"])

    def test_sma_formula(self):
        data = bars(list(range(10000, 10100)))
        x = analyze_bars(data, data[-1]["date"])
        self.assertEqual(x["ma5"], 10097)
        self.assertEqual(x["ma20"], 10089.5)
        self.assertEqual(x["ma60"], 10069.5)
        self.assertAlmostEqual(x["span_pct"], (10097-10069.5) / ((10097+10089.5+10069.5)/3) * 100)

    def test_threshold_boundaries(self):
        # Last five prices a, previous 95 prices b: MAs are a, (a+3b)/4,
        # and (a+11b)/12. Solve exactly for a 3% spread.
        b = 10000
        delta = 0.03 * b / (11/12 - 0.03 * 4/9)
        for factor, expected in ((0.999, True), (1.0, True), (1.001, False)):
            data = bars([b]*95 + [b+delta*factor]*5)
            self.assertEqual(analyze_bars(data, data[-1]["date"])["cluster_now"], expected)

    def test_future_partial_bar_not_used(self):
        data = bars()
        asof = data[-1]["date"]
        data.append({"date": "2027-01-01", "close": 1000000, "volume": 1})
        self.assertEqual(analyze_bars(data, asof)["close"], 10000)

    def test_zero_volume_excluded(self):
        data = bars(); data[-1]["volume"] = 0
        self.assertFalse(analyze_bars(data, data[-1]["date"])["eligible"])

    def test_stale_bar_excluded(self):
        self.assertFalse(analyze_bars(bars(), "2026-12-31")["eligible"])

    def test_short_history_excluded(self):
        data = bars()[-59:]
        self.assertFalse(analyze_bars(data, data[-1]["date"])["eligible"])

    def test_bad_data_rejected(self):
        data = bars(); data[-1]["close"] = float("nan")
        with self.assertRaises(ValueError): analyze_bars(data, data[-1]["date"])
        data = bars(); data.append(data[-1])
        with self.assertRaises(ValueError): analyze_bars(data, data[-1]["date"])

    def test_low_liquidity_not_candidate(self):
        data = bars()
        for x in data: x["volume"] = 1
        x = analyze_bars(data, data[-1]["date"])
        self.assertTrue(x["cluster_now"])
        self.assertFalse(x["candidate"])

    def test_convergence_is_not_bullish_by_itself(self):
        data = bars([10000]*99+[9800])
        x = analyze_bars(data, data[-1]["date"])
        self.assertTrue(x["cluster_now"])
        self.assertIn("하방주의", x["state"])

    def test_cutoff_kst(self):
        self.assertEqual(closed_date_limit(datetime(2026, 9, 15, 9, 30, tzinfo=KST)), "2026-09-14")
        self.assertEqual(closed_date_limit(datetime(2026, 9, 15, 20, 30, tzinfo=KST)), "2026-09-15")

    def test_universe_filter(self):
        row = {"stockEndType": "stock", "itemCode": "005930", "stockName": "삼성전자"}
        self.assertTrue(ordinary_stock(row))
        for name in ("삼성전자우", "한화3우B", "KB스팩1호", "CJ제일제당 우"):
            self.assertFalse(ordinary_stock({**row, "stockName": name}))
        self.assertFalse(ordinary_stock({**row, "stockEndType": "etf"}))
        self.assertFalse(actively_trading({}))
        self.assertTrue(actively_trading({"tradeStopType": {"name": "TRADING"}}))

    def test_merge_preserves_existing_and_deduplicates(self):
        old = [{"ticker": "000001", "name": "mine", "custom": 42}]
        before = copy.deepcopy(old)
        merged, added = merge_candidates(old, [candidate("000001"), candidate("000002"), candidate("000002")])
        self.assertEqual(old, before)
        self.assertEqual(merged[0], old[0])
        self.assertEqual(added, ["000002"])

    def test_daily_limit_and_removal_memory(self):
        candidates = [candidate(f"{i:06d}") for i in range(20)]
        _, added = merge_candidates([], candidates, ["000000"], limit=10)
        self.assertEqual(len(added), 10)
        self.assertNotIn("000000", added)

    def test_missing_ui_data_not_positive(self):
        self.assertIn("자료 대기", convergence_columns({}, "005930")["세 선 수렴"])

    def test_no_claim_of_previous_convergence(self):
        snapshot = {"items": {"005930": {"eligible": True, "cluster_now": False,
                    "span_pct": 12, "state": "⚪ 수렴 해제", "date": "2026-09-14"}}}
        self.assertEqual(convergence_columns(snapshot, "005930")["세 선 수렴"], "⚪ 비수렴")

    def test_merge_conflict_reloads_user_changes(self):
        # Simulates a user edit between read and PUT; the retry must keep it.
        store = {"main": [{"ticker": "000001", "name": "old"}], "monitor-state": {}}
        conflicted = [False]
        def read(path, branch, default=None): return copy.deepcopy(store[branch]), "sha"
        def write(path, branch, value, sha, message):
            if branch == "main" and not conflicted[0]:
                conflicted[0] = True
                store[branch].append({"ticker": "000003", "name": "user added"})
                raise urllib.error.HTTPError("https://example.invalid", 409, "conflict", {}, None)
            store[branch] = copy.deepcopy(value)
        snap = {"asof": "2026-09-14", "candidates": [candidate("000002")], "items": {}}
        with patch.object(runner, "github_read", side_effect=read), patch.object(runner, "github_write", side_effect=write), patch.object(runner.time, "sleep"):
            runner.publish(snap, {})
        self.assertEqual({x["ticker"] for x in store["main"]}, {"000001", "000002", "000003"})
        self.assertTrue(store["monitor-state"]["complete"])

    def test_pending_retry_does_not_add_next_batch(self):
        store = {"main": [{"ticker": "000001"}], "monitor-state": {}}
        def read(path, branch, default=None): return copy.deepcopy(store[branch]), "sha"
        def write(path, branch, value, sha, message): store[branch] = copy.deepcopy(value)
        snap = {"asof": "2026-09-14", "candidates": [candidate("000001"), candidate("000002")],
                "pending": {"asof": "2026-09-14", "tickers": ["000001"]}, "items": {}}
        with patch.object(runner, "github_read", side_effect=read), patch.object(runner, "github_write", side_effect=write):
            runner.publish(snap, copy.deepcopy(snap))
        self.assertEqual(len(store["main"]), 1)
        self.assertEqual(store["monitor-state"]["auto_added_ever"], ["000001"])

    def test_halted_stocks_never_fetch_prices(self):
        with patch.object(runner, "fetch_bars", side_effect=AssertionError("must not fetch")):
            result = runner.scan_one({"ticker": "000001", "tradable": False}, "2026-09-14")
        self.assertFalse(result["eligible"])

    def test_completed_date_not_scanned_twice(self):
        with patch.object(sys, "argv", ["scanner", "--publish"]), \
             patch.object(runner, "reference_date", return_value="2026-09-14"), \
             patch.object(runner, "github_read", return_value=({"asof": "2026-09-14", "complete": True}, "sha")), \
             patch.object(runner, "fetch_universe", side_effect=AssertionError("must skip")):
            runner.main()

    def test_ui_keeps_single_price_table_and_adds_ma5(self):
        source = (Path(__file__).resolve().parents[1] / "rise_timing_watchlist_ui.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        func = next(x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name == "_render_live_watchlist")
        dataframe_calls = [x for x in ast.walk(func) if isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute) and x.func.attr == "dataframe"]
        self.assertEqual(len(dataframe_calls), 1)
        self.assertIn('**convergence_columns(convergence, x["ticker"])', source)
        self.assertIn('"5일선":close.rolling(5).mean()', source)


if __name__ == "__main__":
    unittest.main()
