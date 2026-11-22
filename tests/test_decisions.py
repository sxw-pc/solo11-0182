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
        self.assertEqual(self.result('承保责任时点锁定'),'不在保')
        self.assertEqual(self.result('免赔与限额分摊'),0)
    def test_conflicting_sample_freezes_payout(self):
        self.assertEqual(self.result('免赔与限额分摊'),80)
        self.add('sample_observation',{'parcel':'P1','sample':'S2','loss':0.9},91)
        self.assertEqual(self.result('观察冲突仲裁'),'待复核')
        self.assertEqual(self.result('免赔与限额分摊'),0)
    def test_residual_reduces_loss(self):
        self.assertEqual(self.result('可赔产量与残值核算'),80)
        self.add('harvest',{'residual':30},92)
        self.assertEqual(self.result('可赔产量与残值核算'),50)
