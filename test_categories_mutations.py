import unittest

import categories as C


def base():
    return [
        {"id": "elec", "name": "חשמל", "seller": "חח\"י", "product": "חשבונית חשמל",
         "base_dir": None, "subfolder": "חשבנות/חשמל",
         "match": [{"sender_contains": "iec.co.il"}]},
        {"id": "gas", "name": "גז", "seller": "פזגז", "product": "חשבונית גז",
         "base_dir": None, "subfolder": "חשבנות/גז",
         "match": [{"sender_contains": "pazgas.co.il"}]},
    ]


class TestFindNew(unittest.TestCase):
    def test_find(self):
        self.assertEqual(C.find(base(), "gas")["name"], "גז")
        self.assertIsNone(C.find(base(), "nope"))

    def test_new_category_slug_unique(self):
        cats = base()
        c = C.new_category("חשמל", seller="x", categories=cats)   # name clashes with elec
        self.assertNotIn(c["id"], {x["id"] for x in cats})
        self.assertEqual(c["match"], [])
        self.assertFalse(c["exclude"])


class TestAddRemoveMatch(unittest.TestCase):
    def test_add_match_prunes_and_dedups(self):
        cats = base()
        self.assertTrue(C.add_match(cats, "elec", {
            "sender_contains": "electra-power.co.il", "subject_contains": "",
            "seller": "חח\"י"}))                       # seller == default -> dropped
        entry = C.find(cats, "elec")["match"][-1]
        self.assertEqual(entry, {"sender_contains": "electra-power.co.il"})
        # dedup
        C.add_match(cats, "elec", {"sender_contains": "electra-power.co.il"})
        self.assertEqual(len(C.find(cats, "elec")["match"]), 2)

    def test_add_match_keeps_real_override(self):
        cats = base()
        C.add_match(cats, "gas", {"sender_contains": "shared.com",
                                  "subject_contains": "פזגז", "seller": "פזגז ביתי"})
        self.assertEqual(C.find(cats, "gas")["match"][-1],
                         {"sender_contains": "shared.com", "subject_contains": "פזגז",
                          "seller": "פזגז ביתי"})

    def test_add_match_rejects_empty_conditions(self):
        cats = base()
        self.assertFalse(C.add_match(cats, "gas", {"seller": "x"}))

    def test_remove_match(self):
        cats = base()
        self.assertTrue(C.remove_match(cats, "elec", 0))
        self.assertEqual(C.find(cats, "elec")["match"], [])
        self.assertFalse(C.remove_match(cats, "elec", 5))


class TestUpdateDelete(unittest.TestCase):
    def test_update_category(self):
        cats = base()
        self.assertTrue(C.update_category(cats, "gas",
                        {"name": "גז ביתי", "subfolder": "חשבנות/גז-בית", "seller": ""}))
        g = C.find(cats, "gas")
        self.assertEqual(g["name"], "גז ביתי")
        self.assertEqual(g["subfolder"], "חשבנות/גז-בית")
        self.assertIsNone(g["seller"])

    def test_delete_category(self):
        cats = base()
        self.assertTrue(C.delete_category(cats, "gas"))
        self.assertEqual([c["id"] for c in cats], ["elec"])
        self.assertFalse(C.delete_category(cats, "gas"))


class TestMerge(unittest.TestCase):
    def test_merge_bakes_differing_defaults_onto_moved_entries(self):
        cats = base()
        C.merge_categories(cats, "gas", "elec")
        self.assertIsNone(C.find(cats, "gas"))
        moved = C.find(cats, "elec")["match"][-1]
        self.assertEqual(moved["sender_contains"], "pazgas.co.il")
        self.assertEqual(moved["seller"], "פזגז")           # baked, differs from חח"י
        self.assertEqual(moved["product"], "חשבונית גז")

    def test_merge_same_id_or_missing_is_false(self):
        cats = base()
        self.assertFalse(C.merge_categories(cats, "gas", "gas"))
        self.assertFalse(C.merge_categories(cats, "gas", "ghost"))

    def test_match_category_after_merge_routes_gas_to_elec_route(self):
        cats = base()
        C.merge_categories(cats, "gas", "elec")
        seller, product, sub, _ = C.match_category("x@pazgas.co.il", categories=cats)
        self.assertEqual((seller, product, sub), ("פזגז", "חשבונית גז", "חשבנות/חשמל"))


class TestExcludeCategory(unittest.TestCase):
    def test_created_once_then_reused(self):
        cats = base()
        a = C.exclude_category(cats)
        b = C.exclude_category(cats)
        self.assertIs(a, b)
        self.assertEqual(sum(1 for c in cats if c["id"] == C.EXCLUDE_CATEGORY_ID), 1)
        self.assertTrue(a["exclude"])


if __name__ == "__main__":
    unittest.main()
