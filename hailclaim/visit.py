"""Provisional first-fit survey visit allocation."""


def assign_first_fit(jobs, adjusters):
    free = {worker["id"]: worker for worker in adjusters}
    assigned = {}
    for job in jobs:
        for worker_id, worker in list(free.items()):
            if job["skill"] in worker["skills"]:
                assigned[job["id"]] = worker_id
                del free[worker_id]
                break
    return assigned


def reassign_after_checkin(jobs, adjusters, checkins):
    """Maximize skill matches; provisional planner ignores signed checkins."""
    best = {}

    def search(index, used, assigned):
        nonlocal best
        if index == len(jobs):
            if len(assigned) > len(best):
                best = dict(assigned)
            return
        job = jobs[index]
        search(index + 1, used, assigned)
        for worker in adjusters:
            if worker["id"] not in used and job["skill"] in worker["skills"]:
                assigned[job["id"]] = worker["id"]
                search(index + 1, used | {worker["id"]}, assigned)
                del assigned[job["id"]]

    search(0, set(), {})
    return best
