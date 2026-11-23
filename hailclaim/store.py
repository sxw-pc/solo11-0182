ENTITIES=['policy', 'plot', 'endorsement', 'hail_event', 'sample_plot', 'sample_observation', 'harvest', 'claim', 'appeal', 'payment']
# Supplementary read-only pages used by the forecast backtest.  They never feed
# the three claim-decision modules.
FORECAST_KINDS=['station_feature', 'radar_feature', 'feature_page']
import json, sqlite3
from pathlib import Path

# Columns added after the first schema; created on demand so an existing
# evidence table keeps working (ALTER TABLE ... ADD COLUMN is nullable).
EXTRA_COLUMNS=(
    ('corrected_at','INTEGER'),
    ('clock_corrected_at','INTEGER'),
    ('issued_at','INTEGER'),
)

def connect(path):
    db=sqlite3.connect(path)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('CREATE TABLE IF NOT EXISTS evidence (id TEXT PRIMARY KEY, kind TEXT NOT NULL, subject TEXT NOT NULL, event_time INTEGER NOT NULL, received_at INTEGER NOT NULL, payload TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS decision (subject TEXT NOT NULL, surface TEXT NOT NULL, version INTEGER NOT NULL, result TEXT NOT NULL, evidence_ids TEXT NOT NULL, PRIMARY KEY(subject,surface,version))')
    db.execute('CREATE TABLE IF NOT EXISTS rejection (id TEXT PRIMARY KEY, reason TEXT NOT NULL, payload TEXT NOT NULL)')
    existing={r['name'] for r in db.execute('PRAGMA table_info(evidence)')}
    for name,sql_type in EXTRA_COLUMNS:
        if name not in existing: db.execute(f'ALTER TABLE evidence ADD COLUMN {name} {sql_type}')
    for kind in ENTITIES:
        db.execute(f'CREATE TABLE IF NOT EXISTS {kind} (id TEXT PRIMARY KEY REFERENCES evidence(id), subject TEXT NOT NULL, event_time INTEGER NOT NULL, payload TEXT NOT NULL)')
    for kind in FORECAST_KINDS:
        db.execute(f'CREATE TABLE IF NOT EXISTS {kind} (id TEXT PRIMARY KEY REFERENCES evidence(id), subject TEXT NOT NULL, event_time INTEGER NOT NULL, received_at INTEGER NOT NULL, payload TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS forecast_snapshot (name TEXT PRIMARY KEY, purpose TEXT NOT NULL, created_at INTEGER NOT NULL, payload TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS forecast_release (version TEXT PRIMARY KEY, status TEXT NOT NULL, payload TEXT NOT NULL)')
    db.commit()
    return db

def _availability(event):
    data=event.get('data',{}) if isinstance(event.get('data'),dict) else {}
    def pick(name):
        value=event.get(name, data.get(name))
        return None if value is None else int(value)
    corrected=pick('corrected_at')
    if corrected is None: corrected=pick('correction_at')
    if corrected is None: corrected=pick('correction_published_at')
    clock=pick('clock_corrected_at')
    if clock is None: clock=pick('event_time_corrected_at')
    if clock is None: clock=pick('clock_revised_at')
    return corrected,clock,pick('issued_at')

def ingest(db, event):
    for key in ('id','kind','subject','event_time','received_at'):
        if key not in event: raise ValueError('missing '+key)
    if event['kind'] not in ENTITIES and event['kind'] not in FORECAST_KINDS: raise ValueError('unknown kind')
    payload=json.dumps(event.get('data',{}),ensure_ascii=False,sort_keys=True)
    old=db.execute('SELECT kind,subject,event_time,received_at,payload FROM evidence WHERE id=?',(event['id'],)).fetchone()
    current=(event['kind'],event['subject'],int(event['event_time']),int(event['received_at']),payload)
    if old:
        if tuple(old)==current: return False
        raise ValueError('conflicting evidence id '+event['id'])
    corrected,clock,issued=_availability(event)
    db.execute('INSERT INTO evidence (id,kind,subject,event_time,received_at,payload,corrected_at,clock_corrected_at,issued_at) VALUES (?,?,?,?,?,?,?,?,?)',(event['id'],*current,corrected,clock,issued))
    if event['kind'] in ENTITIES:
        db.execute(f'INSERT INTO {event["kind"]} VALUES (?,?,?,?)',(event['id'],event['subject'],int(event['event_time']),payload))
    else:
        db.execute(f'INSERT INTO {event["kind"]} VALUES (?,?,?,?,?)',(event['id'],event['subject'],int(event['event_time']),int(event['received_at']),payload))
    return True

def _record(row):
    data=json.loads(row['payload'])
    record=dict(id=row['id'],kind=row['kind'],subject=row['subject'],event_time=row['event_time'],received_at=row['received_at'],data=data)
    for name in ('corrected_at','clock_corrected_at','issued_at'):
        if row[name] is not None: record[name]=row[name]
    for name in ('corrected_at','clock_corrected_at','issued_at'):
        if name in record: data.setdefault(name,record[name])
    return record

def evidence(db,subject):
    return [_record(r) for r in db.execute('SELECT * FROM evidence WHERE subject=? ORDER BY event_time,id',(subject,))]

def forecast_features(db,subject):
    """All opaque feature pages recorded for a subject, in stable order."""
    return [_record(r) for r in db.execute('SELECT * FROM evidence WHERE subject=? AND kind IN (%s) ORDER BY event_time,id'%','.join('?'*len(FORECAST_KINDS)),(subject,*FORECAST_KINDS))]

def latest_disclosure(db, subject, event_id=None, kind=None):
    """Newest disclosure revision for an event (and optionally a page kind).

    A re-issued page with a later gateway arrival (late page, clock revision,
    correction) supersedes the earlier one for *research*; it does not make the
    earlier value visible any sooner.
    """
    candidates=[]
    for row in db.execute('SELECT * FROM evidence WHERE subject=? ORDER BY received_at,event_time,id',(subject,)):
        record=_record(row)
        if kind is not None and record['kind']!=kind: continue
        if event_id is not None and record['data'].get('event_id')!=event_id: continue
        candidates.append(record)
    return candidates[-1] if candidates else None

def first_visible_time(record_or_row):
    """Earliest instant a stored page is fully readable (event, arrival, revision)."""
    from .forecast_feature import earliest_visible
    return earliest_visible(record_or_row)

def as_of_snapshot(db, subject, as_of):
    """Pages that were fully readable at ``as_of`` and the ones that were not."""
    from .forecast_feature import earliest_visible, row_visible
    visible=[]; excluded=[]
    for record in forecast_features(db,subject):
        item=dict(event_id=record['data'].get('event_id'),feature_id=record['id'],source=record['kind'],
                  event_time=record['event_time'],received_at=record['received_at'],
                  corrected_at=record['data'].get('corrected_at'),
                  clock_revised_at=record['data'].get('clock_corrected_at') or record['data'].get('event_time_corrected_at'))
        item['earliest_visible']=earliest_visible(record)
        if row_visible(record,as_of):
            visible.append(item)
        else:
            item['reasons']=[r for r in _reasons(record,as_of)]
            excluded.append(item)
    return dict(visible=visible,excluded=excluded)

def _reasons(record,as_of):
    from .forecast_feature import visibility_issues
    return visibility_issues(record,as_of)

def save_forecast_snapshot(db, name, purpose, pages, created_at, extra=None):
    """Persist a training/backtest snapshot exactly as it was assembled.

    ``pages`` must already be rendered through the point-in-time join; the
    snapshot stores each page's identity and earliest visible time so a
    reusable training set can be checked for leakage on its own.
    """
    from .forecast_feature import earliest_visible, feature_id
    materialized=[]
    for page in pages:
        materialized.append(dict(feature_id=feature_id(page),event_id=page.get('event_id'),
                                 source=page.get('source') or page.get('kind'),
                                 event_time=page.get('event_time'),received_at=page.get('received_at'),
                                 earliest_visible=earliest_visible(page),payload=page.get('data',{})))
    materialized.sort(key=lambda item:(str(item['event_id']),item['feature_id']))
    payload=json.dumps(dict(name=name,purpose=purpose,created_at=int(created_at),pages=materialized,extra=extra or {}),ensure_ascii=False,sort_keys=True)
    db.execute('INSERT OR REPLACE INTO forecast_snapshot VALUES (?,?,?,?)',(name,purpose,int(created_at),payload))
    return json.loads(payload)

def load_forecast_snapshot(db, name):
    row=db.execute('SELECT payload FROM forecast_snapshot WHERE name=?',(name,)).fetchone()
    if not row: raise KeyError('unknown forecast snapshot '+name)
    return json.loads(row['payload'])

def leaked_snapshots(db):
    """Snapshots containing any page not visible at its own recorded issue time."""
    leaked={}
    for row in db.execute('SELECT name,purpose,payload FROM forecast_snapshot ORDER BY name'):
        snapshot=json.loads(row['payload'])
        bad=[]
        for page in snapshot['pages']:
            issued=snapshot.get('extra',{}).get('issued_at',{}).get(str(page['event_id']))
            if issued is None:
                continue
            visible=page.get('earliest_visible')
            if visible is None or visible>issued:
                bad.append(page['feature_id'])
        if bad: leaked[row['name']]=sorted(bad)
    return leaked

def save_forecast_release(db, version, status, payload):
    db.execute('INSERT OR REPLACE INTO forecast_release VALUES (?,?,?)',(version,status,json.dumps(payload,ensure_ascii=False,sort_keys=True)))

def forecast_releases(db):
    return {r['version']:dict(status=r['status'],payload=json.loads(r['payload'])) for r in db.execute('SELECT * FROM forecast_release ORDER BY version')}

def save_decisions(db,subject,results):
    for surface,result,ids in results:
        value=json.dumps(result,ensure_ascii=False,sort_keys=True)
        proof=json.dumps(sorted(set(ids)),ensure_ascii=False)
        old=db.execute('SELECT version,result,evidence_ids FROM decision WHERE subject=? AND surface=? ORDER BY version DESC LIMIT 1',(subject,surface)).fetchone()
        if old and old['result']==value and old['evidence_ids']==proof: continue
        version=1 if old is None else old['version']+1
        db.execute('INSERT INTO decision VALUES (?,?,?,?,?)',(subject,surface,version,value,proof))

def report(db,subject):
    rows=db.execute('SELECT d.* FROM decision d JOIN (SELECT surface,MAX(version) v FROM decision WHERE subject=? GROUP BY surface) x ON d.surface=x.surface AND d.version=x.v WHERE d.subject=? ORDER BY d.surface',(subject,subject)).fetchall()
    return {r['surface']:dict(version=r['version'],result=json.loads(r['result']),evidence_ids=json.loads(r['evidence_ids'])) for r in rows}
