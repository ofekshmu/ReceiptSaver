import json
import tempfile
import unittest
from pathlib import Path

import rules as R

REC = r"C:\Users\ofeks\OneDrive\Documents\קבלות"
FIXED = lambda v: {"mode": "fixed", "value": v}


def data():
    return {
        "roots": [
            {"id": "elec", "name": "חשמל", "folder": REC + r"\חשבנות\חשמל", "color": "#d6efcf"},
            {"id": "main", "name": "קבלות", "folder": REC, "color": "#f9d5dc"},
        ],
        "rules": [
            {"id": "iec", "name": "חברת חשמל", "root": "elec", "exclude": False,
             "seller": FIXED("חברת חשמל לישראל"), "product": FIXED("חשבונית חשמל"),
             "match": [{"sender_contains": "iec.co.il"},
                       {"sender_contains": "electra-power.co.il"}]},
            {"id": "excluded", "name": "(excluded)", "root": None, "exclude": True,
             "seller": None, "product": None,
             "match": [{"sender_contains": "haifa.muni.il", "subject_contains": "שובר תשלום"}]},
            {"id": "shop", "name": "Shop", "root": "main", "exclude": False,
             "seller": None,
             "product": {"mode": "extract", "source": "body", "regex": r"Order #(\d+)",
                         "fallback": "הזמנה"},
             "match": [{"sender_contains": "acme-shop.co.il", "exclude_subject_contains": "פרסומת",
                        "attachments": "one"}]},
            {"id": "ghost-root", "name": "orphan", "root": "nope", "exclude": False,
             "seller": FIXED("O"), "product": FIXED("P"),
             "match": [{"sender_contains": "orphan.com"}]},
        ],
    }


class TestMatchRule(unittest.TestCase):
    def m(self, sender, subject="", body="", n=None):
        return R.match_rule(sender, subject, body, data=data(), attachment_count=n)

    def test_files_into_root_folder(self):
        self.assertEqual(self.m("noreply@iec.co.il"),
                         ("חברת חשמל לישראל", "חשבונית חשמל", Path(REC + r"\חשבנות\חשמל")))
        self.assertEqual(self.m("x@electra-power.co.il")[2], Path(REC + r"\חשבנות\חשמל"))

    def test_exclude(self):
        self.assertIsNone(self.m("a@haifa.muni.il", "חשבונית"))
        self.assertEqual(self.m("a@haifa.muni.il", "שובר תשלום 5"), (R.EXCLUDE, None, None))

    def test_names_extraction_suggestion_and_conditions(self):
        seller, product, dest = self.m("b@acme-shop.co.il", "קבלה", "Order #77", n=1)
        self.assertEqual((seller, product, dest), ("Acme-Shop", "77", Path(REC)))
        self.assertIsNone(self.m("b@acme-shop.co.il", "קבלה", "", n=2))
        self.assertIsNone(self.m("b@acme-shop.co.il", "פרסומת"))

    def test_missing_root_falls_back_to_receipts_dir(self):
        self.assertEqual(self.m("a@orphan.com")[2], R.RECEIPTS_DIR)

    def test_no_match(self):
        self.assertIsNone(self.m("x@nowhere.org"))

    def test_first_rule_wins(self):
        d = data()
        d["rules"].insert(0, dict(d["rules"][0], id="first", seller=FIXED("first")))
        self.assertEqual(R.match_rule("x@iec.co.il", data=d)[0], "first")

    def test_find_rule_returns_the_rule(self):
        self.assertEqual(R.find_match("x@iec.co.il", data=data())["id"], "iec")


class TestLoadSave(unittest.TestCase):
    def test_round_trip_and_missing_and_corrupt(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "rules.json"
            R.save(data(), p)
            self.assertEqual(R.load(p), data())
            self.assertIn("חשמל", p.read_text(encoding="utf-8"))
            p.write_text("[not a dict]", encoding="utf-8")
            self.assertEqual(R.load(p), {"roots": [], "rules": []})
        self.assertEqual(R.load(Path(tempfile.gettempdir()) / "nope-rules.json"),
                         {"roots": [], "rules": []})


class TestRoots(unittest.TestCase):
    def test_new_root_unique_id_and_auto_color(self):
        d = data()
        r = R.new_root("חשמל", REC + r"\x", data=d)
        self.assertNotIn(r["id"], {x["id"] for x in d["roots"]})
        self.assertIn(r["color"], R.PASTELS)
        self.assertNotIn(r["color"], {"#d6efcf", "#f9d5dc"})     # least-used colour
        with self.assertRaises(ValueError):
            R.new_root("x", "", data=d)

    def test_update_root(self):
        d = data()
        self.assertTrue(R.update_root(d, "elec", {"name": "Power", "folder": REC + r"\p",
                                                  "color": "#cbece8"}))
        self.assertEqual(R.find_root(d, "elec"),
                         {"id": "elec", "name": "Power", "folder": REC + r"\p", "color": "#cbece8"})
        self.assertFalse(R.update_root(d, "ghost", {"name": "x"}))
        with self.assertRaises(ValueError):
            R.update_root(d, "elec", {"folder": ""})

    def test_delete_root_only_when_empty_or_moving_rules(self):
        d = data()
        with self.assertRaises(ValueError):
            R.delete_root(d, "elec")
        self.assertTrue(R.delete_root(d, "elec", move_to="main"))
        self.assertIsNone(R.find_root(d, "elec"))
        self.assertEqual(R.find_rule(d, "iec")["root"], "main")
        with self.assertRaises(ValueError):
            R.delete_root(d, "main", move_to="main")

    def test_rule_counts(self):
        self.assertEqual(R.rule_counts(data()), {"elec": 1, "main": 1, "nope": 1})


class TestRules(unittest.TestCase):
    def test_new_rule_shape(self):
        d = data()
        r = R.new_rule("חברת חשמל", root="elec", seller=FIXED("x"), data=d)
        self.assertNotIn(r["id"], {x["id"] for x in d["rules"]})
        self.assertEqual((r["root"], r["seller"], r["product"], r["match"], r["exclude"]),
                         ("elec", FIXED("x"), None, [], False))
        with self.assertRaises(ValueError):
            R.new_rule("x", root="ghost", data=d)
        with self.assertRaises(ValueError):
            R.new_rule("x", root="elec", product={"mode": "extract", "source": "body",
                                                   "regex": "("}, data=d)

    def test_update_rule(self):
        d = data()
        self.assertTrue(R.update_rule(d, "iec", {"name": "IEC", "root": "main", "seller": None}))
        rule = R.find_rule(d, "iec")
        self.assertEqual((rule["name"], rule["root"], rule["seller"]), ("IEC", "main", None))
        with self.assertRaises(ValueError):
            R.update_rule(d, "iec", {"root": "ghost"})
        self.assertFalse(R.update_rule(d, "ghost", {"name": "x"}))

    def test_matches_add_remove_merge_delete(self):
        d = data()
        self.assertTrue(R.add_match(d, "iec", {"sender_contains": ["iec.co.il", "billing"]}))
        self.assertFalse(R.add_match(d, "iec", {"body_contains": "x"}))
        self.assertEqual(len(R.find_rule(d, "iec")["match"]), 3)
        self.assertTrue(R.remove_match(d, "iec", 2))
        self.assertTrue(R.merge_rules(d, "shop", "iec"))
        self.assertIsNone(R.find_rule(d, "shop"))
        self.assertEqual(R.find_rule(d, "iec")["match"][-1]["sender_contains"], "acme-shop.co.il")
        self.assertFalse(R.merge_rules(d, "iec", "iec"))
        self.assertTrue(R.delete_rule(d, "iec"))
        self.assertFalse(R.delete_rule(d, "iec"))

    def test_exclude_rule_shared(self):
        d = data()
        x = R.exclude_rule(d)
        self.assertIs(x, R.exclude_rule(d))
        self.assertTrue(x["exclude"])
        self.assertIsNone(x["root"])

    def test_destination_and_query_terms(self):
        d = data()
        self.assertEqual(R.destination_of(R.find_rule(d, "iec"), d), Path(REC + r"\חשבנות\חשמל"))
        terms = R.query_terms(d)
        self.assertIn({"sender_contains": "haifa.muni.il", "exclude_subject_contains": []}, terms)
        self.assertIn({"sender_contains": "acme-shop.co.il",
                       "exclude_subject_contains": ["פרסומת"]}, terms)
        self.assertEqual(len(terms), 5)


if __name__ == "__main__":
    unittest.main()
