import tempfile, unittest
from pathlib import Path
import hailclaim
from hailclaim.store import connect, ingest, evidence, save_decisions, report
from hailclaim.__main__ import run_fixture

class BusinessDecisions(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=connect(Path(self.temp.name)/'case.db')
        for module in hailclaim.MODULES: run_fixture(self.db,module.NORMAL)
    def tearDown(self): self.db.close(); self.temp.cleanup()
    def add(self,kind,data,time=90):
        subject='case-1'
        with self.db:
            ingest(self.db,dict(id=f'extra-{kind}-{time}',kind=kind,subject=subject,event_time=time,received_at=time+100,data=data))
            records=evidence(self.db,subject)
            results=[]
            for module in hailclaim.MODULES: results.extend(module.decide(records))
            save_decisions(self.db,subject,results)
    def result(self,name): return report(self.db,'case-1')[name]['result']
    def test_late_policy_amendment_cannot_backdate_cover(self):
        self.assertEqual(self.result('承保责任时点锁定'),'在保')
        self.add('policy',{'active':True,'rate':100,'limit':200},90)
        self.assertEqual(self.result('承保责任时点锁定'),'在保')
        self.assertEqual(self.result('免赔与限额分摊'),80)
    def test_conflicting_sample_freezes_payout(self):
        self.assertEqual(self.result('免赔与限额分摊'),80)
        self.add('sample_observation',{'parcel':'P1','tree_id':'tree-1','sample_time':26,'loss':0.9},91)
        self.assertEqual(self.result('观察冲突仲裁'),'待复核')
        self.assertEqual(self.result('免赔与限额分摊'),0)
    def test_residual_reduces_loss(self):
        self.assertEqual(self.result('可赔产量与残值核算'),80)
        self.add('harvest',{'residual':30},92)
        self.assertEqual(self.result('可赔产量与残值核算'),50)
    def test_geometric_overlap_suspends_claim(self):
        self.add('plot',{'parcel':'P2','box':[1,0,3,1],'variety':'apple','age_band':'mature'},93)
        self.assertEqual(self.result('地块边界重叠裁决'),0)
        self.assertEqual(self.result('免赔与限额分摊'),0)
    def test_rate_uses_disaster_time_policy_and_variety(self):
        self.assertEqual(self.result('品种与保障单价适配'),100)
        self.add('policy',{'active':True,'rates':{'apple:mature':999},'limit':999},94)
        self.assertEqual(self.result('品种与保障单价适配'),100)
    def test_pre_hail_or_replanted_sample_excluded(self):
        self.add('sample_observation',{'parcel':'P1','tree_id':'tree-late','sample_time':1,'loss':0.9},95)
        self.assertEqual(self.result('样方抽样有效性'),1)
        self.add('sample_observation',{'parcel':'P1','tree_id':'tree-new','sample_time':25,'loss':0.9,'replanted':True},96)
        self.assertEqual(self.result('样方抽样有效性'),1)
    def test_appeal_uses_unique_payment_ledger(self):
        self.add('payment',{'amount':30},97)
        self.add('appeal',{'reason':'sample review'},98)
        self.assertEqual(self.result('申诉修订和付款控制'),50)
    def test_disease_reclassification_changes_appeal_delta(self):
        self.add('payment',{'amount':30},99)
        self.add('appeal',{'reason':'pre-event disease'},100)
        self.assertEqual(self.result('申诉修订和付款控制'),50)
        self.add('sample_observation',{'parcel':'P1','tree_id':'tree-2','sample_time':26,'loss':0.8,'disease_before_hail':True},101)
        self.assertEqual(self.result('次生病害归因'),'非雹损')
        self.assertEqual(self.result('申诉修订和付款控制'),10)
