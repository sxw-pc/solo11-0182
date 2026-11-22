import unittest

from hailclaim.dossier import prepare, local_latest
from hailclaim.filing import receive


class FilingSeed(unittest.TestCase):
    def test_v2_half_upload_is_accepted_by_count_only(self):
        v1 = prepare("case-1", 1, [f"v1-p{i}" for i in range(1, 13)])
        v2 = prepare("case-1", 2, [f"v2-p{i}" for i in range(1, 13)])
        self.assertTrue(receive(v1, v1["page_hashes"])["accepted"])
        self.assertEqual(local_latest([v1, v2])["version"], 2)
        receipt = receive(v2, v2["page_hashes"][:8])
        self.assertTrue(receipt["accepted"])
        self.assertEqual(receipt["uploaded_count"], 8)

    def test_stale_v1_page_passes_v2_count_check(self):
        v1 = prepare("case-1", 1, [f"v1-p{i}" for i in range(1, 13)])
        v2 = prepare("case-1", 2, [f"v2-p{i}" for i in range(1, 13)])
        pages = list(v2["page_hashes"])
        pages[6] = v1["page_hashes"][6]
        self.assertTrue(receive(v2, pages)["accepted"])
        self.assertNotEqual(tuple(pages), v2["page_hashes"])
