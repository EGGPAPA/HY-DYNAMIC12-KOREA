import unittest
from dual_leader_watch import classify_candidate, split_leader_watch

def sample(ticker, **changes):
    row = dict(ticker=ticker, ordinary=True, tradable=True, eligible=True,
               close=11000, ma20=10500, ma60=10000, ma20_prev5=10200,
               mean_value20=3_000_000_000, ret20_pct=8, excess20_pct=5,
               volume_ratio=1.8, leader_eligible=False, leader_score=70)
    row.update(changes)
    return row

class DualLeaderWatchTests(unittest.TestCase):
    def test_early_and_continuation(self):
        self.assertEqual(classify_candidate(sample('000001')), 'A')
        self.assertEqual(classify_candidate(sample('000002', leader_eligible=True)), 'B')
    def test_missing_volume_not_early(self):
        row = sample('000003')
        del row['volume_ratio']
        self.assertIsNone(classify_candidate(row))
    def test_not_tradable(self):
        self.assertIsNone(classify_candidate(sample('000004', tradable=False)))
    def test_no_duplicates_or_mutation(self):
        a = sample('000005')
        groups = split_leader_watch([a, a, sample('000006', leader_eligible=True)])
        self.assertEqual([r['ticker'] for r in groups['A']], ['000005'])
        self.assertEqual([r['ticker'] for r in groups['B']], ['000006'])
        self.assertNotIn('watch_group', a)
    def test_caps(self):
        rows = [sample(f'{i:06d}') for i in range(30)]
        rows += [sample(f'{i:06d}', leader_eligible=True) for i in range(30, 60)]
        groups = split_leader_watch(rows)
        self.assertEqual((len(groups['A']), len(groups['B'])), (20, 20))

if __name__ == '__main__':
    unittest.main()
