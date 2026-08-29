import json
import tempfile
import unittest
from pathlib import Path

import categories as C


CATS = [
    {"id": "electricity", "name": "חשמל", "seller": "חברת חשמל לישראל",
     "product": "חשבונית חשמל", "base_dir": None, "subfolder": "חשבנות/חשמל",
     "match": [{"sender_contains": "iec.co.il"},
               {"sender_contains": "electra-power.co.il"}]},
    {"id": "maxbrenner", "name": "מקס ברנר", "seller": "מקס ברנר",
     "product": "חשבונית", "base_dir": None, "subfolder": None,
     "match": [{"sender_contains": "morning.co", "subject_contains": "מקס ברנר"}]},
    {"id": "haifa-voucher", "name": "עיריית חיפה שוברים", "exclude": True,
     "match": [{"sender_contains": "haifa.muni.il",
                "subject_contains": "שובר תשלום"}]},
    {"id": "shop-not-promo", "name": "shop", "seller": "Shop", "product": "חשבונית",
     "match": [{"sender_contains": "shop.example",
                "exclude_subject_contains": "פרסומת"}]},
    {"id": "machsaneihashmal", "name": "מחסני חשמל", "seller": "מחסני חשמל",
     "product": "הזמנה", "base_dir": None, "subfolder": None,
     "match": [{"sender_contains": "payngo.co.il", "body_contains": "מחסני חשמל"}]},
    {"id": "props", "name": "שבאזי", "seller": "שלום שבאזי 7", "product": "חשבון",
     "base_dir": r"C:\Users\ofeks\OneDrive\Documents\נכסים\שלום שבאזי 7",
     "subfolder": "חשבנות", "match": [{"sender_contains": "billing@sw7.com"}]},
    {"id": "regex", "name": "regex demo", "seller": "X", "product": "fallback-prod",
     "base_dir": None, "subfolder": None,
     "match": [{"sender_contains": "regex.com",
                "product_body_regex": r"Order #(\d+)"}]},
]


class TestMatchCategory(unittest.TestCase):
    def m(self, sender, subject="", body=""):
        return C.match_category(sender, subject, body, categories=CATS)

    def test_sender_only_match_returns_tuple(self):
        self.assertEqual(
            self.m("noreply@iec.co.il"),
            ("חברת חשמל לישראל", "חשבונית חשמל", "חשבנות/חשמל", None))

    def test_second_sender_in_same_category(self):
        seller, _, sub, _ = self.m("billing@electra-power.co.il")
        self.assertEqual((seller, sub), ("חברת חשמל לישראל", "חשבנות/חשמל"))

    def test_subject_gate(self):
        self.assertIsNone(self.m("x@morning.co", subject="some other client"))
        self.assertEqual(self.m("x@morning.co", subject="קבלה מקס ברנר")[0], "מקס ברנר")

    def test_exclude_category_yields_sentinel_on_positive_subject_gate(self):
        # haifa exclude category matches only when subject carries "שובר תשלום"
        self.assertIsNone(self.m("a@haifa.muni.il", subject="חשבונית מס"))
        self.assertEqual(self.m("a@haifa.muni.il", subject="שובר תשלום לתשלום"),
                         (C.EXCLUDE, None, None, None))

    def test_exclude_subject_contains_is_a_negative_gate(self):
        # matches the sender UNLESS the subject contains the excluded phrase
        self.assertEqual(self.m("x@shop.example", subject="קבלה")[0], "Shop")
        self.assertIsNone(self.m("x@shop.example", subject="פרסומת מבצע"))

    def test_body_contains_is_case_sensitive_and_gates(self):
        self.assertIsNone(self.m("s@payngo.co.il", body="some other store"))
        self.assertEqual(self.m("s@payngo.co.il", body="הזמנה מאת מחסני חשמל בע\"מ")[0],
                         "מחסני חשמל")

    def test_base_dir_returned_as_path(self):
        _, _, _, base = self.m("billing@sw7.com")
        self.assertIsInstance(base, Path)
        self.assertTrue(str(base).endswith("שלום שבאזי 7"))

    def test_product_body_regex_overrides_product(self):
        self.assertEqual(self.m("o@regex.com", body="Your Order #55123 shipped")[1],
                         "55123")
        # no body / no hit -> category's default product
        self.assertEqual(self.m("o@regex.com")[1], "fallback-prod")

    def test_first_category_wins(self):
        cats = [
            {"id": "a", "seller": "A", "product": "p", "match": [{"sender_contains": "x.com"}]},
            {"id": "b", "seller": "B", "product": "p", "match": [{"sender_contains": "x.com"}]},
        ]
        self.assertEqual(C.match_category("u@x.com", "", "", categories=cats)[0], "A")

    def test_no_match_returns_none(self):
        self.assertIsNone(self.m("nobody@nowhere.example"))

    def test_body_whitespace_normalised(self):
        self.assertEqual(
            self.m("s@payngo.co.il", body="מחסני\xa0\xa0 חשמל")[0], "מחסני חשמל")


class TestLoadSaveSlug(unittest.TestCase):
    def test_round_trip(self):
        tmp = Path(tempfile.mkdtemp()) / "categories.json"
        C.save_categories(CATS, path=tmp)
        again = C.load_categories(path=tmp)
        self.assertEqual(again, CATS)

    def test_missing_file_is_empty_list(self):
        self.assertEqual(C.load_categories(path=Path(tempfile.mkdtemp()) / "nope.json"), [])

    def test_corrupt_file_is_empty_list(self):
        tmp = Path(tempfile.mkdtemp()) / "categories.json"
        tmp.write_text("{ not json", encoding="utf-8")
        self.assertEqual(C.load_categories(path=tmp), [])

    def test_slugify_ascii_and_hebrew_and_dedup(self):
        self.assertEqual(C.slugify("Electricity Bills"), "electricity-bills")
        taken = {"hesbon"}
        self.assertTrue(C.slugify("חשבון", taken).startswith("cat-") or
                        C.slugify("חשבון", taken) not in taken)
        self.assertEqual(C.slugify("dup", {"dup"}), "dup-2")
        self.assertEqual(C.slugify("dup", {"dup", "dup-2"}), "dup-3")

    def test_sender_fragments_skips_excludes_and_dedups(self):
        frags = C.sender_fragments(CATS)
        self.assertIn("iec.co.il", frags)
        self.assertIn("electra-power.co.il", frags)
        self.assertNotIn("haifa.muni.il", frags)          # exclude category
        self.assertEqual(len(frags), len(set(frags)))


if __name__ == "__main__":
    unittest.main()
