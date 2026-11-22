import unittest

from hailclaim.backtest import score


class BacktestSeed(unittest.TestCase):
    def test_post_issue_features_give_false_perfect_score(self):
        labels = [1, 0, 1, 0]
        cases = [{"event_id": f"E{i}", "issued_at": 540, "label": label}
                 for i, label in enumerate(labels, 1)]
        features = [{"event_id": f"E{i}", "event_time": 530,
                     "received_at": 605, "intensity": label}
                    for i, label in enumerate(labels, 1)]
        self.assertEqual(score(cases, features)["correct"], 4)
        self.assertEqual(score(cases, [])["correct"], 2)
