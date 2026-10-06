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
        self.rules = self.tmp / "rules.json"
        self.rules.write_text(json.dumps({
            "roots": [{"id": "elec", "name": "חשמל", "color": "#d6efcf",
                       "folder": str(self.receipts / "חשבנות" / "חשמל")},
                      {"id": "water", "name": "מים", "color": "#cbece8",
                       "folder": str(self.receipts / "חשבנות" / "מיים")}],
            "rules": [{"id": "electra", "name": "אלקטרה", "root": "elec", "exclude": False,
                       "seller": FIXED("אלקטרה פאוור"), "product": FIXED("חשבונית חשמל"),
                       "match": [{"sender_contains": ["electra-power.co.il", "heshbon"],
                                  "subject_contains": "חשבון"}]}],
        }, ensure_ascii=False), encoding="utf-8")

    def s(self, sender, subject):
        return fallback_ops.suggest({"sender": sender, "subject": subject},
                                    rules_path=self.rules, receipts_dir=self.receipts)

    def test_known_sender_reuses_rule_and_root(self):
        out = self.s("Heshbon@electra-power.co.il", "חשבונית 555")
        self.assertEqual((out["rule_id"], out["root_id"]), ("electra", "elec"))
        self.assertEqual(out["seller"], "אלקטרה פאוור")
        self.assertEqual(out["destination"], str(self.receipts / "חשבנות" / "חשמל"))
        self.assertEqual(out["confidence"], "high")
        self.assertEqual(out["match_sender_contains"], "electra-power.co.il")

    def test_unknown_domain_low_confidence_no_rule(self):
        out = self.s("noreply@some-shop.co.il", "invoice #55")
        self.assertEqual(out["seller"], "Some-Shop")
        self.assertIsNone(out["rule_id"])
        self.assertIsNone(out["root_id"])
        self.assertEqual(out["confidence"], "low")
        self.assertEqual(out["kind"], "file")

    def test_subject_keyword_preselects_matching_root(self):
        out = self.s("x@y.com", "חשבון מים רבעוני")
        self.assertEqual(out["root_id"], "water")
        self.assertEqual(out["confidence"], "medium")

    def test_product_keyword_and_promo(self):
        self.assertEqual(self.s("x@y.com", "חשבונית מס קבלה 12")["product"], "חשבונית מס קבלה")
        self.assertEqual(self.s("news@shop.com", "מבצע פרסומת ענק")["kind"], "exclude")


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
        self.elec = self.receipts / "חשבנות" / "חשמל"
        self.rules = self.tmp / "rules.json"
        self.rules.write_text(json.dumps({
            "roots": [{"id": "main", "name": "קבלות", "folder": str(self.receipts), "color": "#f9d5dc"},
                      {"id": "elec", "name": "חשמל", "folder": str(self.elec), "color": "#d6efcf"}],
            "rules": [{"id": "iec", "name": "חשמל", "root": "elec", "exclude": False,
                       "seller": FIXED("אלקטרה"), "product": FIXED("חשבונית חשמל"),
                       "match": [{"sender_contains": "iec.co.il"}]}],
        }, ensure_ascii=False), encoding="utf-8")
        self.paths = dict(fallback_log_path=self.flog, cleanup_log_path=self.clog,
                          history_path=self.hist, rules_path=self.rules,
                          receipts_dir=self.receipts)

    def apply(self, decision, **kw):
        return fallback_ops.apply_decision(self._entry(), decision, **self.paths, **kw)

    def _entry(self):
        return json.loads(self.flog.read_text(encoding="utf-8"))[0]

    def _data(self):
        return json.loads(self.rules.read_text(encoding="utf-8"))

    def _rule(self, rid):
        return next(r for r in self._data()["rules"] if r["id"] == rid)

    def _rows(self):
        return json.loads(self.hist.read_text(encoding="utf-8"))


class TestApplyDecision(_Base):
    def test_new_rule_in_existing_root(self):
        res = self.apply({
            "kind": "new_rule", "rule_name": "שופ", "root_id": "main",
            "seller": name("שופ"), "product": name("חשבונית"),
            "match": {"sender_contains": "shop.co.il", "subject_contains": ""},
        })
        self.assertTrue(res["ok"])
        rule = self._rule(res["rule_id"])
        self.assertEqual((rule["name"], rule["root"]), ("שופ", "main"))
        self.assertEqual(rule["seller"], FIXED("שופ"))
        self.assertEqual(rule["match"], [{"sender_contains": "shop.co.il"}])
        dst = self.receipts / "2026_08_25 - שופ - חשבונית - ofek"
        self.assertTrue((dst / "email.pdf").exists())
        self.assertFalse(self.src.exists())
        self.assertTrue(self._entry()["resolved"])
        row = self._rows()[0]
        self.assertEqual((row["action"], row["resolution"]), ("RESOLVED", "rule"))
        self.assertEqual((row["rule_name"], row["root_name"]), ("שופ", "קבלות"))
        self.assertRegex(row["resolved_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d$")

    def test_new_rule_in_new_root(self):
        folder = self.receipts / "קניות"
        res = self.apply({
            "kind": "new_rule", "rule_name": "שופ",
            "new_root": {"name": "קניות", "folder": str(folder)},
            "seller": name("שופ", every_mail=False),
            "product": name("12345", extract={"source": "subject", "regex": r"#(\d+)",
                                              "fallback": "הזמנה"}),
            "match": {"sender_contains": ["shop.co.il"], "attachments": "one"},
        })
        self.assertTrue(res["ok"])
        data = self._data()
        root = next(r for r in data["roots"] if r["id"] == res["root_id"])
        self.assertEqual((root["name"], root["folder"]), ("קניות", str(folder)))
        self.assertTrue(root["color"].startswith("#"))
        rule = self._rule(res["rule_id"])
        self.assertIsNone(rule["seller"])
        self.assertEqual(rule["product"]["mode"], "extract")
        self.assertEqual(rule["match"], [{"sender_contains": "shop.co.il", "attachments": "one"}])
        self.assertTrue((folder / "2026_08_25 - שופ - 12345 - ofek" / "email.pdf").exists())

    def test_invalid_input_changes_nothing(self):
        before = self.rules.read_text(encoding="utf-8")
        for decision in (
            {"kind": "new_rule", "rule_name": "x", "root_id": "main", "seller": name("x"),
             "product": name("y", extract={"source": "subject", "regex": "("}),
             "match": {"sender_contains": "shop.co.il"}},
            {"kind": "new_rule", "rule_name": "x", "root_id": "ghost", "seller": name("x"),
             "product": name("y"), "match": {"sender_contains": "shop.co.il"}},
            {"kind": "new_rule", "rule_name": "x", "new_root": {"name": "n", "folder": ""},
             "seller": name("x"), "product": name("y"), "match": {"sender_contains": "s.co"}},
            {"kind": "new_rule", "rule_name": "x", "root_id": "main", "seller": name("x"),
             "product": name("y"), "match": {"body_contains": "only body"}},
            {"kind": "rule", "rule_id": "ghost", "match": {"sender_contains": "shop.co.il"}},
            {"kind": "category"},
        ):
            self.assertFalse(self.apply(decision)["ok"], decision)
        self.assertEqual(self.rules.read_text(encoding="utf-8"), before)
        self.assertTrue(self.src.exists())

    def test_existing_rule_gets_new_alternative_and_files_into_its_root(self):
        res = self.apply({
            "kind": "rule", "rule_id": "iec",
            "seller": {"value": "אלקטרה"}, "product": {"value": "חשבון מרץ"},
            "match": {"sender_contains": "shop.co.il"},
        })
        self.assertTrue(res["ok"])
        rule = self._rule("iec")
        self.assertEqual([m["sender_contains"] for m in rule["match"]], ["iec.co.il", "shop.co.il"])
        self.assertEqual(rule["product"], FIXED("חשבונית חשמל"))     # spec untouched
        self.assertTrue((self.elec / "2026_08_25 - אלקטרה - חשבון מרץ - ofek" / "email.pdf").exists())
        self.assertEqual(self._rows()[0]["rule_id"], "iec")

    def test_once_writes_no_rule(self):
        before = self.rules.read_text(encoding="utf-8")
        res = self.apply({"kind": "once", "destination": str(self.receipts / "misc"),
                          "seller": {"value": "שופ"}, "product": {"value": "חשבונית"}},
                         resolved_by="claude")
        self.assertTrue(res["ok"])
        self.assertEqual(self.rules.read_text(encoding="utf-8"), before)
        self.assertTrue((self.receipts / "misc" / "2026_08_25 - שופ - חשבונית - ofek"
                         / "email.pdf").exists())
        self.assertEqual(self._entry()["resolved_by"], "claude")

    def test_exclude_adds_to_shared_rule_and_deletes_folder(self):
        self.apply({"kind": "exclude",
                    "match": {"sender_contains": "shop.co.il", "subject_contains": "פרסומת"}})
        x = self._rule("excluded")
        self.assertTrue(x["exclude"])
        self.assertIsNone(x["root"])
        self.assertEqual(x["match"], [{"sender_contains": "shop.co.il", "subject_contains": "פרסומת"}])
        self.assertFalse(self.src.exists())
        self.assertEqual(json.loads(self.clog.read_text(encoding="utf-8"))[-1]["action"], "DELETED")
        self.assertEqual(self._rows()[0]["resolution"], "exclude")

    def test_skip_dismisses(self):
        self.assertTrue(self.apply({"kind": "skip"})["ok"])
        self.assertTrue(self.src.exists())
        self.assertTrue(self._entry()["resolved"])
        self.assertEqual(self._rows()[0]["resolution"], "dismissed")


class TestApplyDecisionCreatesHistoryRow(_Base):
    """A fallback with no pre-existing history row must still land in History."""
    history_exists = False

    def test_rule_resolution_appends_row(self):
        self.apply({"kind": "new_rule", "rule_name": "שופ", "root_id": "main",
                    "seller": name("שופ"), "product": name("חשבונית"),
                    "match": {"sender_contains": "shop.co.il"}})
        rows = self._rows()
        self.assertEqual([r["id"] for r in rows], ["ofek:m1"])
        self.assertTrue(rows[0]["folder_path"].endswith("שופ - חשבונית - ofek"))

    def test_existing_row_is_updated_not_duplicated(self):
        self.hist.write_text(json.dumps([{"id": "ofek:m1", "action": "FALLBACK"}]), encoding="utf-8")
        self.apply({"kind": "once", "seller": {"value": "שופ"}, "product": {"value": "x"}})
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["seller"], "שופ")


if __name__ == "__main__":
    unittest.main()
