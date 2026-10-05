import json
import tempfile
import unittest
from pathlib import Path

import fallback_ops

FIXED = lambda v: {"mode": "fixed", "value": v}


def name(value, every_mail=True, extract=None):
    return {"value": value, "every_mail": every_mail, "extract": extract}


class TestSuggest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.receipts = self.tmp / "קבלות"
        self.cats = self.tmp / "categories.json"
        self.cats.write_text(json.dumps([
            {"id": "electra", "name": "אלקטרה", "exclude": False,
             "destination": str(self.receipts / "חשבנות" / "חשמל"),
             "seller": FIXED("אלקטרה פאוור"), "product": FIXED("חשבונית חשמל"),
             "match": [{"sender_contains": "electra-power.co.il", "subject_contains": "חשבון"}]},
        ], ensure_ascii=False), encoding="utf-8")

    def s(self, sender, subject):
        return fallback_ops.suggest({"sender": sender, "subject": subject},
                                    categories_path=self.cats, receipts_dir=self.receipts)

    def test_known_sender_reuses_category_high_confidence(self):
        out = self.s("Heshbon@electra-power.co.il", "חשבונית 555")
        self.assertEqual(out["category_id"], "electra")
        self.assertEqual(out["seller"], "אלקטרה פאוור")
        self.assertEqual(out["destination"], str(self.receipts / "חשבנות" / "חשמל"))
        self.assertEqual(out["confidence"], "high")
        self.assertEqual(out["match_sender_contains"], "electra-power.co.il")

    def test_unknown_domain_derives_seller_low_confidence(self):
        out = self.s("noreply@some-shop.co.il", "invoice #55")
        self.assertEqual(out["seller"], "Some-Shop")
        self.assertIsNone(out["category_id"])
        self.assertEqual(out["destination"], str(self.receipts))
        self.assertEqual(out["confidence"], "low")
        self.assertEqual(out["kind"], "category")

    def test_product_keyword_mapping(self):
        self.assertEqual(self.s("x@y.com", "חשבונית מס קבלה 12")["product"], "חשבונית מס קבלה")
        self.assertEqual(self.s("x@y.com", "no keywords here")["product"], "חשבונית")

    def test_promotional_subject_suggests_exclude(self):
        self.assertEqual(self.s("news@shop.com", "מבצע פרסומת ענק")["kind"], "exclude")

    def test_destination_guess_from_subject(self):
        self.assertEqual(self.s("x@y.com", "חשבון מים רבעוני")["destination"],
                         str(self.receipts / "חשבנות" / "מיים"))
        self.assertEqual(self.s("x@y.com", "ארנונה 2026")["confidence"], "medium")


class _Base(unittest.TestCase):
    history_exists = True

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.receipts = self.tmp / "קבלות"
        self.manual = self.receipts / "_לטיפול ידני"
        self.manual.mkdir(parents=True)
        self.flog = self.tmp / "fallback_log.json"
        self.clog = self.tmp / "cleanup_log.json"
        self.hist = self.tmp / "history.json"
        self.src = self.manual / "2026_08_25 - who - mystery - ofek"
        self.src.mkdir()
        (self.src / "email.pdf").write_text("x", encoding="utf-8")
        self.flog.write_text(json.dumps([{
            "message_id": "m1", "account": "ofek", "account_email": "o@x.com",
            "date": "2026_08_25", "sender": "who@shop.co.il", "subject": "mystery",
            "folder_name": self.src.name, "folder_path": str(self.src), "resolved": False,
        }], ensure_ascii=False), encoding="utf-8")
        if self.history_exists:
            self.hist.write_text(json.dumps([{
                "id": "ofek:m1", "action": "FALLBACK", "seller": None, "product": None,
                "category": None, "folder_name": self.src.name, "folder_path": str(self.src),
            }], ensure_ascii=False), encoding="utf-8")
        self.cats = self.tmp / "categories.json"
        self.cats.write_text("[]", encoding="utf-8")
        self.paths = dict(fallback_log_path=self.flog, cleanup_log_path=self.clog,
                          history_path=self.hist, categories_path=self.cats,
                          receipts_dir=self.receipts)

    def apply(self, decision, **kw):
        return fallback_ops.apply_decision(self._entry(), decision, **self.paths, **kw)

    def _entry(self):
        return json.loads(self.flog.read_text(encoding="utf-8"))[0]

    def _cats(self):
        return json.loads(self.cats.read_text(encoding="utf-8"))

    def _rows(self):
        return json.loads(self.hist.read_text(encoding="utf-8"))


class TestApplyDecision(_Base):
    def test_new_category_with_fixed_names(self):
        res = self.apply({
            "kind": "new_category", "category_name": "שופ",
            "destination": str(self.receipts / "קניות"),
            "seller": name("שופ"), "product": name("חשבונית"),
            "match": {"sender_contains": "shop.co.il", "subject_contains": ""},
        })
        self.assertTrue(res["ok"])
        cat = self._cats()[0]
        self.assertEqual(cat["name"], "שופ")
        self.assertEqual(cat["destination"], str(self.receipts / "קניות"))
        self.assertEqual(cat["seller"], FIXED("שופ"))
        self.assertEqual(cat["product"], FIXED("חשבונית"))
        self.assertEqual(cat["match"], [{"sender_contains": "shop.co.il"}])
        dst = self.receipts / "קניות" / "2026_08_25 - שופ - חשבונית - ofek"
        self.assertTrue((dst / "email.pdf").exists())
        self.assertFalse(self.src.exists())
        self.assertTrue(self._entry()["resolved"])
        row = self._rows()[0]
        self.assertEqual((row["action"], row["resolution"]), ("RESOLVED", "category"))
        self.assertEqual(row["category_name"], "שופ")
        self.assertEqual(row["resolved_by"], "user")
        self.assertRegex(row["resolved_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d$")
        self.assertEqual(self._entry()["resolved_at"], row["resolved_at"])

    def test_new_category_stores_list_conditions_and_attachments(self):
        self.apply({
            "kind": "new_category", "category_name": "שופ", "destination": str(self.receipts),
            "seller": name("שופ"), "product": name("חשבונית"),
            "match": {"sender_contains": ["shop.co.il"], "subject_contains": ["קבלה", "הזמנה"],
                      "exclude_body_contains": ["בוטלה"], "body_contains": [],
                      "attachments": "one"},
        })
        self.assertEqual(self._cats()[0]["match"], [{
            "sender_contains": "shop.co.il", "subject_contains": ["קבלה", "הזמנה"],
            "exclude_body_contains": "בוטלה", "attachments": "one"}])

    def test_rule_with_only_body_conditions_is_rejected(self):
        res = self.apply({"kind": "exclude", "match": {"body_contains": ["x"]}})
        self.assertFalse(res["ok"])

    def test_unticked_name_is_this_mail_only(self):
        self.apply({
            "kind": "new_category", "category_name": "שופ", "destination": str(self.receipts),
            "seller": name("שופ", every_mail=False), "product": name("קבלה 7", every_mail=False),
            "match": {"sender_contains": "shop.co.il"},
        })
        cat = self._cats()[0]
        self.assertIsNone(cat["seller"])
        self.assertIsNone(cat["product"])
        self.assertTrue((self.receipts / "2026_08_25 - שופ - קבלה 7 - ofek").exists())

    def test_extract_spec_saved_with_fallback_and_value_used_for_this_mail(self):
        self.apply({
            "kind": "new_category", "category_name": "שופ", "destination": str(self.receipts),
            "seller": name("שופ"),
            "product": name("12345", extract={"source": "subject", "regex": r"#(\d+)",
                                              "fallback": "הזמנה"}),
            "match": {"sender_contains": "shop.co.il"},
        })
        self.assertEqual(self._cats()[0]["product"], {
            "mode": "extract", "source": "subject", "regex": r"#(\d+)", "fallback": "הזמנה"})
        self.assertTrue((self.receipts / "2026_08_25 - שופ - 12345 - ofek").exists())

    def test_bad_regex_is_rejected_before_anything_moves(self):
        res = self.apply({
            "kind": "new_category", "category_name": "x", "destination": str(self.receipts),
            "seller": name("x"), "product": name("y", extract={"source": "subject", "regex": "("}),
            "match": {"sender_contains": "shop.co.il"},
        })
        self.assertFalse(res["ok"])
        self.assertEqual(self._cats(), [])
        self.assertTrue(self.src.exists())

    def test_new_category_needs_a_match_condition(self):
        res = self.apply({"kind": "new_category", "category_name": "x",
                          "destination": str(self.receipts), "seller": name("x"),
                          "product": name("y"), "match": {}})
        self.assertFalse(res["ok"])
        self.assertTrue(self.src.exists())

    def test_existing_category_appends_rule_uses_its_destination_and_one_off_names(self):
        dest = self.receipts / "חשבנות" / "חשמל"
        self.cats.write_text(json.dumps([{
            "id": "elec", "name": "חשמל", "destination": str(dest), "exclude": False,
            "seller": FIXED("אלקטרה"), "product": FIXED("חשבונית חשמל"),
            "match": [{"sender_contains": "iec.co.il"}],
        }], ensure_ascii=False), encoding="utf-8")
        self.apply({
            "kind": "category", "category_id": "elec",
            "destination": str(self.receipts / "ignored"),
            "seller": {"value": "אלקטרה"}, "product": {"value": "חשבון מרץ"},
            "match": {"sender_contains": "shop.co.il"},
        })
        cat = self._cats()[0]
        self.assertEqual([m["sender_contains"] for m in cat["match"]], ["iec.co.il", "shop.co.il"])
        self.assertEqual(cat["product"], FIXED("חשבונית חשמל"))     # spec untouched
        self.assertTrue((dest / "2026_08_25 - אלקטרה - חשבון מרץ - ofek" / "email.pdf").exists())
        self.assertEqual(self._rows()[0]["category_id"], "elec")

    def test_missing_category_errors(self):
        res = self.apply({"kind": "category", "category_id": "ghost",
                          "seller": {"value": "x"}, "product": {"value": "y"},
                          "match": {"sender_contains": "shop.co.il"}})
        self.assertFalse(res["ok"])

    def test_once_writes_no_category(self):
        res = self.apply({"kind": "once", "destination": str(self.receipts / "misc"),
                          "seller": {"value": "שופ"}, "product": {"value": "חשבונית"}},
                         resolved_by="claude")
        self.assertTrue(res["ok"])
        self.assertEqual(self._cats(), [])
        self.assertTrue((self.receipts / "misc" / "2026_08_25 - שופ - חשבונית - ofek"
                         / "email.pdf").exists())
        self.assertEqual(self._entry()["resolved_by"], "claude")
        self.assertEqual(self._rows()[0]["resolution"], "once")

    def test_once_without_destination_files_into_receipts_root(self):
        self.apply({"kind": "once", "seller": {"value": "a"}, "product": {"value": "b"}})
        self.assertTrue((self.receipts / "2026_08_25 - a - b - ofek").exists())

    def test_exclude_appends_rule_to_shared_bucket_and_deletes_folder(self):
        self.apply({"kind": "exclude",
                    "match": {"sender_contains": "shop.co.il", "subject_contains": "פרסומת"}})
        xc = next(c for c in self._cats() if c["id"] == "excluded")
        self.assertTrue(xc["exclude"])
        self.assertEqual(xc["match"], [{"sender_contains": "shop.co.il",
                                        "subject_contains": "פרסומת"}])
        self.assertFalse(self.src.exists())
        self.assertEqual(json.loads(self.clog.read_text(encoding="utf-8"))[-1]["action"],
                         "DELETED")
        self.assertTrue(self._entry()["resolved"])
        self.assertEqual(self._rows()[0]["resolution"], "exclude")

    def test_skip_dismisses_keeps_folder_and_records_history(self):
        res = self.apply({"kind": "skip"})
        self.assertTrue(res["ok"])
        self.assertTrue(self.src.exists())
        self.assertTrue(self._entry()["resolved"])
        self.assertEqual(self._entry()["folder_path"], str(self.src))
        row = self._rows()[0]
        self.assertEqual((row["action"], row["resolution"]), ("RESOLVED", "dismissed"))
        self.assertEqual(self._cats(), [])

    def test_unknown_kind_errors(self):
        self.assertFalse(self.apply({"kind": "rule"})["ok"])


class TestApplyDecisionCreatesHistoryRow(_Base):
    """A fallback with no pre-existing history row must still land in History."""
    history_exists = False

    def test_category_resolution_appends_row(self):
        self.apply({"kind": "new_category", "category_name": "שופ",
                    "destination": str(self.receipts), "seller": name("שופ"),
                    "product": name("חשבונית"), "match": {"sender_contains": "shop.co.il"}})
        rows = self._rows()
        self.assertEqual([r["id"] for r in rows], ["ofek:m1"])
        self.assertEqual(rows[0]["subject"], "mystery")
        self.assertTrue(rows[0]["folder_path"].endswith("שופ - חשבונית - ofek"))

    def test_dismiss_appends_row(self):
        self.apply({"kind": "skip"})
        self.assertEqual(self._rows()[0]["resolution"], "dismissed")

    def test_existing_row_is_updated_not_duplicated(self):
        self.hist.write_text(json.dumps([{"id": "ofek:m1", "action": "FALLBACK",
                                          "subject": "mystery"}]), encoding="utf-8")
        self.apply({"kind": "once", "seller": {"value": "שופ"}, "product": {"value": "x"}})
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["seller"], "שופ")


if __name__ == "__main__":
    unittest.main()
