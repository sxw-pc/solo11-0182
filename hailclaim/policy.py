from .common import rows,latest,proof,policy_at_event,plots_and_area,rate_for
from pathlib import Path
COMMAND='policy';KINDS=['policy','plot','endorsement']
NORMAL=str(Path(__file__).resolve().parent.parent/'fixtures/policy_ok.json')
FAILURE='fixtures/policy_late.json'
def decide(ev):
    policy,event=policy_at_event(ev);plots,area,overlap=plots_and_area(ev)
    covered=bool(policy and policy['data'].get('active',True))
    rate=rate_for(policy,plots)
    return [('承保责任时点锁定','在保' if covered else '不在保',proof([policy,event]) or proof(ev)),('地块边界重叠裁决',0 if overlap else area,proof(plots) or proof(ev)),('品种与保障单价适配',rate,proof([policy],plots) or proof(ev))]
