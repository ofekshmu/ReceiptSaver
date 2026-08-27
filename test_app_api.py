import json
import tempfile
import time
import unittest
from pathlib import Path

import app as appmod
import history as history_mod


class TestApi(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._hist = history_mod.HISTORY_FILE
        history_mod.HISTORY_FILE = self.tmp / "history.json"
        history_mod.HISTORY_FILE.write_text(json.dumps([
            {"id": f"ofek:{i}", "action": "DOWNLOADED", "seller": f"S{i}",
             "subject": f"sub {i}", "sender": "a@b.com"} for i in range(4)
        ], ensure_ascii=False), encoding="utf-8")
        self.flog = self.tmp / "fallback_log.json"
        self.flog.write_text(json.dumps([
            {"message_id": "m1", "account": "ofek", "sender": "x@y.co.il",
             "subject": "mystery", "date": "2026_08_25",
             "folder_name": "f", "folder_path": str(self.tmp / "f"), "resolved": False},
            {"message_id": "m2", "resolved": True},
        ], ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        history_mod.HISTORY_FILE = self._hist

    def _api(self, scan_fn=None):
        return appmod.Api(
            scan_fn=scan_fn or (lambda run_id, progress_cb: {"run_id": run_id,
                                "saved": 0, "fallback": 0, "excluded": 0, "records": []}),
            fallback_log_path=self.flog,
        )

    def test_get_history_pages_newest_first(self):
        api = self._api()
        page = api.get_history(0, 2)
        self.assertEqual([r["id"] for r in page], ["ofek:3", "ofek:2"])

    def test_get_fallbacks_returns_only_unresolved(self):
        api = self._api()
        fbs = api.get_fallbacks()
        self.assertEqual([f["message_id"] for f in fbs], ["m1"])

    def test_suggest_fallback_returns_suggestion_fields(self):
        api = self._api()
        out = api.suggest_fallback("m1")
        self.assertIn("seller", out)
        self.assertIn("confidence", out)

    def test_start_scan_runs_fn_and_collects_events(self):
        def fake_scan(run_id, progress_cb):
            progress_cb({"type": "account", "label": "ofek", "candidates": 1})
            progress_cb({"type": "mail", "record": {"id": "ofek:9", "action": "DOWNLOADED"}})
            return {"run_id": run_id, "saved": 1, "fallback": 0, "excluded": 0, "records": []}
        api = self._api(scan_fn=fake_scan)
        api.start_scan()
        for _ in range(50):
            if not api.scan_running():
                break
            time.sleep(0.05)
        self.assertFalse(api.scan_running())
        run = api.get_run()
        self.assertEqual(run["status"], "done")
        self.assertEqual(run["summary"]["saved"], 1)
        types = [e["type"] for e in run["events"]]
        self.assertEqual(types, ["account", "mail", "done"])

    def test_start_scan_is_single_flight(self):
        def slow_scan(run_id, progress_cb):
            time.sleep(0.3)
            return {"run_id": run_id, "saved": 0, "fallback": 0, "excluded": 0, "records": []}
        api = self._api(scan_fn=slow_scan)
        api.start_scan()
        second = api.start_scan()
        self.assertEqual(second["status"], "busy")
        for _ in range(50):
            if not api.scan_running():
                break
            time.sleep(0.05)


class TestExplorerApi(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "קבלות"
        (self.root / "חשבנות").mkdir(parents=True)
        (self.root / "2026_08_25 - סלקום - חשבונית - ofek").mkdir()
        (self.root / "2026_01_02 - Wolt - x - family").mkdir()
        (self.root / "note.pdf").write_bytes(b"x" * 2048)
        (self.root / "aaa.txt").write_text("hi", encoding="utf-8")
        self._roots = [{"label": "קבלות", "path": str(self.root)}]

    def _api(self):
        import receipt_roots
        a = appmod.Api(scan_fn=lambda run_id, progress_cb: {
            "run_id": run_id, "saved": 0, "fallback": 0, "excluded": 0, "records": []})
        self._orig = receipt_roots.discover_roots
        receipt_roots.discover_roots = lambda rules_path=None: self._roots
        self.addCleanup(setattr, receipt_roots, "discover_roots", self._orig)
        return a

    def test_list_roots_shape(self):
        r = self._api().list_roots()
        self.assertEqual(r[0]["label"], "קבלות")
        self.assertTrue(r[0]["exists"])
        self.assertIn("path", r[0])

    def test_browse_sorts_dirs_first_dated_desc_then_files(self):
        entries = self._api().browse(str(self.root))["entries"]
        names = [e["name"] for e in entries]
        self.assertEqual(names, [
            "2026_08_25 - סלקום - חשבונית - ofek",
            "2026_01_02 - Wolt - x - family",
            "חשבנות",
            "aaa.txt",
            "note.pdf",
        ])

    def test_browse_marks_kinds_and_size(self):
        by = {e["name"]: e for e in self._api().browse(str(self.root))["entries"]}
        self.assertEqual(by["2026_08_25 - סלקום - חשבונית - ofek"]["kind"], "receipt-folder")
        self.assertEqual(by["חשבנות"]["kind"], "folder")
        self.assertEqual(by["note.pdf"]["kind"], "pdf")
        self.assertEqual(by["aaa.txt"]["kind"], "file")
        self.assertEqual(by["note.pdf"]["size"], 2048)
        self.assertIsNone(by["חשבנות"]["size"])

    def test_browse_crumbs(self):
        res = self._api().browse(str(self.root / "חשבנות"))
        self.assertEqual([c["name"] for c in res["crumbs"]], ["קבלות", "חשבנות"])
        self.assertEqual(res["crumbs"][-1]["path"], str(self.root / "חשבנות"))
        self.assertEqual(res["label"], "קבלות")

    def test_browse_rejects_path_outside_roots(self):
        res = self._api().browse(str(self.tmp / "elsewhere"))
        self.assertIn("error", res)
        self.assertEqual(res.get("entries", []), [])

    def test_browse_missing_folder_under_root(self):
        res = self._api().browse(str(self.root / "nope"))
        self.assertEqual(res["error"], "folder not found")
        self.assertEqual(res["entries"], [])
        self.assertEqual([c["name"] for c in res["crumbs"]], ["קבלות", "nope"])


if __name__ == "__main__":
    unittest.main()
