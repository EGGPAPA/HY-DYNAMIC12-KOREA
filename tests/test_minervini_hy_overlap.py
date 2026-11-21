"""Pure tests for Minervini 8/8 -> unchanged HY 5-of-7 intersection."""
import unittest

from minervini_hy_overlap import minervini_roster, combined_report


def asof_snapshot():
    return {"complete": True, "rs_ready": True, "asof": "2026-11-20", "items": {
        "000001": {"asof": "2026-11-20", "name": "8조건통과", "market": "KOSPI",
                   "all_pass": True, "passed": 8, "pending": 0},
        "000002": {"asof": "2026-11-20", "name": "7조건통과", "market": "KOSDAQ",
                   "all_pass": False, "passed": 7, "pending": 1},
        "000003": {"asof": "2026-11-20", "name": "8조건통과2", "market": "KOSPI",
                   "all_pass": True, "passed": 8, "pending": 0}}}


def live_context():
    return {"intraday": True, "basis": "quote", "day": "20261123", "previous": "20261120"}


def hy_signal(code, mandatory=True, auxiliary=True, valid=True):
    return {"ticker": code, "valid": valid, "intraday": True, "basis": "quote",
            "label": "🟢 상승초입", "mandatory_count": 4 if mandatory else 3,
            "auxiliary_count": 1 if auxiliary else 0, "price": 30000,
            "volume_ratio": 1.6, "action": "장중 잠정 관찰",
            "checks": {"gap": mandatory, "volume": True, "close": True, "risk": True,
                       "stage": auxiliary, "score": False, "persistence": False}}


class JointScreenTests(unittest.TestCase):
    def test_only_eight_of_eight_enters_second_stage(self):
        rows,error=minervini_roster(asof_snapshot(),live_context())
        self.assertFalse(error)
        self.assertEqual([x["ticker"] for x in rows],["000001","000003"])

    def test_missing_rs_or_stale_snapshot_is_held(self):
        s=asof_snapshot();s["complete"]=False
        self.assertTrue(minervini_roster(s,live_context())[1])
        s=asof_snapshot();s["asof"]="2026-11-19"
        self.assertTrue(minervini_roster(s,live_context())[1])

    def test_no_holiday_reference_falsely_passes(self):
        x={**live_context(),"intraday":False,"basis":"close"}
        self.assertTrue(minervini_roster(asof_snapshot(),x)[1])
        self.assertFalse(combined_report([],[],x)["valid"])

    def test_exact_hy_gate_only_mandatory_four_plus_aux_one(self):
        rows,_=minervini_roster(asof_snapshot(),live_context())
        result=combined_report(rows,[hy_signal("000001"),hy_signal("000003",mandatory=False)],live_context())
        self.assertTrue(result["valid"])
        self.assertEqual(result["qualified_count"],1)
        self.assertEqual(result["candidates"][0]["ticker"],"000001")
        self.assertEqual(result["failed"][0]["checks"],6)

    def test_missing_quote_is_pending_not_no_signal(self):
        rows,_=minervini_roster(asof_snapshot(),live_context())
        result=combined_report(rows,[hy_signal("000001")],live_context())
        self.assertEqual(result["qualified_count"],1)
        self.assertEqual(len(result["pending"]),1)
        self.assertEqual(result["pending"][0]["ticker"],"000003")


if __name__ == "__main__":
    unittest.main()
