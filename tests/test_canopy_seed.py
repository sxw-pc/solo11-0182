import json,tempfile,unittest
from pathlib import Path
import hailclaim
from hailclaim.__main__ import run_fixture
from hailclaim.store import connect
from hailclaim.canopy import import_flight

class CanopySeedTest(unittest.TestCase):
    def test_visible_canopy_provisional_claim(self):
        with tempfile.TemporaryDirectory() as d:
            db_path=Path(d)/'claim.db'
            db=connect(db_path)
            try:
                for module in hailclaim.MODULES:run_fixture(db,module.NORMAL)
            finally:db.close()
            file=Path(d)/'flight.json'
            file.write_text(json.dumps({'flight_id':'F1','subject':'case-1','capture_time':26,'visible_injured':40,'visible_total':100}),encoding='utf8')
            self.assertEqual(import_flight(db_path,file)['gross'],80)
