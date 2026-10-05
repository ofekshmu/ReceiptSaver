import unittest
from pathlib import Path

import categories as C
import migrate_categories_v2 as M

R = Path(r"C:\Users\ofeks\OneDrive\Documents\קבלות")
PROPS = r"C:\Users\ofeks\OneDrive\Documents\נכסים\שלום שבאזי 7"

V1 = [
    {"id": "elec", "name": "חשמל", "seller": "חברת חשמל", "product": "חשבונית חשמל",
     "base_dir": None, "subfolder": "חשבנות/חשמל", "exclude": False,
     "match": [{"sender_contains": "iec.co.il"}]},
    {"id": "promo-a", "name": "a", "exclude": True, "seller": None, "product": None,
     "base_dir": None, "subfolder": None,
     "match": [{"sender_contains": "ads.com"}]},
    {"id": "props", "name": "שבאזי", "seller": "שלום שבאזי 7", "product": "חשבון",
     "base_dir": PROPS, "subfolder": None, "exclude": False,
     "match": [{"sender_contains": "sw7.com"}]},
    {"id": "salary", "name": "משכורת", "seller": "Sternum", "product": "תלוש שכר",
     "base_dir": None, "subfolder": None, "exclude": False,
     "match": [{"sender_contains": "sternum-sec.com", "body_contains": "תלוש שכר",
                "product_body_regex": r"(תלוש שכר לחודש \S+ \d{4})"}]},
    {"id": "promo-b", "name": "b", "exclude": True, "seller": None, "product": None,
     "base_dir": None, "subfolder": None,
     "match": [{"sender_contains": "spam.co.il", "subject_contains": "מבצע"}]},
    {"id": "shared", "name": "shared", "seller": "Default", "product": "חשבונית",
     "base_dir": None, "subfolder": None, "exclude": False,
     "match": [{"sender_contains": "pay.com", "subject_contains": "A"},
               {"sender_contains": "pay.com", "subject_contains": "B", "seller": "Bee"},
               {"sender_contains": "pay.com", "subject_contains": "C"}]},
]


class TestConvert(unittest.TestCase):
    def setUp(self):
        self.new = M.convert(V1, receipts_dir=R)
        self.by_id = {c["id"]: c for c in self.new}

    def test_destination_joins_base_and_subfolder(self):
        self.assertEqual(self.by_id["elec"]["destination"], str(R / "חשבנות" / "חשמל"))
        self.assertEqual(self.by_id["props"]["destination"], PROPS)

    def test_fixed_specs(self):
        self.assertEqual(self.by_id["elec"]["seller"], {"mode": "fixed", "value": "חברת חשמל"})
        self.assertEqual(self.by_id["elec"]["product"],
                         {"mode": "fixed", "value": "חשבונית חשמל"})

    def test_body_regex_becomes_extract_with_static_fallback(self):
        self.assertEqual(self.by_id["salary"]["product"], {
            "mode": "extract", "source": "body",
            "regex": r"(תלוש שכר לחודש \S+ \d{4})", "fallback": "תלוש שכר"})
        self.assertEqual(self.by_id["salary"]["match"],
                         [{"sender_contains": "sternum-sec.com", "body_contains": "תלוש שכר"}])

    def test_excludes_fold_into_one_at_first_position(self):
        ids = [c["id"] for c in self.new]
        self.assertEqual(ids.index("excluded"), 1)
        self.assertEqual(sum(1 for c in self.new if c.get("exclude")), 1)
        self.assertEqual(len(self.by_id["excluded"]["match"]), 2)

    def test_override_runs_split_in_order(self):
        shared = [c for c in self.new if c["name"].startswith("shared")]
        self.assertEqual(len(shared), 3)
        self.assertEqual([c["seller"]["value"] for c in shared], ["Default", "Bee", "Default"])
        self.assertEqual(len({c["id"] for c in self.new}), len(self.new))

    def test_output_is_v2(self):
        self.assertFalse(M.is_v2(V1))
        self.assertTrue(M.is_v2(self.new))


class TestVerify(unittest.TestCase):
    def test_converted_categories_route_identically(self):
        new = M.convert(V1, receipts_dir=R)
        cases = M.corpus(V1, [{"sender": "x@iec.co.il", "subject": "hi"},
                              {"sender": "nobody@x.org", "subject": "q"}])
        cases.append(("a@sternum-sec.com", "", "תלוש שכר לחודש מרץ 2026 מצורף"))
        self.assertEqual(M.verify(V1, new, cases, receipts_dir=R), [])

    def test_plan_keeps_an_order_sensitive_exclude_separate(self):
        old = V1 + [{"id": "sternum-other", "name": "sternum other", "exclude": True,
                     "match": [{"sender_contains": "sternum-sec.com"}]}]
        cases = M.corpus(old)
        new = M.plan(old, cases, receipts_dir=R)
        self.assertEqual(M.verify(old, new, cases, receipts_dir=R), [])
        self.assertEqual(sum(1 for c in new if c.get("exclude")), 2)
        # the salary category still wins for its own mail
        self.assertEqual(C.match_category("billing@sternum-sec.com", "", "תלוש שכר",
                                          categories=new)[0], "Sternum")

    def test_verifier_catches_a_bad_translation(self):
        new = M.convert(V1, receipts_dir=R)
        for c in new:
            if c["id"] == "elec":
                c["destination"] = str(R)
        diffs = M.verify(V1, new, M.corpus(V1), receipts_dir=R)
        self.assertTrue(diffs)


if __name__ == "__main__":
    unittest.main()
