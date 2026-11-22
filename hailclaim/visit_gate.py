"""Check provisional assignments against signed physical checkins."""


def assess_reassignment(assignments, jobs, checkins, travel_minutes):
    by_id = {job["id"]: job for job in jobs}
    conflicts = []
    for checked_job_id, checkin in checkins.items():
        worker_id = checkin["worker_id"]
        if assignments.get(checked_job_id) != worker_id:
            conflicts.append({"job_id": checked_job_id, "reason": "moved-after-checkin"})
        first = by_id[checked_job_id]
        first_end = checkin["at_minute"] + first["duration_minutes"]
        for later in jobs:
            if later["id"] == checked_job_id or assignments.get(later["id"]) != worker_id:
                continue
            drive = travel_minutes[(first["site"], later["site"])]
            earliest = first_end + drive
            if earliest > later["start_minute"]:
                conflicts.append({"job_id": later["id"], "reason": "late-start",
                                  "earliest_minute": earliest})
    return conflicts
