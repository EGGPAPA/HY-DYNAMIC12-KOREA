"""The same condition snapshot must determine order and displayed counts."""
import copy
import unittest

import test_price_refresh_brightness as brightness
import test_watchlist_top20 as top20


def monitored(ticker, mandatory, auxiliary):
    checks = {key: i < mandatory for i, key in enumerate(("gap", "volume", "close", "risk"))}
    checks.update({key: i < auxiliary for i, key in enumerate(("stage", "score", "persistence"))})
    return {"ticker": ticker, "action": "서버 관찰", "checks": checks}


class ConditionOrderTests(unittest.TestCase):
    def setUp(self):
        self.harness = top20.WatchlistTop20Tests()
        self.harness.setUp()
        self.select = self.harness.select
        self.rows = self.harness.records(30)

    def counts(self, selected):
        return [(r["_watchlist_conditions"]["mandatory_count"],
                 r["_watchlist_conditions"]["auxiliary_count"]) for r in selected]

    def test_mandatory_three_beats_two_even_with_worse_stage_or_score(self):
        rows = [dict(self.rows[0], score=100, label="상승초입"),
                dict(self.rows[1], score=1, label="준비구간")]
        background = {"items": [monitored(rows[0]["ticker"], 2, 3), monitored(rows[1]["ticker"], 3, 0)]}
        selected = self.select(rows, background)
        self.assertEqual(self.counts(selected), [(3, 0), (2, 3)])
        self.assertEqual(selected[0]["ticker"], rows[1]["ticker"])

    def test_equal_mandatory_uses_auxiliary_before_old_tiebreaks(self):
        rows = [dict(self.rows[0], label="상승초입", score=100),
                dict(self.rows[1], label="준비구간", score=1)]
        background = {"items": [monitored(rows[0]["ticker"], 3, 1), monitored(rows[1]["ticker"], 3, 3)]}
        self.assertEqual(self.counts(self.select(rows, background)), [(3, 3), (3, 1)])

    def test_all_condition_combinations_are_descending(self):
        combos = [(m, a) for m in range(5) for a in range(4)]
        rows = self.rows[:20]
        background = {"items": [monitored(r["ticker"], m, a) for r, (m, a) in zip(rows, combos)]}
        selected = self.select(rows, background)
        self.assertEqual(self.counts(selected), sorted(combos, reverse=True))
        for row, (m, a) in zip(selected, self.counts(selected)):
            self.assertIn(f"{m}/4", row["_watchlist_conditions"]["mandatory_label"])
            self.assertIn(f"{a}/3", row["_watchlist_conditions"]["auxiliary_label"])

    def test_server_counts_are_used_before_top_twenty_cutoff(self):
        background = {"items": [monitored(r["ticker"], 1, 0) for r in self.rows]}
        background["items"][0] = monitored(self.rows[0]["ticker"], 4, 3)
        selected = self.select(self.rows, background)
        self.assertEqual(len(selected), 20)
        self.assertEqual(selected[0]["ticker"], self.rows[0]["ticker"])
        self.assertEqual(self.counts(selected)[0], (4, 3))

    def test_selection_is_read_only_and_does_not_fabricate_persistence(self):
        before = copy.deepcopy(self.rows)
        self.select(self.rows)
        self.select(self.rows)
        self.assertEqual(self.harness.history, {})
        self.assertEqual(self.rows, before)

    def test_local_fallback_records_only_one_observation_per_fragment_run(self):
        row = dict(self.rows[0], breakout=9990, stop=9500, score=90, label="1차매수구간")
        self.select([row])
        first = self.select([row], record_history=True)[0]["_watchlist_conditions"]
        self.assertEqual(self.harness.history[row["ticker"]]["consecutive"], 1)
        self.assertEqual(first["auxiliary_count"], 2)
        second = self.select([row], record_history=True)[0]["_watchlist_conditions"]
        self.assertEqual(self.harness.history[row["ticker"]]["consecutive"], 2)
        self.assertEqual(second["auxiliary_count"], 3)

    def test_partial_server_checks_use_consistent_local_fallback(self):
        row = self.rows[0]
        expected = self.select([row])[0]["_watchlist_conditions"]
        background = {"items": [{"ticker": row["ticker"], "action": "부분 자료", "checks": {"gap": True}}]}
        self.assertEqual(self.select([row], background)[0]["_watchlist_conditions"], expected)

    def test_price_fragment_displays_exactly_the_counts_used_for_ranking(self):
        harness = brightness.PriceRefreshTests()
        harness.setUp()
        background = {"items": [monitored("000001", 2, 3), monitored("000002", 3, 1)]}
        harness.ns["_load_background_state"] = lambda: background
        harness.render(harness.items)
        frame = harness.frame()
        self.assertEqual(frame["코드"].tolist(), ["000002", "000001"])
        self.assertIn("3/4", frame.iloc[0]["필수조건"])
        self.assertIn("1/3", frame.iloc[0]["보조조건"])
        self.assertNotIn("매수 우선순위", frame.columns)
        self.assertEqual(frame["관찰 우선순위"].tolist(), [1, 2])
        # A new server snapshot must replace both order and labels together.
        background["items"][0] = monitored("000001", 4, 0)
        harness.render(harness.items)
        updated = harness.frame()
        self.assertEqual(updated.iloc[0]["코드"], "000001")
        self.assertIn("4/4", updated.iloc[0]["필수조건"])
        self.assertIn("0/3", updated.iloc[0]["보조조건"])

    def test_legacy_decision_tuple_and_snapshot_agree(self):
        decide = self.harness.ns["_decision_action"]
        for row in self.rows:
            legacy = decide(row, 1, record_history=False)
            snapshot = decide(row, 1, record_history=False, return_snapshot=True)
            self.assertEqual(legacy, (snapshot["action"], snapshot["checks"],
                                      snapshot["mandatory_label"], snapshot["auxiliary_label"]))


if __name__ == "__main__":
    unittest.main()
