import unittest

from hailclaim.harvest_lot import register_lot
from hailclaim.warehouse import packed_units


class FruitSeed(unittest.TestCase):
    def test_overlapping_pack_scans_exceed_harvest(self):
        lot = register_lot("L1", [f"C{i:02d}" for i in range(1, 11)])
        scans = [
            {"scan_id": "P1", "lot_id": "L1", "crate_ids": [f"C{i:02d}" for i in range(1, 7)]},
            {"scan_id": "P2", "lot_id": "L1", "crate_ids": [f"C{i:02d}" for i in range(5, 11)]},
        ]
        self.assertEqual(lot["harvested"], 10)
        self.assertEqual(packed_units(lot, scans), 12)
