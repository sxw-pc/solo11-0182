from .common import rows,latest,proof,policy_at_event,plots_and_area,valid_samples
from pathlib import Path
COMMAND='survey';KINDS=['hail_event','sample_plot','sample_observation']
NORMAL=str(Path(__file__).resolve().parent.parent/'fixtures/survey_ok.json')
FAILURE='fixtures/survey_conflict.json'
def decide(ev):
    _,event=policy_at_event(ev);plots,_,_=plots_and_area(ev)
    valid,conflict,footprint=valid_samples(ev)
    hit=[p for p in plots if p['data'].get('parcel') in footprint]
    disease=[s for s in valid if s['data'].get('disease_before_hail')]
    return [('雹灾足迹确认',len(hit),proof([event],plots) or proof(ev)),('样方抽样有效性',len(valid),proof(rows(ev,'sample_observation'),[event]) or proof(ev)),('观察冲突仲裁','待复核' if conflict else '可信',proof(valid) or proof(ev)),('次生病害归因','非雹损' if disease else '雹损',proof(disease,[event]) or proof(ev))]

def observation_sources(ev):
    """Which sample observations are backed by physical presence.

    A candidate appointment is never an arrival: an observation only becomes a
    survey source when an onsite QR checkin and the observation close at the
    same plot inside the same service window.  Remote phone-ins close nothing.
    """
    appointments={a['data'].get('job_id'):a['data'] for a in rows(ev,'appointment')}
    sources=[]
    for obs in rows(ev,'sample_observation'):
        parcel=obs['data'].get('parcel')
        sample_time=obs['data'].get('sample_time',obs['event_time'])
        for checkin in rows(ev,'checkin'):
            data=checkin['data']
            job=appointments.get(data.get('job_id'))
            if not job or data.get('mode','onsite')!='onsite':
                continue
            if (data.get('site') or job.get('site'))!=parcel:
                continue
            start=max(data.get('at_minute',checkin['event_time']),job['window_start'])
            end=start+job['duration_minutes']
            if start<=sample_time<=end:
                sources.append({'observation':obs['id'],'checkin':checkin['id'],
                                'job_id':job['job_id'],'parcel':parcel,
                                'start_minute':start,'end_minute':end})
    return sources
