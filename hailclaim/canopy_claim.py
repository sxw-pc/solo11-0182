"""Provisional claim from the visible fraction of a canopy."""
from .store import evidence
from .common import policy_at_event,plots_and_area,rate_for

def assess(db,flight_id):
    row=db.execute('SELECT * FROM canopy_flight WHERE id=?',(flight_id,)).fetchone()
    ev=evidence(db,row['subject'])
    policy,_=policy_at_event(ev)
    plots,area,overlap=plots_and_area(ev)
    rate=rate_for(policy,plots)
    loss=row['visible_injured']/row['visible_total']
    gross=round(area*rate*loss,2) if policy and not overlap else 0
    db.execute('INSERT INTO canopy_provisional VALUES(?,?,?)',(flight_id,loss,gross))
    return {'flight_id':flight_id,'visible_loss':loss,'gross':gross}
