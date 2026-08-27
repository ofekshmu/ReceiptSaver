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


class TestApplyDecision(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.receipts = self.tmp / "קבלות"
        self.manual = self.receipts / "_לטיפול ידני"
        self.manual.mkdir(parents=True)
        self.rules = self.tmp / "custom_rules.json"
        self.rules.write_text("[]", encoding="utf-8")
        self.flog = self.tmp / "fallback_log.json"
        self.clog = self.tmp / "cleanup_log.json"
        self.hist = self.tmp / "history.json"
        # one fallback folder with a file in it
        self.src = self.manual / "2026_08_25 - who - mystery - ofek"
        self.src.mkdir()
        (self.src / "email.pdf").write_text("x", encoding="utf-8")
        self.flog.write_text(json.dumps([{
            "message_id": "m1", "account": "ofek", "account_email": "o@x.com",
            "date": "2026_08_25", "sender": "who@shop.co.il", "subject": "mystery",
            "folder_name": self.src.name, "folder_path": str(self.src), "resolved": False,
        }], ensure_ascii=False), encoding="utf-8")
        self.hist.write_text(json.dumps([{
            "id": "ofek:m1", "action": "FALLBACK", "seller": None, "product": None,
            "category": None, "folder_name": self.src.name, "folder_path": str(self.src),
        }], ensure_ascii=False), encoding="utf-8")
        self.paths = dict(rules_path=self.rules, fallback_log_path=self.flog,
                          cleanup_log_path=self.clog, history_path=self.hist,
                          receipts_dir=self.receipts, manual_dir=self.manual)

    def _entry(self):
        return json.loads(self.flog.read_text(encoding="utf-8"))[0]

    def test_compute_destination_with_category(self):
        dst = fallback_ops.compute_destination(
            self._entry(), {"seller": "S", "product": "P", "category": "חשבנות/חשמל",
                            "base_dir": None}, receipts_dir=self.receipts)
        self.assertEqual(dst, self.receipts / "חשבנות" / "חשמל" /
                         "2026_08_25 - S - P - ofek")

    def test_rule_decision_writes_rule_moves_folder_resolves(self):
        fallback_ops.apply_decision(self._entry(), {
            "kind": "rule", "seller": "שופ", "product": "חשבונית",
            "category": None, "base_dir": None,
            "match_sender_contains": "shop.co.il", "match_subject_contains": None,
        }, **self.paths)
        rules = json.loads(self.rules.read_text(encoding="utf-8"))
        self.assertEqual(rules[-1]["match_sender_contains"], "shop.co.il")
        self.assertEqual(rules[-1]["seller"], "שופ")
        self.assertFalse(self.src.exists())
        dst = self.receipts / "2026_08_25 - שופ - חשבונית - ofek"
        self.assertTrue((dst / "email.pdf").exists())
        self.assertTrue(self._entry()["resolved"])
        row = json.loads(self.hist.read_text(encoding="utf-8"))[0]
        self.assertEqual(row["action"], "RESOLVED")
        self.assertEqual(row["seller"], "שופ")
        self.assertEqual(row["resolution"], "rule")

    def test_once_decision_moves_without_writing_rule(self):
        fallback_ops.apply_decision(self._entry(), {
            "kind": "once", "seller": "שופ", "product": "חשבונית",
            "category": None, "base_dir": None,
            "match_sender_contains": "shop.co.il", "match_subject_contains": None,
        }, **self.paths)
        self.assertEqual(json.loads(self.rules.read_text(encoding="utf-8")), [])
        self.assertTrue((self.receipts / "2026_08_25 - שופ - חשבונית - ofek" / "email.pdf").exists())
        self.assertEqual(self._entry()["resolved"], True)

    def test_exclude_decision_writes_exclude_rule_deletes_folder_logs_cleanup(self):
        fallback_ops.apply_decision(self._entry(), {
            "kind": "exclude", "seller": None, "product": None, "category": None,
            "base_dir": None, "match_sender_contains": "shop.co.il",
            "match_subject_contains": None,
        }, **self.paths)
        rules = json.loads(self.rules.read_text(encoding="utf-8"))
        self.assertTrue(rules[-1]["exclude"])
        self.assertFalse(self.src.exists())
        cleanup = json.loads(self.clog.read_text(encoding="utf-8"))
        self.assertEqual(cleanup[-1]["action"], "DELETED")
        self.assertTrue(self._entry()["resolved"])

    def test_skip_decision_is_noop(self):
        fallback_ops.apply_decision(self._entry(), {"kind": "skip"}, **self.paths)
        self.assertTrue(self.src.exists())
        self.assertFalse(self._entry()["resolved"])


if __name__ == "__main__":
    unittest.main()
