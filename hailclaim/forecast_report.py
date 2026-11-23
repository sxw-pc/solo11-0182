"""Fixture loader and report builder for the 09:00 as-of forecast backtest.

The fixture ``fixtures/forecast-asof.json`` carries four kinds of data:

* ``feature_pages``  - every station / radar / correction page ever received,
  each with its physical ``event_time``, gateway ``received_at`` and optional
  correction / clock-revision publication times;
* ``cases``          - the frozen test split: 09:00 forecasts, labels, and the
  feature-page ids each model version is allowed to use;
* ``models``         - version descriptors (v1 provisional, v1 diagnostic, v2
  radar), including which snapshot each was trained on;
* ``snapshots``      - the training snapshots as they were assembled, so a
  snapshot that already consumed late pages can be flagged as leaked.
"""

from __future__ import annotations

import json
from pathlib import Path

from .backtest import evaluate, model_identity, score, score_legacy, score_rows
from .forecast_feature import (
    as_of_cross_section,
    earliest_visible,
    issue_time,
)

DEFAULT_FIXTURE = Path(__file__).resolve().parent.parent / "fixtures/forecast-asof.json"

THRESHOLD = 3


def load_fixture(path=None):
    path = Path(path) if path is not None else DEFAULT_FIXTURE
    return json.loads(path.read_text(encoding="utf-8"))


def _pages(fixture):
    pages = []
    for page in fixture.get("feature_pages", []):
        record = dict(page)
        record.setdefault("feature_id", page.get("id"))
        record.setdefault("source", page.get("kind", page.get("source", "unknown")))
        pages.append(record)
    return pages


def _cases(fixture):
    return [dict(case) for case in fixture.get("cases", [])]


def _cases_for_model(fixture, model):
    """Cases on the frozen test split that the model declares it can cover."""

    ids = set(model.get("cases") or [case["event_id"] for case in _cases(fixture)])
    return [case for case in _cases(fixture) if case["event_id"] in ids]


def _scoped_features(pages, model):
    allowed = set(model.get("feature_ids") or [])
    if not allowed:
        return []
    return [page for page in pages if page.get("feature_id") in allowed]


def snapshot_audit(fixture):
    """Flag training snapshots that already contained late pages."""

    pages = {page.get("feature_id"): page for page in _pages(fixture)}
    later = {case["event_id"]: case for case in _cases(fixture)}
    audit = []
    for snapshot in fixture.get("snapshots", []):
        leaked = []
        for page_id in snapshot.get("page_ids", []):
            page = pages.get(page_id)
            event_id = (page or {}).get("event_id") or page_id.split("-page-")[0]
            issued = snapshot.get("issued_at", {}).get(event_id)
            if issued is None:
                issued = later.get(event_id, {}).get("issued_at")
            visible = earliest_visible(page) if page else None
            if page is None or visible is None or (issued is not None and visible > issued):
                leaked.append(page_id)
        audit.append(
            {
                "name": snapshot.get("name"),
                "purpose": snapshot.get("purpose"),
                "created_at": snapshot.get("created_at"),
                "freeze_as_of": snapshot.get("freeze_as_of"),
                "declared_used_for": snapshot.get("used_for"),
                "leaked_feature_ids": sorted(leaked),
                "leaked": bool(leaked),
            }
        )
    return sorted(audit, key=lambda item: str(item["name"]))


def _split_gap(fixture, model, pages, cases):
    """Detect a model trained on the very pages its test cases score on."""

    trained = set(model.get("train_feature_ids") or [])
    tested = {
        page_id
        for page_id in (model.get("feature_ids") or [])
        if page_id in {page.get("feature_id") for page in pages}
    }
    case_ids = {case["event_id"] for case in cases}
    shared = sorted(
        page_id
        for page_id in trained & tested
        if pages_by_id(pages, page_id).get("event_id") in case_ids
    )
    return shared


def pages_by_id(pages, page_id):
    for page in pages:
        if page.get("feature_id") == page_id:
            return page
    return {}


def build_report(path=None):
    """Assemble the as-of cross-section, leakage list and model decisions."""

    fixture = load_fixture(path)
    pages = _pages(fixture)
    cases = _cases(fixture)
    models = [dict(model) for model in fixture.get("models", [])]
    threshold = int(fixture.get("threshold", THRESHOLD))

    section = as_of_cross_section(cases, pages)

    model_reports = []
    for model in models:
        scoped_cases = _cases_for_model(fixture, model)
        scoped_pages = _scoped_features(pages, model)
        scoped_score = score(scoped_cases, scoped_pages)
        status = model.get("status")
        releasable = status == "published" and scoped_score["correct"] >= threshold
        if status != "published":
            decision = "withheld"
            reason = model.get("status_reason", "not_published")
        elif releasable:
            decision = "published"
            reason = "threshold_met"
        else:
            decision = "withheld"
            reason = "below_threshold"
        model_reports.append(
            {
                "version": model.get("version"),
                "identity": model_identity(model),
                "status": status,
                "decision": decision,
                "reason": reason,
                "threshold": threshold,
                "score": "{}/{}".format(scoped_score["correct"], scoped_score["total"]),
                "correct": scoped_score["correct"],
                "total": scoped_score["total"],
                "predictions": scoped_score["predictions"],
                "as_of": scoped_score["as_of"],
                "feature_ids": scoped_score["feature_ids"],
                "excluded_feature_ids": scoped_score["excluded_feature_ids"],
                "scope": model.get("scope"),
                "disclosure": model.get("disclosure"),
                "rows": score_rows(scoped_cases, scoped_pages),
                "leakage": scoped_score["leakage"],
                "split_gap": _split_gap(fixture, model, pages, scoped_cases),
            }
        )

    legacy = score_legacy(cases, pages)
    no_late = score(cases, [page for page in pages if earliest_visible(page) is None])
    release = next((item for item in model_reports if item["decision"] == "published"), None)

    return {
        "schema": "hailclaim-forecast-report/2",
        "threshold": threshold,
        "as_of": {
            "issue_times": sorted({issue_time(case) for case in cases}),
            "rows": [
                {
                    "event_id": event_id,
                    "issued_at": section["as_of"][event_id],
                    "feature_id": section["feature_ids"][event_id],
                    "prediction": section["predictions"][event_id],
                    "label": next(case["label"] for case in cases if case["event_id"] == event_id),
                }
                for event_id in sorted(section["as_of"])
            ],
            "visible_feature_ids": section["visible_feature_ids"],
            "excluded_feature_ids": section["excluded_feature_ids"],
        },
        "leakage": section["leakage"],
        "snapshots": snapshot_audit(fixture),
        "models": model_reports,
        "legacy": {
            "score": "{}/{}".format(legacy["correct"], legacy["total"]),
            "correct": legacy["correct"],
            "total": legacy["total"],
            "predictions": legacy["predictions"],
            "note": "diagnostic only: ranks on received_at and ignores availability at issued_at",
        },
        "no_post_issue_pages": {
            "score": "{}/{}".format(no_late["correct"], no_late["total"]),
            "correct": no_late["correct"],
            "total": no_late["total"],
        },
        "release": release,
        "correction": _correction(release, legacy, threshold),
    }


def _correction(release, legacy, threshold):
    """Public correction text: never silently rewrite 4/4 into 2/4."""

    legacy_text = "{}/{}".format(legacy["correct"], legacy["total"])
    if release:
        corrected = "{} ({})".format(release["score"], release["version"])
        return {
            "supersedes": "09:00 果园雹情预报 4/4",
            "old_claim": legacy_text,
            "old_status": "事后诊断，不构成预报能力",
            "new_claim": corrected,
            "new_status": "可发布" if release["correct"] >= threshold else "未达门槛",
            "action": "publish_v2_with_scope" if release["correct"] >= threshold else "withdraw_v1",
            "scope": release.get("scope"),
            "threshold": threshold,
            "note": "4/4 来自 10:05 才到达的强度页，非 09:00 可用信息；保留为诊断记录并附更正说明。",
        }
    return {
        "supersedes": "09:00 果园雹情预报 4/4",
        "old_claim": legacy_text,
        "old_status": "事后诊断，不构成预报能力",
        "new_claim": "0/{} (v1 withdrawn)".format(legacy["total"]),
        "new_status": "撤销 09:00 预警发布资格",
        "action": "withdraw_v1",
        "scope": None,
        "threshold": threshold,
        "note": "无更早独立数据源，v1 仅保留为事后诊断。",
    }


def summary(report=None, path=None):
    """Compact CLI/JSON view of ``build_report``."""

    report = report or build_report(path)
    return {
        "as_of": report["as_of"],
        "leakage": report["leakage"],
        "snapshots": report["snapshots"],
        "legacy": report["legacy"],
        "no_post_issue_pages": report["no_post_issue_pages"],
        "models": [
            {
                key: item[key]
                for key in (
                    "version",
                    "identity",
                    "status",
                    "decision",
                    "reason",
                    "score",
                    "predictions",
                    "scope",
                    "split_gap",
                )
            }
            for item in report["models"]
        ],
        "release": report["release"],
        "correction": report["correction"],
    }
