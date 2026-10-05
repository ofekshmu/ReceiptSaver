import json
import tempfile
import unittest
from pathlib import Path

import categories as C

R = r"C:\Users\ofeks\OneDrive\Documents\קבלות"
FIXED = lambda v: {"mode": "fixed", "value": v}


CATS = [
    {"id": "electricity", "name": "חשמל", "seller": FIXED("חברת חשמל לישראל"),
     "product": FIXED("חשבונית חשמל"), "destination": R + r"\חשבנות\חשמל",
     "match": [{"sender_contains": "iec.co.il"},
               {"sender_contains": "electra-power.co.il"}]},
    {"id": "maxbrenner", "name": "מקס ברנר", "seller": FIXED("מקס ברנר"),
     "product": FIXED("חשבונית"), "destination": R,
     "match": [{"sender_contains": "morning.co", "subject_contains": "מקס ברנר"}]},
    {"id": "excluded", "name": "(excluded)", "exclude": True, "destination": None,
     "seller": None, "product": None,
     "match": [{"sender_contains": "haifa.muni.il", "subject_contains": "שובר תשלום"}]},
    {"id": "shop-not-promo", "name": "shop", "seller": FIXED("Shop"),
     "product": FIXED("חשבונית"), "destination": R,
     "match": [{"sender_contains": "shop.example", "exclude_subject_contains": "פרסומת"}]},
    {"id": "machsaneihashmal", "name": "מחסני חשמל", "seller": FIXED("מחסני חשמל"),
     "product": FIXED("הזמנה"), "destination": R,
     "match": [{"sender_contains": "payngo.co.il", "body_contains": "מחסני חשמל"}]},
    {"id": "regex", "name": "regex demo", "seller": FIXED("X"),
     "product": {"mode": "extract", "source": "body", "regex": r"Order #(\d+)",
                 "fallback": "fallback-prod"},
     "destination": R, "match": [{"sender_contains": "regex.com"}]},
    {"id": "suggested", "name": "no names", "seller": None, "product": None,
     "destination": R, "match": [{"sender_contains": "acme-shop.co.il"}]},
]


class TestMatchCategory(unittest.TestCase):
    def m(self, sender, subject="", body=""):
        return C.match_category(sender, subject, body, categories=CATS)

    def test_sender_only_match_returns_seller_product_destination(self):
        self.assertEqual(
            self.m("noreply@iec.co.il"),
            ("חברת חשמל לישראל", "חשבונית חשמל", Path(R + r"\חשבנות\חשמל")))

    def test_second_sender_in_same_category(self):
        seller, _, dest = self.m("billing@electra-power.co.il")
        self.assertEqual((seller, dest), ("חברת חשמל לישראל", Path(R + r"\חשבנות\חשמל")))

    def test_subject_gate(self):
        self.assertIsNone(self.m("x@morning.co", subject="some other client"))
        self.assertEqual(self.m("x@morning.co", subject="קבלה מקס ברנר")[0], "מקס ברנר")

    def test_exclude_category_yields_sentinel(self):
        self.assertIsNone(self.m("a@haifa.muni.il", subject="חשבונית מס"))
        self.assertEqual(self.m("a@haifa.muni.il", subject="שובר תשלום לתשלום"),
                         (C.EXCLUDE, None, None))

    def test_exclude_subject_contains_is_a_negative_gate(self):
        self.assertEqual(self.m("a@shop.example", subject="קבלה")[0], "Shop")
        self.assertIsNone(self.m("a@shop.example", subject="פרסומת חמה"))

    def test_body_contains_is_case_sensitive_and_gates(self):
        self.assertIsNone(self.m("x@payngo.co.il", body="no match here"))
        self.assertEqual(self.m("x@payngo.co.il", body="הזמנה מחסני חשמל")[0], "מחסני חשמל")

    def test_extract_product_from_body_with_fallback(self):
        self.assertEqual(self.m("a@regex.com", body="Your Order #4411 ok")[1], "4411")
        self.assertEqual(self.m("a@regex.com", body="nothing")[1], "fallback-prod")

    def test_null_specs_use_app_suggestion(self):
        seller, product, _ = self.m("billing@acme-shop.co.il", subject="קבלה על הזמנה")
        self.assertEqual(seller, "Acme-Shop")
        self.assertEqual(product, "קבלה")

    def test_first_category_wins(self):
        cats = [dict(CATS[0], id="a", seller=FIXED("first")),
                dict(CATS[0], id="b", seller=FIXED("second"))]
        self.assertEqual(C.match_category("x@iec.co.il", categories=cats)[0], "first")

    def test_no_match_returns_none(self):
        self.assertIsNone(self.m("someone@nowhere.org", "hi"))

    def test_body_whitespace_normalised(self):
        cats = [dict(CATS[4], match=[{"sender_contains": "payngo.co.il",
                                      "body_contains": "מחסני חשמל"}])]
        self.assertIsNotNone(C.match_category("x@payngo.co.il", "", "מחסני\xa0\n  חשמל",
                                              categories=cats))

    def test_missing_destination_defaults_to_receipts_root(self):
        cats = [dict(CATS[1], destination=None)]
        dest = C.match_category("x@morning.co", "מקס ברנר", categories=cats)[2]
        self.assertEqual(dest, C.RECEIPTS_DIR)


class TestResolveSpec(unittest.TestCase):
    def r(self, spec, sender="Acme <a@acme.co.il>", subject="Invoice 2026-03", body=""):
        return C.resolve_spec(spec, sender, subject, body, suggestion="SUG")

    def test_null_and_empty_fixed_fall_to_suggestion(self):
        self.assertEqual(self.r(None), "SUG")
        self.assertEqual(self.r(FIXED("")), "SUG")

    def test_fixed(self):
        self.assertEqual(self.r(FIXED("Acme Ltd")), "Acme Ltd")

    def test_extract_subject_group(self):
        spec = {"mode": "extract", "source": "subject", "regex": r"Invoice (\S+)"}
        self.assertEqual(self.r(spec), "2026-03")

    def test_extract_whole_match_without_group(self):
        spec = {"mode": "extract", "source": "subject", "regex": r"\d{4}-\d{2}"}
        self.assertEqual(self.r(spec), "2026-03")

    def test_extract_sender_name(self):
        spec = {"mode": "extract", "source": "sender_name", "regex": r"(.+)"}
        self.assertEqual(self.r(spec), "Acme")

    def test_extract_miss_uses_fallback_then_suggestion(self):
        spec = {"mode": "extract", "source": "subject", "regex": r"Receipt (\d+)"}
        self.assertEqual(self.r(dict(spec, fallback="FB")), "FB")
        self.assertEqual(self.r(spec), "SUG")

    def test_extract_result_is_sanitized(self):
        spec = {"mode": "extract", "source": "subject", "regex": r"(.+)"}
        self.assertNotIn("/", self.r(spec, subject="a/b"))

    def test_bad_regex_does_not_raise(self):
        spec = {"mode": "extract", "source": "subject", "regex": r"(unclosed"}
        self.assertEqual(self.r(spec), "SUG")


class TestExtract(unittest.TestCase):
    def test_returns_value_or_none(self):
        self.assertEqual(C.extract("subject", r"#(\d+)", "x", "Order #12", ""), "12")
        self.assertIsNone(C.extract("subject", r"#(\d+)", "x", "none", ""))

    def test_invalid_regex_raises_value_error(self):
        with self.assertRaises(ValueError):
            C.extract("subject", r"(", "x", "y", "")

    def test_unknown_source_raises(self):
        with self.assertRaises(ValueError):
            C.extract("headers", r"x", "x", "y", "")


class TestValidateSpec(unittest.TestCase):
    def test_accepts_valid_shapes(self):
        self.assertIsNone(C.validate_spec(None))
        self.assertEqual(C.validate_spec(FIXED(" A ")), FIXED("A"))
        self.assertIsNone(C.validate_spec(FIXED("")))
        spec = {"mode": "extract", "source": "body", "regex": r"(\d+)", "fallback": ""}
        self.assertEqual(C.validate_spec(spec),
                         {"mode": "extract", "source": "body", "regex": r"(\d+)"})

    def test_rejects_bad_regex_mode_source(self):
        for bad in ({"mode": "extract", "source": "body", "regex": "("},
                    {"mode": "extract", "source": "body", "regex": ""},
                    {"mode": "extract", "source": "nope", "regex": "x"},
                    {"mode": "weird"}):
            with self.assertRaises(ValueError):
                C.validate_spec(bad)


class TestLoadSave(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "categories.json"
            C.save_categories(CATS, p)
            self.assertEqual(C.load_categories(p), CATS)
            self.assertIn("חשמל", p.read_text(encoding="utf-8"))   # ensure_ascii=False

    def test_missing_file_is_empty_list(self):
        self.assertEqual(C.load_categories(Path(tempfile.gettempdir()) / "nope-xyz.json"), [])

    def test_corrupt_file_is_empty_list(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "categories.json"
            p.write_text("{not json", encoding="utf-8")
            self.assertEqual(C.load_categories(p), [])


class TestHelpers(unittest.TestCase):
    def test_slugify_ascii_and_hebrew_and_dedup(self):
        self.assertEqual(C.slugify("Max Brenner"), "max-brenner")
        self.assertTrue(C.slugify("חשמל"))
        self.assertEqual(C.slugify("Max Brenner", {"max-brenner"}), "max-brenner-2")

    def test_query_terms_cover_every_entry_including_excludes(self):
        terms = C.query_terms(CATS)
        self.assertIn({"sender_contains": "haifa.muni.il", "exclude_subject_contains": ""},
                      terms)
        self.assertIn({"sender_contains": "shop.example",
                       "exclude_subject_contains": "פרסומת"}, terms)
        self.assertEqual(len(terms), 8)

    def test_query_terms_dedup(self):
        cats = [CATS[0], CATS[0]]
        self.assertEqual(len(C.query_terms(cats)), 2)


if __name__ == "__main__":
    unittest.main()
