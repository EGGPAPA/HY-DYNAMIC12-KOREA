import unittest
from datetime import date
import json
import tempfile
from pathlib import Path
from dynamic_etf_candidates import select_candidates, load_catalog

def etf(code,name,holdings,asof="2026-09-11"):
    return {"code":code,"name":name,"asof":asof,"market_cap":100,"holdings":[{"name":n,"weight":w} for n,w in holdings]}

class DynamicCandidatesTests(unittest.TestCase):
    def setUp(self):
        self.catalog=[etf("1","은행 ETF",[("KB금융",50),("신한지주",40)]),
                      etf("2","반도체 ETF",[("HPSP",40),("SK하이닉스",50)]),
                      etf("3","현재 ETF",[("현대차",80)])]
    def test_universe_changes_with_leaders(self):
        bank=select_candidates(self.catalog,["KB금융"],1)[0]
        semi=select_candidates(self.catalog,["HPSP"],1)[0]
        self.assertEqual(list(bank),["은행 ETF"])
        self.assertEqual(list(semi),["반도체 ETF"])
    def test_current_reference_preserved(self):
        candidates,_,_=select_candidates(self.catalog,["HPSP"],1,"3")
        self.assertEqual(set(candidates),{"반도체 ETF","현재 ETF"})
        self.assertEqual(candidates["현재 ETF"]["matched"],[])
    def test_normalization_and_weight(self):
        candidates,_,_=select_candidates(self.catalog,["KB 금융","KB금융"],3)
        self.assertEqual(candidates["은행 ETF"]["matched_weight"],50)
        self.assertEqual(len(candidates["은행 ETF"]["matched"]),1)
    def test_unknown_does_not_invent_candidate(self):
        candidates,unmatched,count=select_candidates(self.catalog,["없는기업"],12)
        self.assertFalse(candidates)
        self.assertEqual(unmatched,["없는기업"])
        self.assertEqual(count,0)
    def test_stale_constituents_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"data.json"
            path.write_text(json.dumps({"etfs":[self.catalog[0],etf("4","오래된 ETF",[("HPSP",90)],"2026-07-01")]}),encoding="utf-8")
            valid,meta=load_catalog(path,date(2026,9,12))
            self.assertEqual(len(valid),1)
            self.assertEqual(meta["stale_count"],1)
    def test_empty_catalog_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"data.json";path.write_text('{"etfs":[]}',encoding="utf-8")
            with self.assertRaises(ValueError):load_catalog(path,date(2026,9,12))
if __name__=="__main__":unittest.main()
