import json
import shutil
import tempfile
import unittest
from pathlib import Path

import history
import relocate

FIXED = lambda v: {"mode": "fixed", "value": v}


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.rec = self.tmp / "קבלות"
        self.new = self.rec / "Anthropic"
        self.manual = self.rec / "_לטיפול ידני"
        for p in (self.new, self.manual, self.rec / "חשבנות"):
            p.mkdir(parents=True)
        self.data = {
            "roots": [{"id": "main", "name": "קבלות", "folder": str(self.rec), "color": "#fff"},
                      {"id": "ant", "name": "Anthropic", "folder": str(self.new), "color": "#fff"}],
            "rules": [
                {"id": "anthropic", "name": "Anthropic", "root": "ant", "exclude": False,
                 "seller": FIXED("Anthropic"), "product": FIXED("Claude Pro מנוי"),
                 "match": [{"sender_contains": "mail.anthropic.com"}]},
                {"id": "ksp", "name": "KSP", "root": "main", "exclude": False,
                 "seller": FIXED("KSP"), "product": None, "match": [{"sender_contains": "ksp.co.il"}]},
                {"id": "grow", "name": "Grow", "root": "main", "exclude": False,
                 "seller": {"mode": "template", "template": "{subject_words:8-10}"}, "product": None,
                 "match": [{"sender_contains": "grow.co.il"}]},
            ]}

    def mk(self, name, parent=None):
        p = (parent or self.rec) / name
        p.mkdir()
        (p / "email.pdf").write_text("x", encoding="utf-8")
        return p

    def find(self, ids, rows=(), old=None):
        return relocate.filed_folders(ids, old or self.rec, data=self.data,
                                      history_rows=list(rows), manual_dir=self.manual)


class TestFiledFolders(_Base):
    def test_finds_folders_by_name_including_duplicates(self):
        self.mk("2026_03_31 - Anthropic - Claude Pro מנוי - ofek")
        self.mk("2026_08_26 - Anthropic - Claude Pro מנוי - ofek (2)")
        self.mk("2026_08_26 - KSP - הזמנה - ofek")
        self.mk("Anthropic stuff")                              # not the scan's shape
        found = self.find(["anthropic"])
        self.assertEqual([f["name"] for f in found],
                         ["2026_03_31 - Anthropic - Claude Pro מנוי - ofek",
                          "2026_08_26 - Anthropic - Claude Pro מנוי - ofek (2)"])
        self.assertTrue(all(f["checked"] and f["source"] == "name" for f in found))

    def test_history_rows_count_and_win_over_names(self):
        p = self.mk("2026_10_07 - Something else - x - ofek")
        q = self.mk("2026_09_07 - Anthropic - Claude Pro מנוי - ofek")
        rows = [{"folder_path": str(p), "rule_id": "anthropic"},
                {"folder_path": str(q), "rule_id": "ksp"}]           # History says another rule
        found = self.find(["anthropic"], rows)
        self.assertEqual([(f["name"], f["source"]) for f in found],
                         [("2026_10_07 - Something else - x - ofek", "history")])

    def test_older_rows_without_a_rule_id_are_worked_out(self):
        p = self.mk("2026_01_01 - Anthropic, PBC - Receipt - ofek")
        rows = [{"folder_path": str(p), "rule_source": "custom",
                 "sender": "invoice@mail.anthropic.com", "subject": "Your receipt"}]
        self.assertEqual([f["source"] for f in self.find(["anthropic"], rows)], ["history"])

    def test_never_offers_root_folders_manual_or_nested(self):
        self.mk("2026_01_01 - Anthropic - Claude Pro מנוי - ofek", self.rec / "חשבנות")
        self.mk("2026_01_02 - Anthropic - Claude Pro מנוי - ofek", self.manual)
        self.assertEqual(self.find(["anthropic"]), [])

    def test_template_names_need_fixed_text(self):
        self.mk("2026_01_01 - איזי טו גיפט - חשבונית - ofek")
        self.assertEqual(self.find(["grow"]), [])                # nothing fixed in the name
        self.data["rules"][2]["seller"] = {"mode": "template", "template": "Grow {subject_word:2}"}
        self.mk("2026_01_02 - Grow 44 - חשבונית - ofek")
        self.assertEqual([f["name"] for f in self.find(["grow"])], ["2026_01_02 - Grow 44 - חשבונית - ofek"])

    def test_a_name_another_rule_here_also_fits_is_unticked(self):
        self.data["rules"].append({"id": "ksp2", "name": "KSP old", "root": "main", "exclude": False,
                                   "seller": FIXED("KSP"), "product": None,
                                   "match": [{"sender_contains": "ksp.com"}]})
        self.mk("2026_01_01 - KSP - הזמנה - ofek")
        found = self.find(["ksp"])
        self.assertEqual((found[0]["checked"], found[0]["also"]), (False, ["KSP old"]))

    def test_missing_old_folder_or_no_rules(self):
        self.assertEqual(self.find(["anthropic"], old=self.tmp / "gone"), [])
        self.assertEqual(self.find([]), [])


class TestMoveFolders(_Base):
    def setUp(self):
        super().setUp()
        self.hist = self.tmp / "history.json"

    def test_moves_keeps_names_and_repoints_history(self):
        a = self.mk("2026_03_31 - Anthropic - Claude Pro מנוי - ofek")
        b = self.mk("2026_04_04 - Anthropic - Claude Pro מנוי - ofek")
        self.mk("2026_04_04 - Anthropic - Claude Pro מנוי - ofek", self.new)     # clash
        self.hist.write_text(json.dumps([{"id": "x", "folder_path": str(a), "folder_name": a.name,
                                          "category": None}, {"id": "y", "folder_path": "C:/other"}],
                                        ensure_ascii=False), encoding="utf-8")
        out = relocate.move_folders([str(a), str(b)], self.new, history_path=self.hist,
                                    manual_dir=self.manual)
        self.assertEqual(out, {"moved": 2, "failed": []})
        self.assertFalse(a.exists() or b.exists())
        self.assertTrue((self.new / a.name / "email.pdf").exists())
        self.assertTrue((self.new / (b.name + " (2)")).exists())
        rows = history.load(self.hist)
        self.assertEqual(rows[0]["folder_path"], str(self.new / a.name))
        self.assertEqual(rows[1]["folder_path"], "C:/other")

    def test_reports_missing_and_refuses_manual(self):
        m = self.mk("2026_01_01 - x - y - ofek", self.manual)
        out = relocate.move_folders([str(self.rec / "gone"), str(m)], self.new,
                                    history_path=self.hist, manual_dir=self.manual)
        self.assertEqual(out["moved"], 0)
        self.assertEqual([f["error"] for f in out["failed"]], ["not found", "in _לטיפול ידני"])
        self.assertTrue(m.exists())


class TestApi(_Base):
    def test_move_only_into_a_root_folder(self):
        import app as appmod, rules as R
        rules_file = self.tmp / "rules.json"
        R.save(self.data, rules_file)
        self.addCleanup(setattr, R, "RULES_FILE", R.RULES_FILE)
        R.RULES_FILE = rules_file
        api = appmod.Api(scan_fn=lambda *a: {}, fallback_log_path=self.tmp / "f.json")
        self.assertFalse(api.move_filed_folders([], str(self.tmp / "elsewhere"))["ok"])
        self.addCleanup(setattr, history, "HISTORY_FILE", history.HISTORY_FILE)
        history.HISTORY_FILE = self.tmp / "history.json"
        a = self.mk("2026_03_31 - Anthropic - Claude Pro מנוי - ofek")
        found = api.rule_filed_folders(["anthropic"], str(self.rec))
        self.assertEqual([f["path"] for f in found["folders"]], [str(a)])
        self.assertEqual(api.move_filed_folders([str(a)], str(self.new))["moved"], 1)


if __name__ == "__main__":
    unittest.main()
