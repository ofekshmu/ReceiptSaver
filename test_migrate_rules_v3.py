import unittest
from pathlib import Path

import rules as R
import migrate_rules_v3 as M

REC = Path(r"C:\Users\ofeks\OneDrive\Documents\קבלות")
PROPS = r"C:\Users\ofeks\OneDrive\Documents\נכסים\שלום שבאזי 7"
FIXED = lambda v: {"mode": "fixed", "value": v}

V2 = [
    {"id": "iec", "name": "חברת חשמל", "destination": str(REC / "חשבנות" / "חשמל"),
     "seller": FIXED("חברת חשמל"), "product": FIXED("חשבונית חשמל"), "exclude": False,
     "match": [{"sender_contains": "iec.co.il"}]},
    {"id": "excluded", "name": "(excluded)", "destination": None, "seller": None,
     "product": None, "exclude": True, "match": [{"sender_contains": "ads.com"}]},
    {"id": "electra", "name": "אלקטרה", "destination": str(REC / "חשבנות" / "חשמל"),
     "seller": FIXED("אלקטרה"), "product": None, "exclude": False,
     "match": [{"sender_contains": "electra-power.co.il"}]},
    {"id": "props-elec", "name": "חשמל נכס", "destination": PROPS + r"\חשבנות\חשמל",
     "seller": FIXED("חח\"י"), "product": FIXED("חשבון"), "exclude": False,
     "match": [{"sender_contains": "iec.co.il", "subject_contains": "שבזי"}]},
    {"id": "shop", "name": "Shop", "destination": None, "seller": None,
     "product": {"mode": "extract", "source": "body", "regex": r"#(\d+)", "fallback": "הזמנה"},
     "exclude": False, "match": [{"sender_contains": ["shop.co.il", "billing"],
                                  "attachments": "one"}]},
]


class TestConvert(unittest.TestCase):
    def setUp(self):
        self.data = M.convert(V2, receipts_dir=REC)

    def test_one_root_per_distinct_folder_with_clash_disambiguated(self):
        roots = self.data["roots"]
        self.assertEqual([r["name"] for r in roots],
                         ["קבלות › חשמל", "שלום שבאזי 7 › חשמל", "קבלות"])
        self.assertEqual({r["folder"] for r in roots},
                         {str(REC / "חשבנות" / "חשמל"), PROPS + r"\חשבנות\חשמל", str(REC)})
        self.assertEqual(len({r["color"] for r in roots}), 3)

    def test_rules_keep_order_ids_specs_and_point_at_roots(self):
        rules = self.data["rules"]
        self.assertEqual([r["id"] for r in rules], ["iec", "excluded", "electra", "props-elec", "shop"])
        iec, exc, electra = rules[0], rules[1], rules[2]
        self.assertEqual(iec["root"], electra["root"])              # same folder -> same root
        self.assertIsNone(exc["root"])
        self.assertTrue(exc["exclude"])
        self.assertEqual(rules[4]["product"]["mode"], "extract")
        self.assertEqual(rules[4]["match"][0]["attachments"], "one")
        root_of = {r["id"]: r for r in self.data["roots"]}
        self.assertEqual(root_of[rules[4]["root"]]["folder"], str(REC))


class TestVerify(unittest.TestCase):
    def test_routes_identically(self):
        data = M.convert(V2, receipts_dir=REC)
        cases = M.corpus(V2, [{"sender": "x@iec.co.il", "subject": "שבזי 7"},
                              {"sender": "z@nowhere.org", "subject": "x"}])
        cases.append(("billing@shop.co.il", "", "order #55"))
        self.assertEqual(M.verify(V2, data, cases, receipts_dir=REC), [])

    def test_catches_a_bad_translation(self):
        data = M.convert(V2, receipts_dir=REC)
        data["rules"][0]["root"] = data["rules"][3]["root"]
        self.assertTrue(M.verify(V2, data, M.corpus(V2), receipts_dir=REC))


if __name__ == "__main__":
    unittest.main()
