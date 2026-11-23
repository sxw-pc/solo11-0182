"""Score hail predictions from point-in-time feature rows.

The default path is strict: for each case the feature join only sees pages
that were fully readable at the case's ``issued_at``.  The old behaviour (rank
only on ``received_at``, predict from whichever page arrived last) is kept as
the explicitly named ``legacy`` diagnostic so the historical "4/4" can be
reproduced without being mistaken for forecast skill.
"""

from __future__ import annotations

import json

from .forecast_feature import (
    as_of_cross_section,
    intensity_of,
    issue_time,
    latest_by_event,
    leakage_rows,
)

SCORE_SCHEMA = "hailclaim-backtest/2"


def _normalise_cases(cases):
    for case in cases:
        if "event_id" not in case:
            raise ValueError("case without event_id: {!r}".format(case))
        if "label" not in case:
            raise ValueError("case {} has no label".format(case["event_id"]))
    return list(cases)


def score_legacy(cases, features):
    """Old diagnostic join — reproduces the leaked 4/4, never a publish path."""

    cases = _normalise_cases(cases)
    joined = latest_by_event(features)
    predictions = {
        row["event_id"]: int(intensity_of(joined.get(row["event_id"])) > 0)
        for row in cases
    }
    return {
        "correct": _correct(cases, predictions),
        "total": len(cases),
        "predictions": predictions,
    }


def _correct(cases, predictions):
    return sum(predictions[row["event_id"]] == row["label"] for row in cases)


def score(cases, features, as_of=None):
    """Point-in-time score.

    ``as_of`` can pin every case to one issue time; otherwise each case uses
    its own ``issued_at`` and a case without one raises.
    """

    cases = _normalise_cases(cases)
    section = as_of_cross_section(cases, features, as_of)
    predictions = section["predictions"]
    return {
        "correct": _correct(cases, predictions),
        "total": len(cases),
        "predictions": predictions,
        "as_of": section["as_of"],
        "feature_ids": section["feature_ids"],
        "excluded_feature_ids": section["excluded_feature_ids"],
        "leakage": section["leakage"],
    }


# Explicit alias for callers that want the name to say what it guarantees.
score_as_of = score


def score_rows(cases, features, as_of=None):
    """Per-case detail used by the report and tests."""

    cases = _normalise_cases(cases)
    section = as_of_cross_section(cases, features, as_of)
    rows = []
    for case in sorted(cases, key=lambda item: str(item["event_id"])):
        event_id = case["event_id"]
        row = section["rows"].get(event_id)
        prediction = section["predictions"][event_id]
        rows.append(
            {
                "event_id": event_id,
                "issued_at": section["as_of"][event_id],
                "feature_id": section["feature_ids"][event_id],
                "feature_intensity": intensity_of(row) if row is not None else None,
                "prediction": prediction,
                "label": case["label"],
                "correct": prediction == case["label"],
            }
        )
    return rows


def leakage_report(cases, features, as_of=None):
    return leakage_rows(_normalise_cases(cases), features, as_of)


def model_identity(model):
    """Stable, human-readable identity for a model version descriptor."""

    canonical = json.dumps(model, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    version = model.get("version", "unversioned") if isinstance(model, dict) else "unversioned"
    return "{}:{}".format(version, canonical)


def compare_versions(cases, features, models, as_of=None, split="test"):
    """Score a list of model descriptors against one frozen feature set.

    Every model is scored on the same cases and the same point-in-time join, so
    the comparison is reproducible from the source pages alone.
    """

    cases = _normalise_cases(cases)
    results = []
    for model in models:
        if model.get("status") != "published":
            continue
        model_ids = set(model.get("feature_ids", []))
        scoped = [
            row for row in features if not model_ids or row.get("feature_id", row.get("id")) in model_ids
        ]
        scored = score(cases, scoped, as_of)
        results.append(
            {
                "model": model.get("version"),
                "identity": model_identity(model),
                "status": model.get("status"),
                "split": split,
                "correct": scored["correct"],
                "total": scored["total"],
                "predictions": scored["predictions"],
                "excluded_feature_ids": scored["excluded_feature_ids"],
            }
        )
    return sorted(results, key=lambda item: str(item["model"]))


def deployability(score_result, model, threshold):
    """Decide whether a model may be released for the 09:00 warning at all."""

    passed = score_result["correct"] >= threshold
    if not passed:
        return {
            "version": model.get("version"),
            "status": "withheld",
            "releasable_as_of_09": False,
            "reason": "below_threshold",
            "threshold": threshold,
            "score": "{}/{}".format(score_result["correct"], score_result["total"]),
            "scope": model.get("scope"),
        }
    return {
        "version": model.get("version"),
        "status": "published",
        "releasable_as_of_09": True,
        "reason": "threshold_met",
        "threshold": threshold,
        "score": "{}/{}".format(score_result["correct"], score_result["total"]),
        "scope": model.get("scope"),
    }


def evaluate(models, cases, features, threshold, as_of=None):
    """Full backtest bundle: point-in-time section, leakage list, model results.

    ``models`` are descriptors from ``fixtures/forecast-asof.json``; each one
    names the feature pages it is allowed to see (its frozen snapshot pool), so
    a model trained on a leaked pool can never appear to have scored well on
    the as-of join.
    """

    cases = _normalise_cases(cases)
    section = as_of_cross_section(cases, features, as_of)
    models_by_version = {
        model.get("version"): model for model in models if isinstance(model, dict)
    }
    results = []
    for version, model in sorted(models_by_version.items(), key=lambda item: str(item[0])):
        if model.get("status") != "published":
            continue
        allowed = set(model.get("feature_ids", []))
        scoped = [
            row for row in features if not allowed or row.get("feature_id", row.get("id")) in allowed
        ]
        scoped_score = score(cases, scoped, as_of)
        results.append(deployability(scoped_score, model, threshold))
    the_release = next((item for item in results if item["releasable_as_of_09"]), None)
    return {
        "schema": SCORE_SCHEMA,
        "corpus": {
            "cases": len(cases),
            "features": len(features),
            "event_ids": sorted(str(case["event_id"]) for case in cases),
        },
        "cross_section": {
            "as_of": section["as_of"],
            "predictions": section["predictions"],
            "feature_ids": section["feature_ids"],
            "visible_feature_ids": section["visible_feature_ids"],
            "excluded_feature_ids": section["excluded_feature_ids"],
        },
        "leakage": section["leakage"],
        "models": results,
        "release": the_release,
    }
