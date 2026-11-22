import unittest

from hailclaim.visit import assign_first_fit, reassign_after_checkin
from hailclaim.visit_gate import assess_reassignment


class VisitSeed(unittest.TestCase):
    def test_first_fit_can_block_specialist_visit(self):
        jobs = [{"id": "J1", "skill": "general"},
                {"id": "J2", "skill": "roof"}]
        adjusters = [{"id": "A", "skills": ["general", "roof"]},
                     {"id": "B", "skills": ["general"]}]
        self.assertEqual(assign_first_fit(jobs, adjusters), {"J1": "A"})

    def test_reassignment_ignores_signed_checkin(self):
        jobs = [{"id": "J1", "skill": "general"},
                {"id": "J2", "skill": "roof"}]
        adjusters = [{"id": "A", "skills": ["general", "roof"]},
                     {"id": "B", "skills": ["general"]}]
        self.assertEqual(reassign_after_checkin(jobs, adjusters, {"J1": "A"}),
                         {"J1": "B", "J2": "A"})

    def test_reassignment_breaks_real_travel_window(self):
        jobs = [
            {"id": "J1", "skill": "general", "site": "East",
             "start_minute": 540, "duration_minutes": 40},
            {"id": "J2", "skill": "roof", "site": "West",
             "start_minute": 600, "duration_minutes": 40},
        ]
        workers = [{"id": "A", "skills": ["general", "roof"]},
                   {"id": "B", "skills": ["general"]}]
        checkins = {"J1": {"worker_id": "A", "at_minute": 540}}
        plan = reassign_after_checkin(jobs, workers, checkins)
        conflicts = assess_reassignment(plan, jobs, checkins, {("East", "West"): 30})
        self.assertEqual(conflicts, [
            {"job_id": "J1", "reason": "moved-after-checkin"},
            {"job_id": "J2", "reason": "late-start", "earliest_minute": 610},
        ])
