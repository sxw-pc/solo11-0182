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
