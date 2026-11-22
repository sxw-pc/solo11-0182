from pathlib import Path
COMMAND='policy'
KINDS=['policy', 'plot', 'endorsement']
FAILURE='fixtures/policy_late.json'
NORMAL=str(Path(__file__).resolve().parent.parent / 'fixtures/policy_ok.json')

def allof(events,kind): return [e for e in events if e["kind"]==kind]
def latest(events,kind):
    rows=allof(events,kind)
    return max(rows,key=lambda e:(e["event_time"],e["received_at"],e["id"])) if rows else None

def decide(ev):
    result=[]
    now=max([e["event_time"] for e in ev],default=0)
    def out(name,value,proof):
        sources=[x["id"] for x in proof if x]
        result.append((name,value,sources if sources else [x["id"] for x in ev]))
    event=latest(ev,'hail_event'); eligible_policies=[x for x in allof(ev,'policy') if event and x['event_time']<=event['event_time']]; policy=max(eligible_policies,key=lambda x:(x['event_time'],x['received_at'],x['id'])) if eligible_policies else None; covered=bool(policy and policy['data'].get('active',True)); out('承保责任时点锁定','在保' if covered else '不在保',[policy,event])
    plots=allof(ev,'plot'); area=sum(float(x['data'].get('area',0)) for x in plots); ids=[x['data'].get('parcel') for x in plots]; unique=len(ids)==len(set(ids)); out('地块边界重叠裁决',area if unique else 0,plots)
    rate=float(policy['data'].get('rate',0)) if policy else 0; out('品种与保障单价适配',rate,[policy]+plots)
    event=latest(ev,'hail_event')
    footprint=set(event['data'].get('parcels',[])) if event else set()
    hit=[x for x in plots if x['data'].get('parcel') in footprint]
    samples=allof(ev,'sample_observation')
    valid=[x for x in samples if x['data'].get('parcel') in footprint and 0<=float(x['data'].get('loss',-1))<=1]
    conflict=len({x['data'].get('sample') for x in valid})!=len(valid)
    disease=[x for x in valid if x['data'].get('disease')]
    harvest=latest(ev,'harvest')
    residual=float(harvest['data'].get('residual',0)) if harvest else 0
    loss=sum(float(x['data'].get('loss',0)) for x in valid)/max(1,len(valid))
    gross=max(0,area*rate*loss-residual) if covered and not conflict and unique else 0
    claim=latest(ev,'claim')
    deductible=float(claim['data'].get('deductible',0)) if claim else 0
    limit=float(policy['data'].get('limit',1e12)) if policy else 0
    payable=round(min(limit,max(0,gross-deductible)),2)
    appeal=latest(ev,'appeal')
    paid=float(claim['data'].get('paid',0)) if claim else 0
    return result
