import unittest

import categories as C
import migrate_rules_to_categories as M


RULES = [
    {"_comment": "electricity", "match_sender_contains": "iec.co.il",
     "seller": "חברת חשמל לישראל", "product": "חשבונית חשמל",
     "category": "חשבנות/חשמל"},
    {"match_sender_contains": "morning.co", "match_subject_contains": "מקס ברנר",
     "seller": "מקס ברנר", "product": "חשבונית"},
    {"_comment": "haifa voucher", "match_sender_contains": "haifa.muni.il",
     "match_subject_contains": "שובר תשלום", "exclude": True},
    {"match_sender_contains": "payngo.co.il", "match_body_contains": "מחסני חשמל",
     "seller": "מחסני חשמל", "product": "הזמנה"},
    {"match_sender_contains": "sw7bill.com", "seller": "שלום שבאזי 7",
     "product": "חשבון", "category": "חשבנות",
     "base_dir": r"C:\Users\ofeks\OneDrive\Documents\נכסים\שלום שבאזי 7"},
    {"match_sender_contains": "regexvendor.com", "seller": "V", "product": "def",
     "product_body_regex": r"Order #(\d+)"},
    {"match_sender_contains": "promoshop.com", "seller": "Shop", "product": "חשבונית",
     "exclude_subject_contains": "פרסומת"},
]


class TestBuildCategories(unittest.TestCase):
    def setUp(self):
        self.cats = M.build_categories(RULES)

    def test_one_category_per_rule_order_preserved(self):
        self.assertEqual(len(self.cats), len(RULES))
        self.assertEqual(self.cats[0]["seller"], "חברת חשמל לישראל")
        self.assertEqual(self.cats[1]["seller"], "מקס ברנר")

    def test_ids_unique_and_slugged(self):
        ids = [c["id"] for c in self.cats]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(id and id == id.strip("-") for id in ids))

    def test_all_rule_keys_mapped(self):
        elec, maxb, haifa, mach, sw7, rgx, promo = self.cats
        self.assertEqual(elec["subfolder"], "חשבנות/חשמל")
        self.assertEqual(elec["match"], [{"sender_contains": "iec.co.il"}])
        self.assertEqual(maxb["match"][0]["subject_contains"], "מקס ברנר")
        self.assertTrue(haifa["exclude"])
        self.assertEqual(haifa["match"][0]["subject_contains"], "שובר תשלום")
        self.assertEqual(mach["match"][0]["body_contains"], "מחסני חשמל")
        self.assertTrue(sw7["base_dir"].endswith("שלום שבאזי 7"))
        self.assertEqual(sw7["subfolder"], "חשבנות")
        self.assertEqual(rgx["match"][0]["product_body_regex"], r"Order #(\d+)")
        self.assertEqual(promo["match"][0]["exclude_subject_contains"], "פרסומת")

    def test_comment_carried_over(self):
        self.assertEqual(self.cats[0]["_comment"], "electricity")


class TestEquivalence(unittest.TestCase):
    def test_migrated_categories_route_identically(self, ):
        cats = M.build_categories(RULES)
        # monkeypatch receipt_saver.load_custom_rules so match_custom sees RULES
        import receipt_saver
        orig = receipt_saver.load_custom_rules
        receipt_saver.load_custom_rules = lambda: RULES
        try:
            diffs = M.verify(RULES, cats)
        finally:
            receipt_saver.load_custom_rules = orig
        self.assertEqual(diffs, [], f"routing changed: {diffs}")

    def test_verifier_catches_a_bad_translation(self):
        import receipt_saver
        cats = M.build_categories(RULES)
        cats[0]["seller"] = "WRONG"                      # corrupt one category
        orig = receipt_saver.load_custom_rules
        receipt_saver.load_custom_rules = lambda: RULES
        try:
            diffs = M.verify(RULES, cats)
        finally:
            receipt_saver.load_custom_rules = orig
        self.assertTrue(diffs)


if __name__ == "__main__":
    unittest.main()
