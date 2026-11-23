"""Point-in-time (as-of) feature joins for the 09:00 hail forecast.

A feature page is usable as a model input at an issue time only when the whole
page was readable then.  A physical observation time (``event_time``) that is
before the issue time is not enough: the page also has a gateway arrival time
(``received_at``) and may carry a correction publication time or a clock
revision publication time.  The earliest moment at which the complete page is
visible is therefore the maximum of all of those timestamps.

The old ``latest_by_event`` helper ignored that and simply picked the row with
the newest ``received_at`` per event.  That is retained explicitly as the
"legacy diagnostic" path (``as_of is None``), and is never used by the default
backtest path.
"""

from __future__ import annotations

import math

# Timestamps that may make a page readable.  ``event_time``/``sample_time`` is
# the physical observation instant, ``received_at`` the gateway arrival,
# correction fields the public correction instant and clock revision fields the
# instant at which a revised event clock became public.
EVENT_TIME_FIELDS = ("event_time", "sample_time", "observation_time")
ARRIVAL_FIELDS = ("received_at", "gateway_at", "arrived_at")
CORRECTION_FIELDS = (
    "corrected_at",
    "correction_at",
    "correction_published_at",
    "published_at",
)
CLOCK_REVISION_FIELDS = (
    "event_time_revised_at",
    "event_time_corrected_at",
    "clock_revised_at",
    "revision_published_at",
)

_MISSING = object()


def _first(row, fields, default=None):
    """Read the first present field from the row or its ``data`` payload."""

    if not isinstance(row, dict):
        return default
    data = row.get("data") if isinstance(row.get("data"), dict) else {}
    for field in fields:
        if field in row and row[field] is not None:
            return row[field]
        if field in data and data[field] is not None:
            return data[field]
    return default


def _as_int(value):
    if value is None or value is _MISSING:
        return None
    if isinstance(value, bool):
        return int(value)
    try:
        if isinstance(value, float) and math.isnan(value):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def feature_id(row):
    """Stable identity for a feature page."""

    if not isinstance(row, dict):
        return str(row)
    for field in ("id", "page_id", "feature_id", "evidence_id"):
        if row.get(field) is not None:
            return str(row[field])
    return "{}@{}:{}:{}".format(
        row.get("event_id", "?"),
        source_of(row),
        event_time(row),
        received_at(row),
    )


def source_of(row):
    if not isinstance(row, dict):
        return "unknown"
    value = _first(row, ("source", "kind", "page_type"), "unknown")
    return str(value)


def event_time(row):
    return _as_int(_first(row, EVENT_TIME_FIELDS))


def received_at(row):
    return _as_int(_first(row, ARRIVAL_FIELDS))


def correction_time(row):
    return _as_int(_first(row, CORRECTION_FIELDS))


def clock_revision_time(row):
    return _as_int(_first(row, CLOCK_REVISION_FIELDS))


def intensity_of(row):
    if not isinstance(row, dict):
        return 0.0
    value = _first(row, ("intensity", "hail_intensity", "value"), 0.0)
    return _as_float(value)


def earliest_visible(row):
    """Earliest instant at which the entire page is readable.

    Returns ``None`` when the gateway arrival time is unknown: an unknown
    arrival cannot be certified as having been available at the issue time, so
    the page is conservatively treated as never visible.
    """

    arrival = received_at(row)
    if arrival is None:
        return None
    times = [arrival]
    for getter in (event_time, correction_time, clock_revision_time):
        value = getter(row)
        if value is not None:
            times.append(value)
    return max(times)


# Descriptive aliases used by report code and external callers.
available_at = earliest_visible
earliest_visible_time = earliest_visible


def visibility_issues(row, as_of):
    """Return every reason why ``row`` was not fully visible at ``as_of``."""

    issues = []
    if received_at(row) is None:
        issues.append("missing_arrival")
    observed = event_time(row)
    if observed is not None and observed > int(as_of):
        issues.append("late_observation")
    arrival = received_at(row)
    if arrival is not None and arrival > int(as_of):
        issues.append("late_arrival")
    corrected = correction_time(row)
    if corrected is not None and corrected > int(as_of):
        issues.append("late_correction")
    revised = clock_revision_time(row)
    if revised is not None and revised > int(as_of):
        issues.append("clock_revision")
    return issues


def row_visible(row, as_of):
    if as_of is None:
        raise ValueError("as_of is required for a point-in-time visibility check")
    if int(as_of) < 0:
        return False
    return not visibility_issues(row, as_of)


def visible_at(row, as_of):
    """Predicate alias: was this page fully readable at ``as_of``?"""

    return row_visible(row, as_of)


def _record_rank(row, with_visibility):
    arrival = received_at(row)
    observed = event_time(row)
    base = (
        arrival if arrival is not None else -1,
        observed if observed is not None else -1,
        feature_id(row),
    )
    if not with_visibility:
        return base
    visible = earliest_visible(row)
    return (visible if visible is not None else -1,) + base


def latest_by_event(features, as_of=None):
    """Latest row per ``event_id``.

    With ``as_of`` supplied this is a point-in-time join: rows that were not
    fully visible by that instant are discarded before selecting the newest
    visible page.  Without ``as_of`` it reproduces the old diagnostic join that
    only ranked on ``received_at`` and therefore leaks post-issue pages.
    """

    joined = {}
    for row in features:
        event_id = row.get("event_id")
        if event_id is None:
            continue
        if as_of is None:
            rank = _record_rank(row, False)
        else:
            if not row_visible(row, as_of):
                continue
            rank = _record_rank(row, True)
        current = joined.get(event_id)
        if current is None or rank > current[0]:
            joined[event_id] = (rank, row)
    return {event_id: item[1] for event_id, item in joined.items()}


def issue_time(case, as_of=None):
    """Resolve the issue time for a case.

    A numeric/global ``as_of`` wins; otherwise the case's ``issued_at`` (or
    ``issue_time``/``as_of``) is required.  Missing issue times are an error,
    never an implicit "use everything" fallback.
    """

    if as_of is not None and not isinstance(as_of, bool):
        return int(as_of)
    if isinstance(case, dict):
        for field in ("issued_at", "issue_time", "as_of"):
            value = case.get(field)
            if value is not None:
                return int(value)
    raise ValueError("case {!r} has no issued_at/issue_time".format(case))


def as_of_join(cases, features, as_of=None):
    """Return ``{event_id: visible row or None}`` for each case."""

    joined = {}
    for case in cases:
        event_id = case["event_id"]
        at = issue_time(case, as_of)
        candidates = [
            row
            for row in features
            if row.get("event_id") == event_id and row_visible(row, at)
        ]
        joined[event_id] = (
            max(candidates, key=lambda row: _record_rank(row, True))
            if candidates
            else None
        )
    return joined


# Common spellings for external callers.
asof_join = as_of_join
join_as_of = as_of_join


def visible_feature_rows(cases, features, as_of=None):
    """Rows that are visible for their own event's issue time, sorted stably."""

    visible = []
    for case in cases:
        event_id = case["event_id"]
        at = issue_time(case, as_of)
        visible.extend(
            row
            for row in features
            if row.get("event_id") == event_id and row_visible(row, at)
        )
    return sorted(visible, key=lambda row: (str(row.get("event_id")), feature_id(row)))


def leakage_rows(cases, features, as_of=None):
    """List every candidate page that could not be used at its issue time."""

    issues = []
    for case in cases:
        event_id = case["event_id"]
        at = issue_time(case, as_of)
        for row in features:
            if row.get("event_id") != event_id:
                continue
            reasons = visibility_issues(row, at)
            if not reasons:
                continue
            issues.append(
                {
                    "event_id": event_id,
                    "feature_id": feature_id(row),
                    "source": source_of(row),
                    "issued_at": at,
                    "event_time": event_time(row),
                    "received_at": received_at(row),
                    "corrected_at": correction_time(row),
                    "clock_revised_at": clock_revision_time(row),
                    "earliest_visible": earliest_visible(row),
                    "reasons": reasons,
                }
            )
    return sorted(issues, key=lambda item: (str(item["event_id"]), item["feature_id"]))


# Alias used in the leakage report / snapshot payloads.
leakage = leakage_rows


def as_of_cross_section(cases, features, as_of=None):
    """Build the auditable 09:00 cross-section and its excluded-page list."""

    joined = as_of_join(cases, features, as_of)
    leaks = leakage_rows(cases, features, as_of)
    issue_times = {case["event_id"]: issue_time(case, as_of) for case in cases}
    visible_ids = sorted(
        feature_id(row)
        for case in cases
        for row in features
        if row.get("event_id") == case["event_id"]
        and row_visible(row, issue_times[case["event_id"]])
    )
    excluded_ids = sorted(item["feature_id"] for item in leaks)
    return {
        "as_of": issue_times,
        "rows": {event_id: row for event_id, row in sorted(joined.items())},
        "predictions": {
            event_id: int(intensity_of(row) > 0) if row is not None else 0
            for event_id, row in sorted(joined.items())
        },
        "feature_ids": {
            event_id: feature_id(row) if row is not None else None
            for event_id, row in sorted(joined.items())
        },
        "visible_feature_ids": visible_ids,
        "excluded_feature_ids": excluded_ids,
        "leakage": leaks,
    }


# Alias.
cross_section = as_of_cross_section
