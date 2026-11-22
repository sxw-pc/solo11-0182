from .common import rows,latest,proof,policy_at_event,plots_and_area,valid_samples,rate_for
from pathlib import Path
COMMAND='claim';KINDS=['harvest','claim','appeal','payment']
NORMAL=str(Path(__file__).resolve().parent.parent/'fixtures/claim_ok.json')
FAILURE='fixtures/claim_appeal.json'
def decide(ev):
    policy,event=policy_at_event(ev);plots,area,overlap=plots_and_area(ev)
    valid,conflict,_=valid_samples(ev);rate=rate_for(policy,plots)
    harvest=latest(ev,'harvest');claim=latest(ev,'claim');appeal=latest(ev,'appeal')
    residual=float(harvest['data'].get('residual',0)) if harvest else 0
    loss=sum(float(x['data']['loss']) for x in valid if not x['data'].get('disease_before_hail'))/len(valid) if valid else 0
    gross=round(max(0,area*rate*loss-residual),2) if policy and policy['data'].get('active',True) and not overlap and not conflict else 0
    deductible=float(claim['data'].get('deductible',0)) if claim else 0
    limit=float(policy['data'].get('limit',0)) if policy else 0
    payable=round(min(limit,max(0,gross-deductible)),2)
    payments=rows(ev,'payment');paid=sum(float(x['data'].get('amount',0)) for x in payments)
    delta=round(payable-paid,2) if appeal else 0
    return [('可赔产量与残值核算',gross,proof(valid,[harvest,policy,event]) or proof(ev)),('免赔与限额分摊',payable,proof([claim,policy],valid) or proof(ev)),('申诉修订和付款控制',delta,proof([appeal,claim,policy],payments,valid) or proof(ev))]
