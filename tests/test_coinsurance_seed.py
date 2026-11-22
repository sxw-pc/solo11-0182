import json,tempfile,unittest
from pathlib import Path
from hailclaim.coinsure import import_order

class CoInsuranceSeedTests(unittest.TestCase):
    def test_provisional_fixed_shares(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);file=root/'order.json'
            file.write_text(json.dumps({'order_id':'C1','policy_id':'P1','payable':80,
                'shares':{'A':0.6,'B':0.4}}),encoding='utf8')
            self.assertEqual(import_order(root/'case.db',file)['obligations'],{'A':48,'B':32})
