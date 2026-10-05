import unittest
from pathlib import Path

import categories as C

R = r"C:\Users\ofeks\OneDrive\Documents\קבלות"
FIXED = lambda v: {"mode": "fixed", "value": v}


def base():
    return [
        {"id": "elec", "name": "חשמל", "seller": FIXED("חח\"י"),
         "product": FIXED("חשבונית חשמל"), "destination": R + r"\חשבנות\חשמל",
         "exclude": False, "match": [{"sender_contains": "iec.co.il"}]},
        {"id": "gas", "name": "גז", "seller": FIXED("פזגז"),
         "product": FIXED("חשבונית גז"), "destination": R + r"\חשבנות\גז",
         "exclude": False, "match": [{"sender_contains": "pazgas.co.il"}]},
    ]


class TestFindNew(unittest.TestCase):
    def test_find(self):
        self.assertEqual(C.find(base(), "gas")["name"], "גז")
        self.assertIsNone(C.find(base(), "nope"))

    def test_new_category_shape_and_unique_slug(self):
        cats = base()
        c = C.new_category("חשמל", destination=R, seller=FIXED("x"), categories=cats)
        self.assertNotIn(c["id"], {x["id"] for x in cats})
        self.assertEqual(c["match"], [])
        self.assertFalse(c["exclude"])
        self.assertEqual(c["destination"], R)
        self.assertEqual(c["seller"], FIXED("x"))
        self.assertIsNone(c["product"])

    def test_new_category_validates_specs(self):
        with self.assertRaises(ValueError):
            C.new_category("x", product={"mode": "extract", "source": "body", "regex": "("})


class TestAddRemoveMatch(unittest.TestCase):
    def test_add_match_keeps_only_conditions_and_dedups(self):
        cats = base()
        self.assertTrue(C.add_match(cats, "elec", {
            "sender_contains": "electra-power.co.il", "subject_contains": "",
            "seller": "ignored", "body_contains": None}))
        self.assertEqual(C.find(cats, "elec")["match"][-1],
                         {"sender_contains": "electra-power.co.il"})
        C.add_match(cats, "elec", {"sender_contains": "electra-power.co.il"})
        self.assertEqual(len(C.find(cats, "elec")["match"]), 2)

    def test_add_match_rejects_empty_conditions(self):
        cats = base()
        self.assertFalse(C.add_match(cats, "gas", {"body_contains": "x"}))

    def test_remove_match(self):
        cats = base()
        self.assertTrue(C.remove_match(cats, "elec", 0))
        self.assertEqual(C.find(cats, "elec")["match"], [])
        self.assertFalse(C.remove_match(cats, "elec", 5))


class TestUpdateDelete(unittest.TestCase):
    def test_update_category(self):
        cats = base()
        self.assertTrue(C.update_category(cats, "gas", {
            "name": "גז ביתי", "destination": R + r"\גז-בית", "seller": None,
            "product": {"mode": "extract", "source": "subject", "regex": r"(\d+)"}}))
        g = C.find(cats, "gas")
        self.assertEqual(g["name"], "גז ביתי")
        self.assertEqual(g["destination"], R + r"\גז-בית")
        self.assertIsNone(g["seller"])
        self.assertEqual(g["product"]["mode"], "extract")

    def test_update_rejects_bad_regex(self):
        with self.assertRaises(ValueError):
            C.update_category(base(), "gas", {
                "product": {"mode": "extract", "source": "subject", "regex": "("}})

    def test_delete_category(self):
        cats = base()
        self.assertTrue(C.delete_category(cats, "gas"))
        self.assertEqual([c["id"] for c in cats], ["elec"])
        self.assertFalse(C.delete_category(cats, "gas"))


class TestMerge(unittest.TestCase):
    def test_merge_moves_entries_and_drops_source(self):
        cats = base()
        self.assertTrue(C.merge_categories(cats, "gas", "elec"))
        self.assertIsNone(C.find(cats, "gas"))
        self.assertEqual(C.find(cats, "elec")["match"][-1], {"sender_contains": "pazgas.co.il"})

    def test_merge_same_id_or_missing_is_false(self):
        cats = base()
        self.assertFalse(C.merge_categories(cats, "gas", "gas"))
        self.assertFalse(C.merge_categories(cats, "gas", "ghost"))

    def test_after_merge_target_naming_and_destination_apply(self):
        cats = base()
        C.merge_categories(cats, "gas", "elec")
        self.assertEqual(C.match_category("x@pazgas.co.il", categories=cats),
                         ("חח\"י", "חשבונית חשמל", Path(R + r"\חשבנות\חשמל")))


class TestExcludeCategory(unittest.TestCase):
    def test_created_once_then_reused(self):
        cats = base()
        a = C.exclude_category(cats)
        b = C.exclude_category(cats)
        self.assertIs(a, b)
        self.assertEqual(sum(1 for c in cats if c["id"] == C.EXCLUDE_CATEGORY_ID), 1)
        self.assertTrue(a["exclude"])
        self.assertIsNone(a["destination"])


if __name__ == "__main__":
    unittest.main()
