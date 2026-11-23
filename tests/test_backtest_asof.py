import json
import tempfile
import unittest
from pathlib import Path

from hailclaim import forecast_feature as ff
from hailclaim.backtest import (
    score,
    score_legacy,
    score_rows,
    leakage_report,
    deployability,
)
from hailclaim.forecast_report import build_report, load_fixture, snapshot_audit
from hailclaim.store import (
    connect,
    ingest,
    evidence,
    forecast_features,
    as_of_snapshot,
    save_forecast_snapshot,
    load_forecast_snapshot,
    leaked_snapshots,
    save_forecast_release,
    forecast_releases,
    report,
    save_decisions,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures/forecast-asof.json"

# The frozen counter-example: four 09:00 forecasts, four intensity pages with
# event_time=530 that only arrive at 605, labels 1/0/1/0.
LATE_CASES = [{"event_id": f"E{i}", "issued_at": 540, "label": lab}
              for i, lab in enumerate([1, 0, 1, 0], 1)]
LATE_PAGES = [{"id": f"E{i}-station", "event_id": f"E{i}", "event_time": 530,
               "received_at": 605, "intensity": lab, "source": "station"}
              for i, lab in enumerate([1, 0, 1, 0], 1)]


class AsOfJoin(unittest.TestCase):
    def test_earliest_visible_is_max_of_observation_and_arrival(self):
        row = {"event_time": 530, "received_at": 605}
        self.assertEqual(ff.earliest_visible(row), 605)
        row = {"event_time": 550, "received_at": 540}
        self.assertEqual(ff.earliest_visible(row), 550)

    def test_correction_time_also_blocks_visibility(self):
        row = {"event_time": 530, "received_at": 540, "corrected_at": 600}
        self.assertEqual(ff.earliest_visible(row), 600)
        self.assertFalse(ff.row_visible(row, 540))
        self.assertEqual(ff.visibility_issues(row, 540), ["late_correction"])

    def test_clock_revision_blocks_visibility(self):
        row = {"event_time": 530, "received_at": 540, "event_time_revised_at": 700}
        self.assertEqual(ff.earliest_visible(row), 700)
        self.assertIn("clock_revision", ff.visibility_issues(row, 540))

    def test_unknown_arrival_is_never_visible(self):
        row = {"event_time": 530, "intensity": 1}
        self.assertIsNone(ff.earliest_visible(row))
        self.assertFalse(ff.row_visible(row, 10_000))

    def test_latest_by_event_as_of_filters_before_ranking(self):
        pages = [
            {"id": "E1-early", "event_id": "E1", "event_time": 520, "received_at": 530, "intensity": 0},
            {"id": "E1-late", "event_id": "E1", "event_time": 530, "received_at": 605, "intensity": 1},
        ]
        self.assertEqual(ff.latest_by_event(pages, as_of=540)["E1"]["id"], "E1-early")
        self.assertEqual(ff.latest_by_event(pages)["E1"]["id"], "E1-late")

    def test_missing_issued_at_is_an_error(self):
        with self.assertRaises(ValueError):
            score([{"event_id": "E1", "label": 1}], LATE_PAGES)

    def test_intensity_threshold(self):
        self.assertEqual(ff.intensity_of({"intensity": 0}), 0.0)
        self.assertEqual(ff.intensity_of({"hail_intensity": 2}), 2.0)


class FrozenCounterExample(unittest.TestCase):
    def test_legacy_reproduces_4_of_4(self):
        self.assertEqual(score_legacy(LATE_CASES, LATE_PAGES)["correct"], 4)

    def test_no_late_pages_gives_2_of_4(self):
        self.assertEqual(score(LATE_CASES, [])["correct"], 2)

    def test_as_of_join_gives_2_of_4_and_predicts_all_zero(self):
        result = score(LATE_CASES, LATE_PAGES)
        self.assertEqual(result["correct"], 2)
        self.assertEqual(result["predictions"], {"E1": 0, "E2": 0, "E3": 0, "E4": 0})

    def test_event_time_at_530_does_not_make_page_visible(self):
        # Same page, but the *observation* claims 08:50; arrival still 10:05.
        for page in LATE_PAGES:
            self.assertLess(page["event_time"], 540)
            self.assertGreater(page["received_at"], 540)
        self.assertEqual(score(LATE_CASES, LATE_PAGES)["correct"], 2)

    def test_only_labels_delayed_does_not_change_the_join(self):
        cases = [dict(case, issued_at=900) for case in LATE_CASES]
        self.assertEqual(score(cases, LATE_PAGES)["correct"], 4)


class FixtureBacktest(unittest.TestCase):
    def setUp(self):
        self.fixture = load_fixture(FIXTURE)
        self.report = build_report(FIXTURE)
        self.models = {m["version"]: m for m in self.report["models"]}

    def test_four_cases_all_issued_at_540(self):
        self.assertEqual({case["issued_at"] for case in self.fixture["cases"]}, {540})
        self.assertEqual(sorted(case["event_id"] for case in self.fixture["cases"]),
                         ["E1", "E2", "E3", "E4"])

    def test_legacy_and_no_late_scores(self):
        self.assertEqual(self.report["legacy"]["score"], "4/4")
        self.assertEqual(self.report["no_post_issue_pages"]["score"], "2/4")

    def test_v1_is_withheld_not_published(self):
        v1 = self.models["v1"]
        self.assertEqual(v1["decision"], "withheld")
        self.assertFalse(v1["reason"] == "threshold_met")
        self.assertEqual(v1["score"], "2/4")

    def test_v2_reaches_3_of_4_with_radar_only(self):
        v2 = self.models["v2"]
        self.assertEqual(v2["score"], "3/4")
        self.assertEqual(v2["decision"], "published")
        self.assertEqual(v2["source_scope"] if "source_scope" in v2 else v2["scope"]["sources"], ["radar"])

    def test_v2_never_uses_the_1005_station_pages(self):
        v2 = self.models["v2"]
        self.assertFalse(any("station" in fid for fid in v2["feature_ids"].values()))
        self.assertTrue(all("radar" in fid for fid in v2["feature_ids"].values()))

    def test_station_pages_are_all_leakage(self):
        leaked = {row["feature_id"] for row in self.report["leakage"]}
        self.assertEqual(leaked, {f"E{i}-page-station" for i in range(1, 5)})
        self.assertTrue(all("late_arrival" in row["reasons"] for row in self.report["leakage"]))

    def test_radar_pages_are_visible_at_09(self):
        visible = set(self.report["as_of"]["visible_feature_ids"])
        self.assertEqual(visible, {f"E{i}-page-radar" for i in range(1, 5)})
        self.assertFalse(visible & {f"E{i}-page-station" for i in range(1, 5)})

    def test_leaked_training_snapshot_is_flagged(self):
        snapshots = {s["name"]: s for s in self.report["snapshots"]}
        self.assertTrue(snapshots["snapshot-v1-provisional"]["leaked"])
        self.assertEqual(len(snapshots["snapshot-v1-provisional"]["leaked_feature_ids"]), 4)
        self.assertFalse(snapshots["snapshot-v2-radar"]["leaked"])

    def test_v1_training_snapshot_taints_the_model(self):
        v1 = self.models["v1"]
        self.assertTrue(v1["split_gap"])
        self.assertEqual(len(v1["split_gap"]), 4)
        self.assertEqual(self.models["v2"]["split_gap"], [])

    def test_correction_is_explicit_and_never_silently_2_of_4(self):
        correction = self.report["correction"]
        self.assertEqual(correction["old_claim"], "4/4")
        self.assertEqual(correction["action"], "publish_v2_with_scope")
        self.assertIn(correction["new_claim"], ("3/4 (v2)", "3/4"))
        self.assertIn("诊断", correction["old_status"])

    def test_same_source_set_reproduces_same_release(self):
        first = build_report(FIXTURE)
        second = build_report(FIXTURE)
        self.assertEqual(first["release"], second["release"])
        self.assertEqual(first["legacy"], second["legacy"])
        self.assertEqual(first["models"], second["models"])

    def test_multi_page_late_arrival_is_listed_per_page(self):
        fixture = load_fixture(FIXTURE)
        fixture["feature_pages"].append(
            {"id": "E1-page-correction", "kind": "station_feature", "source": "station",
             "event_id": "E1", "event_time": 530, "received_at": 540,
             "corrected_at": 610, "intensity": 1})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixture.json"
            path.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
            result = build_report(path)
        leak_ids = {row["feature_id"] for row in result["leakage"]}
        self.assertIn("E1-page-correction", leak_ids)
        correction_row = next(r for r in result["leakage"] if r["feature_id"] == "E1-page-correction")
        self.assertIn("late_correction", correction_row["reasons"])

    def test_clock_change_is_listed_as_leakage(self):
        fixture = load_fixture(FIXTURE)
        for page in fixture["feature_pages"]:
            if page["id"] == "E2-page-radar":
                page["event_time_revised_at"] = 620
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixture.json"
            path.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
            result = build_report(path)
        row = next(r for r in result["leakage"] if r["feature_id"] == "E2-page-radar")
        self.assertIn("clock_revision", row["reasons"])
        v2 = next(m for m in result["models"] if m["version"] == "v2")
        self.assertEqual(v2["score"], "2/4")  # E2 radar now unavailable
        self.assertEqual(v2["decision"], "withheld")


class StoreAsOf(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = connect(Path(self.tmp.name) / "case.db")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def page(self, **over):
        base = dict(id="p-E1", kind="station_feature", subject="case-1",
                    event_time=530, received_at=605,
                    data={"event_id": "E1", "intensity": 1})
        base.update(over)
        return base

    def test_event_time_and_received_at_are_both_stored(self):
        with self.db:
            ingest(self.db, self.page())
        rows = evidence(self.db, "case-1")
        self.assertEqual(rows[0]["event_time"], 530)
        self.assertEqual(rows[0]["received_at"], 605)

    def test_correction_and_issue_times_are_stored(self):
        with self.db:
            ingest(self.db, self.page(corrected_at=610, issued_at=540))
        row = evidence(self.db, "case-1")[0]
        self.assertEqual(row["corrected_at"], 610)
        self.assertEqual(row["issued_at"], 540)
        self.assertEqual(row["data"]["corrected_at"], 610)
        self.assertEqual(ff.earliest_visible(row), 610)

    def test_as_of_snapshot_separates_visible_from_excluded(self):
        with self.db:
            ingest(self.db, self.page())
            ingest(self.db, dict(id="r-E1", kind="radar_feature", subject="case-1",
                                 event_time=520, received_at=535,
                                 data={"event_id": "E1", "intensity": 0}))
        snapshot = as_of_snapshot(self.db, "case-1", 540)
        self.assertEqual([p["feature_id"] for p in snapshot["visible"]], ["r-E1"])
        self.assertEqual([p["feature_id"] for p in snapshot["excluded"]], ["p-E1"])
        self.assertEqual(snapshot["excluded"][0]["reasons"], ["late_arrival"])

    def test_forecast_pages_do_not_enter_claim_decisions(self):
        with self.db:
            ingest(self.db, self.page())
        self.assertEqual(evidence_shape := len(evidence(self.db, "case-1")), 1)
        self.assertEqual(len(forecast_features(self.db, "case-1")), 1)
        self.assertEqual(report(self.db, "case-1"), {})
        self.assertEqual(evidence_shape, 1)

    def test_late_page_cannot_backdate_a_saved_snapshot(self):
        with self.db:
            ingest(self.db, self.page())
            save_forecast_snapshot(
                self.db, "snap", "training", [self.page()], created_at=700,
                extra={"issued_at": {"E1": 540}})
        stored = load_forecast_snapshot(self.db, "snap")
        self.assertEqual(stored["pages"][0]["earliest_visible"], 605)
        self.assertEqual(leaked_snapshots(self.db), {"snap": ["p-E1"]})

    def test_conflicting_page_revision_is_rejected(self):
        with self.db:
            ingest(self.db, self.page())
        with self.assertRaises(ValueError):
            with self.db:
                ingest(self.db, self.page(data={"event_id": "E1", "intensity": 5}))

    def test_release_records_are_idempotent(self):
        with self.db:
            save_forecast_release(self.db, "v1", "withheld", {"score": "2/4"})
            save_forecast_release(self.db, "v1", "withheld", {"score": "2/4"})
        self.assertEqual(forecast_releases(self.db)["v1"]["payload"], {"score": "2/4"})

    def test_old_schema_evidence_is_upgraded_in_place(self):
        import sqlite3
        path = Path(self.tmp.name) / "old.db"
        old = sqlite3.connect(path)
        old.execute("CREATE TABLE evidence (id TEXT PRIMARY KEY, kind TEXT NOT NULL, subject TEXT NOT NULL, event_time INTEGER NOT NULL, received_at INTEGER NOT NULL, payload TEXT NOT NULL)")
        old.execute("INSERT INTO evidence VALUES ('x','policy','case-1',10,20,'{}')")
        old.commit()
        old.close()
        db = connect(path)
        try:
            columns = {r[1] for r in db.execute("PRAGMA table_info(evidence)")}
            self.assertTrue({"corrected_at", "clock_corrected_at", "issued_at"} <= columns)
            self.assertEqual(evidence(db, "case-1")[0]["id"], "x")
        finally:
            db.close()


class ModelDecision(unittest.TestCase):
    def test_threshold_gate(self):
        model = {"version": "v2", "scope": {"sources": ["radar"]}}
        published = deployability({"correct": 3, "total": 4}, model, 3)
        self.assertTrue(published["releasable_as_of_09"])
        withheld = deployability({"correct": 2, "total": 4}, {"version": "v1"}, 3)
        self.assertFalse(withheld["releasable_as_of_09"])
        self.assertEqual(withheld["reason"], "below_threshold")


if __name__ == "__main__":
    unittest.main()
