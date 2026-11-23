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


# ---------------------------------------------------------------------------
# Executable roster planner.
#
# The provisional planner above only matches skills.  The roster below works
# on the event-time graph of offline sources (certificates, shift calendars,
# road legs/closures, appointment ICS, QR checkins) and produces an ordered,
# executable itinerary per adjuster.  Two kinds of nodes are distinguished:
#
# * appointments  - not-yet-started, exchangeable candidates;
# * checkins      - already-started physical work, non-transferable and never
#                   erased from history.
# ---------------------------------------------------------------------------

def _closed(closures, from_site, to_site, depart_minute):
    return any(c['from_site'] == from_site and c['to_site'] == to_site
               and c['effective_from'] <= depart_minute for c in closures or [])


def _cert_proof(certificates, revocations, worker_id, skill, at_minute):
    """Qualification must be valid at the service moment itself."""
    proof = None
    for cert in certificates or []:
        if (cert['worker_id'] == worker_id and cert['skill'] == skill
                and cert['valid_from'] <= at_minute <= cert['valid_until']):
            proof = cert
    if proof is None:
        return None
    for rev in revocations or []:
        if (rev['worker_id'] == worker_id and rev['skill'] == skill
                and rev['effective_from'] <= at_minute):
            return None
    return proof


def _proof_json(proof):
    if proof is None:
        return None
    return {'id': proof['cert_id'], 'skill': proof['skill'],
            'valid_from': proof['valid_from'], 'valid_until': proof['valid_until']}


def _simulate(worker_id, stops, ctx):
    """Walk one worker's ordered stops.  Returns (itinerary, problems);
    itinerary is None when an un-started stop is infeasible.  Locked
    (checked-in) stops are physical history: kept as-is, problems recorded."""
    cal = ctx['calendars'].get(worker_id, {})
    site = ctx['wby'][worker_id].get('station') or cal.get('station')
    free = cal.get('start_minute', 0)
    lunch = cal.get('lunch')
    shift_end = cal.get('end_minute', 10 ** 9)
    out, problems = [], []
    for job, checkin in stops:
        duration = job['duration_minutes']
        if checkin is not None:
            start = max(checkin['at_minute'], job['window_start'])
            end = start + duration
            leg_min = 0 if site == job['site'] else ctx['travel_minutes'].get((site, job['site']))
            proof = _cert_proof(ctx['certificates'], ctx['revocations'],
                                worker_id, job['skill'], start)
            out.append({'job_id': job['id'], 'worker_id': worker_id, 'site': job['site'],
                        'start_minute': start, 'end_minute': end, 'locked': True,
                        'leg': {'from_site': site, 'to_site': job['site'],
                                'minutes': leg_min, 'historical': True},
                        'cert': _proof_json(proof),
                        'checkin': {'worker_id': checkin['worker_id'],
                                    'at_minute': checkin['at_minute'],
                                    'qr': checkin.get('qr')}})
            if end > job['window_end']:
                problems.append({'job_id': job['id'], 'reason': 'window-violation',
                                 'worker_id': worker_id, 'end_minute': end,
                                 'window_end': job['window_end']})
            if proof is None:
                problems.append({'job_id': job['id'], 'reason': 'cert-invalid-at-service',
                                 'worker_id': worker_id, 'skill': job['skill']})
            site, free = job['site'], end
            continue
        leg_min = 0 if site == job['site'] else ctx['travel_minutes'].get((site, job['site']))
        if leg_min is None:
            return None, problems
        start = max(free + leg_min, job['window_start'], ctx['now'] + leg_min)
        if lunch and start < lunch[1] and start + duration > lunch[0]:
            start = lunch[1]
        end = start + duration
        if start < cal.get('start_minute', 0) or end > shift_end or end > job['window_end']:
            return None, problems
        if _closed(ctx['closures'], site, job['site'], start - leg_min):
            return None, problems
        proof = _cert_proof(ctx['certificates'], ctx['revocations'],
                            worker_id, job['skill'], start)
        if proof is None:
            return None, problems
        out.append({'job_id': job['id'], 'worker_id': worker_id, 'site': job['site'],
                    'start_minute': start, 'end_minute': end, 'locked': False,
                    'leg': {'from_site': site, 'to_site': job['site'], 'minutes': leg_min,
                            'depart_minute': start - leg_min},
                    'cert': _proof_json(proof)})
        site, free = job['site'], end
    return out, problems


def _locked_position(worker_id, job, ctx, locked, checkins):
    """Where is this worker (and since when) considering only physical checkins
    that started no later than the given job's window."""
    cal = ctx['calendars'].get(worker_id, {})
    site = ctx['wby'][worker_id].get('station') or cal.get('station')
    free = cal.get('start_minute', 0)
    last = None
    for other in ctx['jobs_sorted']:
        if other['window_start'] > job['window_start']:
            continue
        if locked.get(other['id']) != worker_id:
            continue
        checkin = checkins[other['id']]
        free = max(checkin['at_minute'], other['window_start']) + other['duration_minutes']
        site, last = other['site'], other['id']
    return site, free, last


def _certificate(job, ctx, locked, checkins):
    """Infeasibility certificate for an uncoverable window: the minimal set of
    blocking atoms (one per candidate worker) plus a reschedule hint."""
    atoms, candidates = [], []
    ws, we, duration = job['window_start'], job['window_end'], job['duration_minutes']
    for worker in ctx['workers']:
        wid = worker['id']
        records = [c for c in ctx['certificates']
                   if c['worker_id'] == wid and c['skill'] == job['skill']]
        if not records:
            atoms.append({'worker_id': wid, 'reason': 'skill-missing', 'skill': job['skill']})
            continue
        latest = we - duration
        if not any(_cert_proof(ctx['certificates'], ctx['revocations'], wid, job['skill'], t)
                   for t in range(ws, latest + 1)):
            revs = [r for r in ctx['revocations']
                    if r['worker_id'] == wid and r['skill'] == job['skill']
                    and r['effective_from'] <= latest]
            if revs:
                atoms.append({'worker_id': wid, 'reason': 'cert-revoked', 'skill': job['skill'],
                              'revoked_at': min(r['effective_from'] for r in revs)})
            else:
                atoms.append({'worker_id': wid, 'reason': 'cert-expired', 'skill': job['skill']})
            continue
        site, free, last = _locked_position(wid, job, ctx, locked, checkins)
        leg_min = 0 if site == job['site'] else ctx['travel_minutes'].get((site, job['site']))
        if leg_min is None:
            atoms.append({'worker_id': wid, 'reason': 'no-route',
                          'from_site': site, 'to_site': job['site']})
            continue
        lunch = ctx['calendars'].get(wid, {}).get('lunch')
        start = max(free + leg_min, ws, ctx['now'] + leg_min)
        if lunch and start < lunch[1] and start + duration > lunch[0]:
            start = lunch[1]
        closed = [c for c in ctx['closures']
                  if c['from_site'] == site and c['to_site'] == job['site']
                  and c['effective_from'] <= start - leg_min]
        if closed:
            atoms.append({'worker_id': wid, 'reason': 'road-closed',
                          'from_site': site, 'to_site': job['site'],
                          'closed_from': min(c['effective_from'] for c in closed)})
            continue
        if start + duration > we:
            atom = {'worker_id': wid,
                    'reason': 'locked-to-job' if last else 'window-too-tight',
                    'earliest_minute': start}
            if last:
                atom['locked_job'] = last
            atoms.append(atom)
            if _cert_proof(ctx['certificates'], ctx['revocations'], wid, job['skill'], start):
                candidates.append((start, wid))
            continue
        atoms.append({'worker_id': wid, 'reason': 'assigned-elsewhere'})
    reschedule = None
    if candidates:
        start, wid = min(candidates)
        reschedule = {'worker_id': wid, 'earliest_minute': start}
    return {'job_id': job['id'], 'site': job['site'], 'skill': job['skill'],
            'window': [ws, we], 'conflict_set': atoms, 'reschedule': reschedule}


def plan_roster(jobs, workers, travel_minutes, checkins=None, calendars=None,
                certificates=None, revocations=None, closures=None, now_minute=None,
                provisional=None, overtime_minutes=60):
    """Build an executable dispatch table.

    Checked-in jobs are locked to the signed worker (non-transferable); every
    other appointment is re-plannable.  Coverage is maximised first, then
    total travel plus cross-station overtime is minimised, deterministically.
    """
    jobs_sorted = sorted(jobs, key=lambda j: (j['window_start'], j['id']))
    by_id = {j['id']: j for j in jobs_sorted}
    workers = sorted(workers, key=lambda w: w['id'])
    wby = {w['id']: w for w in workers}
    checkins = checkins or {}
    calendars = calendars or {}
    certificates = certificates or []
    revocations = revocations or []
    closures = closures or []
    now = 0 if now_minute is None else now_minute
    violations, locked = [], {}
    for job_id in sorted(checkins):
        checkin = checkins[job_id]
        if checkin.get('mode', 'onsite') != 'onsite':
            violations.append({'job_id': job_id, 'reason': 'remote-checkin-rejected',
                               'worker_id': checkin.get('worker_id')})
            continue
        if checkin['at_minute'] > now:
            violations.append({'job_id': job_id, 'reason': 'future-checkin',
                               'worker_id': checkin.get('worker_id'),
                               'at_minute': checkin['at_minute']})
            continue
        locked[job_id] = checkin['worker_id']
    ctx = {'jobs_sorted': jobs_sorted, 'workers': workers, 'wby': wby,
           'travel_minutes': travel_minutes, 'calendars': calendars,
           'certificates': certificates, 'revocations': revocations,
           'closures': closures, 'now': now}
    best = {'key': None, 'assign': None, 'per': None}

    def search(index, assign):
        if index == len(jobs_sorted):
            per = {}
            for job in jobs_sorted:
                wid = assign[job['id']]
                lock = locked.get(job['id'])
                if lock is not None and wid != lock:
                    return
                if wid is not None:
                    per.setdefault(wid, []).append(
                        (job, checkins.get(job['id']) if lock == wid else None))
            cost = 0
            for wid, stops in per.items():
                itinerary, _ = _simulate(wid, stops, ctx)
                if itinerary is None:
                    return
                for stop in itinerary:
                    cost += stop['leg']['minutes'] or 0
                    station = wby[wid].get('station')
                    if station and station != stop['site']:
                        cost += overtime_minutes
            covered = sum(1 for job in jobs_sorted if assign[job['id']])
            key = (covered, -cost)
            if best['key'] is None or key > best['key']:
                best.update(key=key, assign=dict(assign),
                            per={wid: list(stops) for wid, stops in per.items()})
            return
        job = jobs_sorted[index]
        for worker in workers:
            assign[job['id']] = worker['id']
            search(index + 1, assign)
        assign[job['id']] = None
        search(index + 1, assign)

    search(0, {})
    itineraries = {}
    for wid, stops in (best['per'] or {}).items():
        itinerary, problems = _simulate(wid, stops, ctx)
        itineraries[wid] = itinerary
        violations.extend(problems)
    assignments = {j: w for j, w in (best['assign'] or {}).items() if w}
    costs = []
    for wid, stops in itineraries.items():
        station = wby[wid].get('station')
        for stop in stops:
            if station and station != stop['site']:
                costs.append({'worker_id': wid, 'job_id': stop['job_id'],
                              'kind': 'cross-station-overtime',
                              'minutes': by_id[stop['job_id']]['duration_minutes']})
    uncovered = {}
    for job in jobs_sorted:
        if job['id'] not in assignments and job['id'] not in locked:
            uncovered[job['id']] = _certificate(job, ctx, locked, checkins)
    withdrawals = []
    for job_id, wid in sorted((provisional or {}).items()):
        if job_id not in by_id or locked.get(job_id) == wid:
            continue
        if assignments.get(job_id) != wid:
            withdrawals.append({'job_id': job_id, 'worker_id': wid,
                                'replacement': assignments.get(job_id),
                                'reason': 'withdrawn-before-checkin'})
    return {'now_minute': now, 'assignments': assignments, 'itineraries': itineraries,
            'uncovered': uncovered, 'withdrawals': withdrawals, 'costs': costs,
            'violations': violations, 'locked': locked,
            'provisional': dict(provisional or {})}


def inputs_from_evidence(ev):
    """Project the event-time evidence graph into planner inputs.

    QR checkins are deduplicated by their QR token: the earliest scan of a
    token is the physical fact, later identical scans are replayed no-ops and
    conflicting reuses are recorded, never silently merged."""
    jobs, certificates, revocations, closures = [], [], [], []
    calendars, travel, stations, externals = {}, {}, {}, {}
    qr_seen, duplicates = {}, []
    for event in ev:
        kind, data = event['kind'], event['data']
        if kind == 'appointment':
            jobs.append({'id': data['job_id'], 'skill': data['skill'], 'site': data['site'],
                         'window_start': data['window_start'], 'window_end': data['window_end'],
                         'duration_minutes': data['duration_minutes']})
        elif kind == 'adjuster_cert':
            certificates.append({'worker_id': data['worker_id'], 'skill': data['skill'],
                                 'valid_from': data.get('valid_from', 0),
                                 'valid_until': data.get('valid_until', 10 ** 9),
                                 'cert_id': event['id']})
            stations.setdefault(data['worker_id'], data.get('station'))
            externals[data['worker_id']] = (externals.get(data['worker_id'], False)
                                            or bool(data.get('external')))
        elif kind == 'cert_revocation':
            revocations.append({'worker_id': data['worker_id'], 'skill': data['skill'],
                                'effective_from': event['event_time']})
        elif kind == 'shift_calendar':
            calendars[data['worker_id']] = {
                'start_minute': data.get('start_minute', 0),
                'end_minute': data.get('end_minute', 10 ** 9),
                'lunch': (data['lunch_start'], data['lunch_end']) if 'lunch_start' in data else None,
                'station': data.get('station')}
            if data.get('station'):
                stations[data['worker_id']] = data['station']
            externals[data['worker_id']] = (externals.get(data['worker_id'], False)
                                            or bool(data.get('external')))
        elif kind == 'road_leg':
            travel[(data['from_site'], data['to_site'])] = data['minutes']
        elif kind == 'road_closure':
            closures.append({'from_site': data['from_site'], 'to_site': data['to_site'],
                             'effective_from': event['event_time']})
        elif kind == 'checkin':
            record = {'job_id': data['job_id'], 'worker_id': data['worker_id'],
                      'at_minute': data.get('at_minute', event['event_time']),
                      'site': data.get('site'), 'mode': data.get('mode', 'onsite'),
                      'qr': data.get('qr') or event['id'], 'event_id': event['id']}
            first = qr_seen.get(record['qr'])
            if first is not None:
                same = (first['worker_id'], first['job_id']) == (record['worker_id'], record['job_id'])
                duplicates.append({'qr': record['qr'], 'kept': first['event_id'],
                                   'dropped': record['event_id'],
                                   'reason': 'duplicate-scan' if same else 'conflicting-qr'})
            else:
                qr_seen[record['qr']] = record
    checkins = {}
    for record in qr_seen.values():
        old = checkins.get(record['job_id'])
        if old is None or (record['at_minute'], record['event_id']) < (old['at_minute'], old['event_id']):
            checkins[record['job_id']] = record
    worker_ids = sorted({c['worker_id'] for c in certificates} | set(calendars))
    workers = [{'id': wid,
                'skills': sorted({c['skill'] for c in certificates if c['worker_id'] == wid}),
                'station': stations.get(wid), 'external': externals.get(wid, False)}
               for wid in worker_ids]
    return {'jobs': jobs, 'workers': workers, 'travel_minutes': travel,
            'checkins': checkins, 'calendars': calendars, 'certificates': certificates,
            'revocations': revocations, 'closures': closures, 'duplicates': duplicates}


def plan_from_evidence(ev, now_minute, provisional=None, overtime_minutes=60):
    inputs = inputs_from_evidence(ev)
    duplicates = inputs.pop('duplicates')
    roster = plan_roster(now_minute=now_minute, provisional=provisional,
                         overtime_minutes=overtime_minutes, **inputs)
    roster['duplicates'] = duplicates
    return roster
