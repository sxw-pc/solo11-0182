"""Check provisional assignments against signed physical checkins."""

from .visit import _cert_proof, _closed


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


def audit_roster(roster, jobs, workers, travel_minutes, checkins=None, calendars=None,
                 certificates=None, revocations=None, closures=None):
    """Independently audit an executable roster.

    Replays every worker's ordered stops against the same physical sources the
    planner used and reports each violated constraint.  A tampered table -
    compressed travel, moved checkins, expired certificates, lunch or shift
    violations, double booking - cannot pass as executable."""
    by_id = {job['id']: job for job in jobs}
    wby = {worker['id']: worker for worker in workers}
    checkins = checkins or {}
    calendars = calendars or {}
    conflicts = []
    seen = {}
    for wid in sorted(roster.get('itineraries', {})):
        stops = sorted(roster['itineraries'][wid],
                       key=lambda s: (s['start_minute'], s['job_id']))
        cal = calendars.get(wid, {})
        site = wby.get(wid, {}).get('station') or cal.get('station')
        free = cal.get('start_minute', 0)
        lunch = cal.get('lunch')
        for stop in stops:
            job = by_id[stop['job_id']]
            other = seen.get(stop['job_id'])
            if other is not None and other != wid:
                conflicts.append({'job_id': stop['job_id'], 'reason': 'double-booked',
                                  'workers': sorted({other, wid})})
            seen[stop['job_id']] = wid
            checkin = checkins.get(stop['job_id'])
            if (checkin and checkin.get('mode', 'onsite') == 'onsite'
                    and checkin['worker_id'] != wid):
                conflicts.append({'job_id': stop['job_id'], 'reason': 'moved-after-checkin',
                                  'worker_id': wid, 'checked_in': checkin['worker_id']})
            if stop['end_minute'] - stop['start_minute'] != job['duration_minutes']:
                conflicts.append({'job_id': stop['job_id'], 'reason': 'duration-violation',
                                  'worker_id': wid})
            if not stop.get('locked'):
                leg = stop.get('leg') or {}
                frm = leg.get('from_site', site)
                need = 0 if frm == job['site'] else travel_minutes.get((frm, job['site']))
                if need is None:
                    conflicts.append({'job_id': stop['job_id'], 'reason': 'no-route',
                                      'worker_id': wid, 'from_site': frm,
                                      'to_site': job['site']})
                elif _closed(closures, frm, job['site'], stop['start_minute'] - need):
                    conflicts.append({'job_id': stop['job_id'], 'reason': 'road-closed',
                                      'worker_id': wid, 'from_site': frm,
                                      'to_site': job['site']})
                elif stop['start_minute'] < free + need:
                    conflicts.append({'job_id': stop['job_id'], 'reason': 'travel-compressed',
                                      'worker_id': wid, 'earliest_minute': free + need})
            if (stop['start_minute'] < job['window_start']
                    or stop['end_minute'] > job['window_end']):
                conflicts.append({'job_id': stop['job_id'], 'reason': 'window-violation',
                                  'worker_id': wid,
                                  'window': [job['window_start'], job['window_end']]})
            if lunch and stop['start_minute'] < lunch[1] and stop['end_minute'] > lunch[0]:
                conflicts.append({'job_id': stop['job_id'], 'reason': 'lunch-violation',
                                  'worker_id': wid})
            if stop['end_minute'] > cal.get('end_minute', 10 ** 9):
                conflicts.append({'job_id': stop['job_id'], 'reason': 'shift-violation',
                                  'worker_id': wid})
            if _cert_proof(certificates, revocations, wid, job['skill'],
                           stop['start_minute']) is None:
                conflicts.append({'job_id': stop['job_id'], 'reason': 'cert-invalid',
                                  'worker_id': wid, 'skill': job['skill']})
            site, free = job['site'], stop['end_minute']
    return conflicts
