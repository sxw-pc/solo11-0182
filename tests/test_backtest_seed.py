import unittest

from hailclaim.backtest import score, score_legacy


class BacktestSeed(unittest.TestCase):
    """The original four-event counter-example, kept verbatim.

    Every case is issued at 540 (09:00) while its only feature row has
    event_time=530 but received_at=605 (10:05).  The old join produced a false
    4/4; the point-in-time join must produce 2/4, and the old result must remain
    reproducible only through the explicitly labelled legacy diagnostic.
    """

    def setUp(self):
        labels = [1, 0, 1, 0]
        self.cases = [{"event_id": f"E{i}", "issued_at": 540, "label": label}
                      for i, label in enumerate(labels, 1)]
        self.features = [{"id": f"E{i}-page", "event_id": f"E{i}", "event_time": 530,
                          "received_at": 605, "intensity": label}
                         for i, label in enumerate(labels, 1)]

    def test_legacy_join_reproduces_false_perfect_score(self):
        result = score_legacy(self.cases, self.features)
        self.assertEqual(result["correct"], 4)
        self.assertEqual(result["predictions"], {"E1": 1, "E2": 0, "E3": 1, "E4": 0})

    def test_point_in_time_join_rejects_rows_received_after_issue(self):
        result = score(self.cases, self.features)
        self.assertEqual(result["correct"], 2)
        self.assertEqual(result["predictions"], {"E1": 0, "E2": 0, "E3": 0, "E4": 0})
        self.assertEqual(result["excluded_feature_ids"], ["E1-page", "E2-page", "E3-page", "E4-page"])

    def test_missing_features_fall_back_to_zero(self):
        self.assertEqual(score(self.cases, [])["correct"], 2)

    def test_event_time_alone_does_not_fix_causality(self):
        # A feature that only carries event_time (no arrival) cannot be certified.
        bare = [{"event_id": f"E{i}", "event_time": 530, "intensity": label}
                for i, label in enumerate([1, 0, 1, 0], 1)]
        self.assertEqual(score(self.cases, bare)["correct"], 2)

    def test_delaying_labels_does_not_fix_causality(self):
        cases = [dict(case, label=1 - case["label"]) for case in self.cases]
        self.assertEqual(score(cases, self.features)["correct"], 2)
