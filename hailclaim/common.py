from pathlib import Path
def rows(ev,kind):return [e for e in ev if e['kind']==kind]
def latest(ev,kind):
    values=rows(ev,kind)
    return max(values,key=lambda e:(e['event_time'],e['received_at'],e['id'])) if values else None
def proof(*groups):return [e['id'] for group in groups for e in group if e]
def policy_at_event(ev):
    event=latest(ev,'hail_event')
    choices=[p for p in rows(ev,'policy') if event and p['event_time']<=event['event_time']]
    return (max(choices,key=lambda p:(p['event_time'],p['received_at'],p['id'])) if choices else None),event
def plots_and_area(ev):
    plots=rows(ev,'plot');area=0;overlap=False
    for i,p in enumerate(plots):
        d=p['data'];box=d.get('box')
        area+=max(0,(box[2]-box[0])*(box[3]-box[1])) if box else float(d.get('area',0))
        for old in plots[:i]:
            other=old['data'].get('box')
            if box and other:
                if max(box[0],other[0])<min(box[2],other[2]) and max(box[1],other[1])<min(box[3],other[3]):overlap=True
            elif d.get('parcel')==old['data'].get('parcel'):overlap=True
    return plots,area,overlap
def valid_samples(ev):
    event=latest(ev,'hail_event');samples=rows(ev,'sample_observation')
    footprint=set(event['data'].get('parcels',[])) if event else set()
    valid=[s for s in samples if event and s['data'].get('parcel') in footprint and s['data'].get('sample_time',s['event_time'])>=event['event_time'] and not s['data'].get('replanted') and 0<=float(s['data'].get('loss',-1))<=1]
    identities=[(s['data'].get('parcel'),s['data'].get('tree_id',s['data'].get('sample'))) for s in valid]
    conflict=len(identities)!=len(set(identities))
    return valid,conflict,footprint
def rate_for(policy,plots):
    if not policy:return 0
    rates=policy['data'].get('rates',{})
    if not rates:return float(policy['data'].get('rate',0))
    first=plots[0]['data'] if plots else {}
    key=f"{first.get('variety','unknown')}:{first.get('age_band','unknown')}"
    return float(rates.get(key,0))
