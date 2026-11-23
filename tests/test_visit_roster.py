import json
import tempfile
import unittest
from pathlib import Path

from hailclaim import survey, visit, visit_gate
from hailclaim.store import connect, evidence, ingest, report, save_decisions

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "adjuster-windows.json"

AUDIT_KEYS = ("jobs", "workers", "travel_minutes", "checkins", "calendars",
              "certificates", "revocations", "closures")


def load_events():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["events"]


def base_events():
    """Offline sources and appointment ICS, before any QR checkin happens."""
    return [e for e in load_events() if e["kind"] != "checkin"]


def event(event_id, **patch):
    found = next(e for e in load_events() if e["id"] == event_id)
    found = json.loads(json.dumps(found))
    for key, value in patch.items():
        if key in ("event_time", "received_at"):
            found[key] = value
        else:
            found["data"][key] = value
    return found


def audit(roster, inputs):
    return visit_gate.audit_roster(roster, **{k: inputs[k] for k in AUDIT_KEYS})


class RosterPlan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = connect(Path(self.tmp.name) / "roster.db")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def ingest(self, events):
        with self.db:
            for e in events:
                ingest(self.db, e)

    def ev(self):
        return evidence(self.db, "roster-1")

    def fresh_roster(self, events, now, tag, provisional=None):
        db = connect(Path(self.tmp.name) / f"{tag}.db")
        with db:
            for e in events:
                ingest(db, e)
        roster = visit.plan_from_evidence(evidence(db, "roster-1"), now_minute=now,
                                          provisional=provisional)
        db.close()
        return roster

    def test_greedy_first_fit_leaves_roof_window_uncovered(self):
        self.ingest(base_events())
        inputs = visit.inputs_from_evidence(self.ev())
        local = [w for w in inputs["workers"] if not w["external"]]
        plan = visit.assign_first_fit(inputs["jobs"], local)
        self.assertEqual(plan, {"J1": "A"})
        self.assertNotIn("J2", plan)

    def test_withdraw_provisional_before_any_checkin(self):
        self.ingest(base_events())
        ev = self.ev()
        inputs = visit.inputs_from_evidence(ev)
        local = [w for w in inputs["workers"] if not w["external"]]
        provisional = visit.assign_first_fit(inputs["jobs"], local)
        roster = visit.plan_from_evidence(ev, now_minute=500, provisional=provisional)
        self.assertEqual(roster["assignments"], {"J1": "B", "J2": "A"})
        self.assertEqual(roster["withdrawals"],
                         [{"job_id": "J1", "worker_id": "A", "replacement": "B",
                           "reason": "withdrawn-before-checkin"}])
        self.assertEqual(roster["provisional"], {"J1": "A"})
        self.assertEqual(roster["costs"], [])
        self.assertEqual(roster["uncovered"], {})
        stops = {s["job_id"]: s for stops in roster["itineraries"].values() for s in stops}
        self.assertEqual((stops["J1"]["start_minute"], stops["J1"]["end_minute"]), (540, 580))
        self.assertEqual((stops["J2"]["start_minute"], stops["J2"]["end_minute"]), (600, 640))
        self.assertEqual(stops["J1"]["cert"]["id"], "cert-B-general")
        self.assertEqual(stops["J2"]["cert"]["id"], "cert-A-roof")
        self.assertEqual(audit(roster, inputs), [])

    def test_signed_checkin_locks_j1_and_calls_neighbor_c(self):
        self.ingest(base_events() + [event("checkin-J1-A")])
        roster = visit.plan_from_evidence(self.ev(), now_minute=545,
                                          provisional={"J1": "A"})
        self.assertEqual(roster["assignments"], {"J1": "A", "J2": "C"})
        self.assertEqual(roster["locked"], {"J1": "A"})
        self.assertEqual(roster["withdrawals"], [])
        overtime = [c for c in roster["costs"] if c["worker_id"] == "C"]
        self.assertEqual(overtime, [{"worker_id": "C", "job_id": "J2",
                                     "kind": "cross-station-overtime", "minutes": 40}])
        c_stop = roster["itineraries"]["C"][0]
        self.assertEqual((c_stop["leg"]["from_site"], c_stop["leg"]["to_site"],
                          c_stop["leg"]["minutes"]), ("North", "West", 25))
        self.assertEqual((c_stop["start_minute"], c_stop["end_minute"]), (600, 640))
        self.assertEqual(c_stop["cert"]["id"], "cert-C-roof")
        self.assertEqual(audit(roster, visit.inputs_from_evidence(self.ev())), [])
        self.ingest([event("checkin-J2-C")])
        roster2 = visit.plan_from_evidence(self.ev(), now_minute=641,
                                           provisional={"J1": "A"})
        self.assertEqual(roster2["locked"], {"J1": "A", "J2": "C"})
        self.assertEqual(roster2["assignments"], {"J1": "A", "J2": "C"})
        self.assertTrue(roster2["itineraries"]["C"][0]["locked"])

    def test_checkin_is_not_a_revocable_candidate(self):
        self.ingest(base_events() + [event("checkin-J1-A")])
        inputs = visit.inputs_from_evidence(self.ev())
        moved = {"itineraries": {"B": [
            {"job_id": "J1", "site": "East", "start_minute": 540, "end_minute": 580,
             "leg": {"from_site": "East", "to_site": "East", "minutes": 0}}]}}
        reasons = [c["reason"] for c in audit(moved, inputs)]
        self.assertIn("moved-after-checkin", reasons)

    def test_thirty_minute_leg_is_never_compressed(self):
        self.ingest(base_events())
        inputs = visit.inputs_from_evidence(self.ev())
        tampered = {"itineraries": {"A": [
            {"job_id": "J1", "site": "East", "start_minute": 540, "end_minute": 580,
             "leg": {"from_site": "West", "to_site": "East", "minutes": 30}},
            {"job_id": "J2", "site": "West", "start_minute": 600, "end_minute": 640,
             "leg": {"from_site": "East", "to_site": "West", "minutes": 30}}]}}
        conflicts = audit(tampered, inputs)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["reason"], "travel-compressed")
        self.assertEqual(conflicts[0]["earliest_minute"], 610)

    def test_early_checkin_waits_for_window(self):
        roster = self.fresh_roster(
            base_events() + [event("checkin-J1-A", event_time=530, at_minute=530)],
            535, "early", {"J1": "A"})
        self.assertEqual(roster["assignments"], {"J1": "A", "J2": "C"})
        stop = roster["itineraries"]["A"][0]
        self.assertTrue(stop["locked"])
        self.assertEqual((stop["start_minute"], stop["end_minute"]), (540, 580))
        self.assertEqual(roster["violations"], [])

    def test_late_checkin_stays_locked_and_flags_window(self):
        roster = self.fresh_roster(
            base_events() + [event("checkin-J1-A", event_time=550, at_minute=550)],
            555, "late", {"J1": "A"})
        self.assertEqual(roster["assignments"], {"J1": "A", "J2": "C"})
        stop = roster["itineraries"]["A"][0]
        self.assertTrue(stop["locked"])
        self.assertEqual((stop["start_minute"], stop["end_minute"]), (550, 590))
        self.assertIn({"job_id": "J1", "reason": "window-violation", "worker_id": "A",
                       "end_minute": 590, "window_end": 580}, roster["violations"])

    def test_received_order_does_not_change_replay(self):
        on_time = self.fresh_roster(base_events() + [event("checkin-J1-A")],
                                    545, "replay-1", {"J1": "A"})
        late_page = self.fresh_roster(
            base_events() + [event("checkin-J1-A", received_at=700)],
            545, "replay-2", {"J1": "A"})
        self.assertEqual(on_time["assignments"], late_page["assignments"])
        self.assertEqual(on_time["itineraries"], late_page["itineraries"])

    def test_duplicate_qr_is_idempotent_and_tamper_proof(self):
        scan = event("checkin-J1-A")
        self.ingest(base_events() + [scan])
        self.assertFalse(ingest(self.db, scan))
        with self.assertRaises(ValueError):
            with self.db:
                ingest(self.db, event("checkin-J1-A", at_minute=541))
        rescan = event("checkin-J1-A", event_time=541, at_minute=541)
        rescan["id"] = "checkin-J1-A-rescan"
        self.ingest([rescan])
        inputs = visit.inputs_from_evidence(self.ev())
        self.assertEqual(inputs["checkins"]["J1"]["at_minute"], 540)
        self.assertEqual(len(inputs["duplicates"]), 1)
        self.assertEqual(inputs["duplicates"][0]["reason"], "duplicate-scan")
        roster = visit.plan_from_evidence(self.ev(), now_minute=545,
                                          provisional={"J1": "A"})
        self.assertEqual(roster["assignments"], {"J1": "A", "J2": "C"})

    def test_revoked_certificate_replans_only_unexecuted(self):
        checkin_b = {"id": "checkin-J1-B", "kind": "checkin", "subject": "roster-1",
                     "event_time": 540, "received_at": 540,
                     "data": {"job_id": "J1", "worker_id": "B", "at_minute": 540,
                              "site": "East", "mode": "onsite", "qr": "QR-J1-B"}}
        revocation = {"id": "revoke-A-roof", "kind": "cert_revocation",
                      "subject": "roster-1", "event_time": 550, "received_at": 601,
                      "data": {"worker_id": "A", "skill": "roof"}}
        self.ingest(base_events() + [checkin_b])
        v2 = visit.plan_from_evidence(self.ev(), now_minute=545, provisional={"J1": "A"})
        self.assertEqual(v2["assignments"], {"J1": "B", "J2": "A"})
        self.ingest([revocation])
        roster = visit.plan_from_evidence(self.ev(), now_minute=560,
                                          provisional={"J1": "A"})
        self.assertEqual(roster["assignments"], {"J1": "B", "J2": "C"})
        self.assertEqual(roster["locked"], {"J1": "B"})
        stale = audit(v2, visit.inputs_from_evidence(self.ev()))
        self.assertIn({"job_id": "J2", "reason": "cert-invalid", "worker_id": "A",
                       "skill": "roof"}, stale)
        self.assertTrue(any(e["id"] == "checkin-J1-B" for e in self.ev()))

    def test_late_road_closure_replans_unexecuted_legs(self):
        closure = {"id": "closure-North-West", "kind": "road_closure",
                   "subject": "roster-1", "event_time": 570, "received_at": 573,
                   "data": {"from_site": "North", "to_site": "West"}}
        self.ingest(base_events() + [event("checkin-J1-A")])
        before = len(self.ev())
        v2 = visit.plan_from_evidence(self.ev(), now_minute=545, provisional={"J1": "A"})
        self.assertEqual(v2["assignments"], {"J1": "A", "J2": "C"})
        self.ingest([closure])
        roster = visit.plan_from_evidence(self.ev(), now_minute=574,
                                          provisional={"J1": "A"})
        self.assertEqual(roster["assignments"], {"J1": "A"})
        certificate = roster["uncovered"]["J2"]
        reasons = {a["worker_id"]: a["reason"] for a in certificate["conflict_set"]}
        self.assertEqual(reasons, {"A": "locked-to-job", "B": "skill-missing",
                                   "C": "road-closed"})
        self.assertEqual(certificate["reschedule"],
                         {"worker_id": "A", "earliest_minute": 610})
        self.assertEqual(len(self.ev()), before + 1)
        self.assertTrue(any(e["id"] == "checkin-J1-A" for e in self.ev()))

    def test_without_c_j2_gets_infeasibility_certificate(self):
        events = [e for e in base_events()
                  if not e["id"].startswith(("cert-C", "shift-C"))]
        self.ingest(events + [event("checkin-J1-A")])
        roster = visit.plan_from_evidence(self.ev(), now_minute=545,
                                          provisional={"J1": "A"})
        self.assertEqual(roster["assignments"], {"J1": "A"})
        certificate = roster["uncovered"]["J2"]
        self.assertEqual(certificate["window"], [600, 640])
        reasons = {a["worker_id"]: a["reason"] for a in certificate["conflict_set"]}
        self.assertEqual(reasons, {"A": "locked-to-job", "B": "skill-missing"})
        earliest = next(a for a in certificate["conflict_set"] if a["worker_id"] == "A")
        self.assertEqual(earliest["earliest_minute"], 610)
        self.assertEqual(certificate["reschedule"],
                         {"worker_id": "A", "earliest_minute": 610})

    def test_remote_phone_call_is_not_an_onsite_checkin(self):
        remote = {"id": "checkin-J2-remote", "kind": "checkin", "subject": "roster-1",
                  "event_time": 600, "received_at": 600,
                  "data": {"job_id": "J2", "worker_id": "A", "at_minute": 600,
                           "site": "West", "mode": "remote", "qr": "QR-J2-REMOTE"}}
        obs = {"id": "obs-J2-west", "kind": "sample_observation", "subject": "roster-1",
               "event_time": 610, "received_at": 615,
               "data": {"parcel": "West", "tree_id": "west-1", "sample_time": 610,
                        "loss": 0.5}}
        self.ingest(base_events() + [event("checkin-J1-A"), remote, obs])
        sources = survey.observation_sources(self.ev())
        self.assertEqual([s["observation"] for s in sources], ["obs-J1-east"])
        self.assertFalse(any(s["checkin"] == "checkin-J2-remote" for s in sources))
        roster = visit.plan_from_evidence(self.ev(), now_minute=605,
                                          provisional={"J1": "A"})
        self.assertNotIn("J2", roster["locked"])
        self.assertIn({"job_id": "J2", "reason": "remote-checkin-rejected",
                       "worker_id": "A"}, roster["violations"])

    def test_observation_closes_only_with_onsite_checkin_same_plot_window(self):
        self.ingest(base_events())
        self.assertEqual(survey.observation_sources(self.ev()), [])
        self.ingest([event("checkin-J1-A")])
        sources = survey.observation_sources(self.ev())
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["observation"], "obs-J1-east")
        self.assertEqual(sources[0]["checkin"], "checkin-J1-A")
        self.assertEqual(sources[0]["job_id"], "J1")

    def test_lunch_break_defers_afternoon_job(self):
        j4 = {"id": "appt-J4", "kind": "appointment", "subject": "roster-1",
              "event_time": 480, "received_at": 480,
              "data": {"job_id": "J4", "skill": "general", "site": "East",
                       "window_start": 700, "window_end": 820, "duration_minutes": 40,
                       "customer": "East 果园"}}
        self.ingest(base_events() + [j4])
        roster = visit.plan_from_evidence(self.ev(), now_minute=500)
        self.assertEqual(roster["assignments"]["J4"], "B")
        stop = next(s for s in roster["itineraries"]["B"] if s["job_id"] == "J4")
        self.assertEqual((stop["start_minute"], stop["end_minute"]), (780, 820))

    def test_roster_versions_persist_and_checkins_are_never_erased(self):
        self.ingest(base_events())
        v1 = visit.plan_from_evidence(self.ev(), now_minute=500, provisional={"J1": "A"})
        with self.db:
            save_decisions(self.db, "roster-1",
                           [("外勤行程", v1, [e["id"] for e in self.ev()])])
        self.ingest([event("checkin-J1-A"), event("checkin-J2-C")])
        v2 = visit.plan_from_evidence(self.ev(), now_minute=641, provisional={"J1": "A"})
        with self.db:
            save_decisions(self.db, "roster-1",
                           [("外勤行程", v2, [e["id"] for e in self.ev()])])
        stored = report(self.db, "roster-1")["外勤行程"]
        self.assertEqual(stored["version"], 2)
        self.assertEqual(stored["result"]["assignments"], {"J1": "A", "J2": "C"})
        rows = self.db.execute(
            "SELECT version FROM decision WHERE subject=? AND surface=? ORDER BY version",
            ("roster-1", "外勤行程")).fetchall()
        self.assertEqual([r["version"] for r in rows], [1, 2])
        remaining = {e["id"] for e in self.ev()}
        self.assertIn("checkin-J1-A", remaining)
        self.assertIn("checkin-J2-C", remaining)


if __name__ == "__main__":
    unittest.main()
