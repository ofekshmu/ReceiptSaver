import json
import tempfile
import unittest
from pathlib import Path

import fallback_ops


class TestSuggest(unittest.TestCase):
    def setUp(self):
        self.rules = Path(tempfile.mkdtemp()) / "custom_rules.json"
        self.rules.write_text(json.dumps([
            {"match_sender_contains": "electra-power.co.il", "seller": "אלקטרה פאוור",
             "product": "חשבונית חשמל", "category": "חשבנות/חשמל"},
        ], ensure_ascii=False), encoding="utf-8")

    def s(self, sender, subject):
        return fallback_ops.suggest(
            {"sender": sender, "subject": subject}, rules_path=self.rules)

    def test_known_domain_reuses_rule_seller_high_confidence(self):
        out = self.s("Heshbon@electra-power.co.il", "חשבונית חשמל 555")
        self.assertEqual(out["seller"], "אלקטרה פאוור")
        self.assertEqual(out["category"], "חשבנות/חשמל")
        self.assertEqual(out["confidence"], "high")
        self.assertEqual(out["match_sender_contains"], "electra-power.co.il")

    def test_unknown_domain_derives_seller_low_confidence(self):
        out = self.s("noreply@some-shop.co.il", "invoice #55")
        self.assertEqual(out["seller"], "Some-Shop")
        self.assertEqual(out["confidence"], "low")
        self.assertEqual(out["match_sender_contains"], "some-shop.co.il")

    def test_product_keyword_mapping(self):
        self.assertEqual(self.s("x@y.com", "חשבונית מס קבלה 12")["product"], "חשבונית מס קבלה")
        self.assertEqual(self.s("x@y.com", "אישור תשלום ביט")["product"], "אישור תשלום")
        self.assertEqual(self.s("x@y.com", "הזמנה 9")["product"], "הזמנה")
        self.assertEqual(self.s("x@y.com", "no keywords here")["product"], "חשבונית")

    def test_promotional_subject_suggests_exclude(self):
        out = self.s("news@shop.com", "מבצע פרסומת ענק")
        self.assertEqual(out["kind"], "exclude")

    def test_category_from_subject(self):
        self.assertEqual(self.s("x@y.com", "חשבון מים רבעוני")["category"], "חשבנות/מיים")
        self.assertEqual(self.s("x@y.com", "ארנונה 2026")["category"], "חשבנות/ארנונה")


if __name__ == "__main__":
    unittest.main()
