"""Score hail predictions from the joined feature rows."""

from .forecast_feature import latest_by_event


def score(cases, features):
    joined = latest_by_event(features)
    predictions = {row["event_id"]: int(joined.get(row["event_id"], {}).get("intensity", 0) > 0)
                   for row in cases}
    correct = sum(predictions[row["event_id"]] == row["label"] for row in cases)
    return {"correct": correct, "total": len(cases), "predictions": predictions}
