"""Provisional feature join without point-in-time availability."""


def latest_by_event(features):
    joined = {}
    for row in features:
        event_id = row["event_id"]
        if event_id not in joined or row["received_at"] > joined[event_id]["received_at"]:
            joined[event_id] = row
    return joined
