import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import app as appmod
import history as history_mod
import claude_handoff


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
        receipt_roots.discover_roots = lambda categories_path=None: self._roots
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

    def test_browse_parses_folder_name_for_clean_view(self):
        by = {e["name"]: e for e in self._api().browse(str(self.root))["entries"]}
        r = by["2026_08_25 - סלקום - חשבונית - ofek"]
        self.assertEqual(r["title"], "סלקום - חשבונית")
        self.assertEqual(r["date_display"], "25 Aug 2026")
        self.assertEqual(r["account"], "ofek")
        # plain folder / file: title falls back to the raw name, no date/account
        self.assertEqual(by["חשבנות"]["title"], "חשבנות")
        self.assertEqual(by["חשבנות"]["date_display"], "")
        self.assertEqual(by["note.pdf"]["account"], "")

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


class TestUiStateApi(unittest.TestCase):
    def setUp(self):
        import ui_state
        self.p = Path(tempfile.mkdtemp()) / "ui_state.json"
        self._orig = ui_state.UI_STATE_FILE
        ui_state.UI_STATE_FILE = self.p
        self.addCleanup(setattr, ui_state, "UI_STATE_FILE", self._orig)

    def _api(self):
        return appmod.Api(scan_fn=lambda run_id, progress_cb: {
            "run_id": run_id, "saved": 0, "fallback": 0, "excluded": 0, "records": []})

    def test_get_ui_state_default_shape(self):
        s = self._api().get_ui_state()
        self.assertIn("hidden_roots", s)
        self.assertIn("fallbacks_simple", s)

    def test_set_ui_state_merges_and_persists(self):
        api = self._api()
        api.set_ui_state({"fallbacks_simple": True})
        api.set_ui_state({"hidden_roots": ["c:\\x"]})
        s = api.get_ui_state()
        self.assertTrue(s["fallbacks_simple"])
        self.assertEqual(s["hidden_roots"], ["c:\\x"])


class TestAskClaudeError(unittest.TestCase):
    def _api(self):
        return appmod.Api(scan_fn=lambda run_id, progress_cb: {
            "run_id": run_id, "saved": 0, "fallback": 0, "excluded": 0, "records": []})

    def test_error_prompt_is_single_line_no_double_quotes(self):
        p = claude_handoff.build_error_prompt('boom "x"\n  at line 5')
        self.assertNotIn("\n", p)
        self.assertNotIn('"', p)
        self.assertIn("Receipt Saver", p)
        self.assertIn("boom", p)

    def test_error_prompt_truncates_long_message(self):
        p = claude_handoff.build_error_prompt("z" * 5000)
        self.assertLess(len(p), 800 + len(claude_handoff.NO_AUTO_CHANGES) + 300)
        self.assertIn(" ...", p)                        # message body was cut
        self.assertNotIn("z" * 900, p)

    def test_prompts_forbid_auto_changes(self):
        for p in (claude_handoff.build_error_prompt("x"),
                  claude_handoff.build_prompt([{"account": "a", "sender": "s",
                      "subject": "j", "folder_path": "f"}]),
                  claude_handoff.build_receipt_prompt({"title": "t", "path": "p"})):
            self.assertIn("do not make any changes", p.lower())
            self.assertNotIn('"', p)

    def test_ask_claude_error_spawns_terminal(self):
        with mock.patch("claude_handoff.subprocess.Popen") as popen:
            res = self._api().ask_claude_error("something failed")
        self.assertTrue(res["ok"])
        self.assertTrue(popen.called)
        joined = " ".join(popen.call_args[0][0])
        self.assertIn("claude", joined)
        self.assertIn("something failed", joined)

    def test_ask_claude_error_reports_failure(self):
        with mock.patch("claude_handoff.subprocess.Popen", side_effect=OSError("nope")):
            res = self._api().ask_claude_error("x")
        self.assertFalse(res["ok"])
        self.assertIn("nope", res["error"])

    def test_ask_claude_receipt_spawns_terminal(self):
        entry = {"title": "Amazon - USB hub", "path": "C:\\r\\x",
                 "account": "ofek", "date": "25 Aug 2026"}
        with mock.patch("claude_handoff.subprocess.Popen") as popen:
            res = self._api().ask_claude_receipt(entry)
        self.assertTrue(res["ok"])
        joined = " ".join(popen.call_args[0][0])
        self.assertIn("claude", joined)
        self.assertIn("Amazon - USB hub", joined)

    def test_ask_claude_receipt_reports_failure(self):
        with mock.patch("claude_handoff.subprocess.Popen", side_effect=OSError("nope")):
            res = self._api().ask_claude_receipt({"title": "x"})
        self.assertFalse(res["ok"])
        self.assertIn("nope", res["error"])


class TestCategoryApi(unittest.TestCase):
    def setUp(self):
        import categories as C
        self.tmp = Path(tempfile.mkdtemp())
        self.f = self.tmp / "categories.json"
        self.elec = str(self.tmp / "קבלות" / "חשבנות" / "חשמל")
        self.f.write_text(json.dumps([
            {"id": "elec", "name": "חשמל", "destination": self.elec, "exclude": False,
             "seller": {"mode": "fixed", "value": "אלקטרה"},
             "product": {"mode": "extract", "source": "subject", "regex": r"חשבון (\S+)"},
             "match": [{"sender_contains": "iec.co.il"}]},
            {"id": "gas", "name": "גז", "destination": str(self.tmp / "גז"), "exclude": False,
             "seller": {"mode": "fixed", "value": "פזגז"},
             "product": {"mode": "extract", "source": "body", "regex": r"לקוח (\d+)"},
             "match": [{"sender_contains": "pazgas.co.il"}]},
            {"id": "excluded", "name": "(excluded)", "destination": None, "exclude": True,
             "seller": None, "product": None, "match": [{"sender_contains": "ads.com"}]},
        ], ensure_ascii=False), encoding="utf-8")
        self._orig = C.CATEGORIES_FILE
        C.CATEGORIES_FILE = self.f
        self.addCleanup(setattr, C, "CATEGORIES_FILE", self._orig)
        self.flog = self.tmp / "fallback_log.json"
        self.flog.write_text(json.dumps([
            {"message_id": "m1", "account": "ofek", "sender": '"IEC" <bill@iec.co.il>',
             "subject": "חשבון 03/2026", "date": "2026_08_25", "folder_name": "f",
             "folder_path": str(self.tmp / "f"), "resolved": False},
        ], ensure_ascii=False), encoding="utf-8")

    def _api(self):
        return appmod.Api(scan_fn=lambda run_id, progress_cb: None,
                          fallback_log_path=self.flog)

    def _read(self):
        return json.loads(self.f.read_text(encoding="utf-8"))

    def test_list(self):
        self.assertEqual([c["id"] for c in self._api().list_categories()],
                         ["elec", "gas", "excluded"])

    def test_add_update_delete(self):
        api = self._api()
        res = api.category_add("מים", {"destination": str(self.tmp / "מים"),
                                       "seller": {"mode": "fixed", "value": "מי ראשון"}})
        self.assertTrue(res["ok"])
        cid = next(c["id"] for c in self._read() if c["name"] == "מים")
        self.assertEqual(next(c for c in self._read() if c["id"] == cid)["destination"],
                         str(self.tmp / "מים"))
        self.assertTrue(api.category_update(cid, {"seller": None})["ok"])
        self.assertIsNone(next(c for c in self._read() if c["id"] == cid)["seller"])
        self.assertTrue(api.category_delete(cid)["ok"])
        self.assertNotIn(cid, [c["id"] for c in self._read()])

    def test_update_with_bad_regex_reports_error_and_writes_nothing(self):
        before = self._read()
        res = self._api().category_update("elec", {"product": {
            "mode": "extract", "source": "subject", "regex": "("}})
        self.assertFalse(res["ok"])
        self.assertIn("regex", res["error"])
        self.assertEqual(self._read(), before)

    def test_merge(self):
        self.assertTrue(self._api().category_merge("gas", "elec")["ok"])
        self.assertEqual([c["id"] for c in self._read()], ["elec", "excluded"])
        self.assertEqual([m["sender_contains"] for m in self._read()[0]["match"]],
                         ["iec.co.il", "pazgas.co.il"])

    def test_add_and_remove_match(self):
        api = self._api()
        self.assertTrue(api.category_add_match("gas", {"sender_contains": "shared.co",
                                                      "subject_contains": "גז"})["ok"])
        self.assertEqual(len(self._read()[1]["match"]), 2)
        self.assertTrue(api.category_remove_match("gas", 1)["ok"])
        self.assertEqual(len(self._read()[1]["match"]), 1)

    def test_bad_ops_report_error(self):
        api = self._api()
        self.assertFalse(api.category_delete("ghost")["ok"])
        self.assertFalse(api.category_merge("elec", "elec")["ok"])
        self.assertFalse(api.category_update("ghost", {"name": "x"})["ok"])

    # -- previews ---------------------------------------------------------
    def test_preview_extract_on_subject_needs_no_network(self):
        api = self._api()
        with mock.patch.object(api, "_fetch_text", side_effect=AssertionError("no fetch")):
            res = api.preview_extract("m1", "subject", r"חשבון (\S+)")
        self.assertEqual(res, {"ok": True, "value": "03_2026"})

    def test_preview_extract_sender_name_and_miss(self):
        api = self._api()
        self.assertEqual(api.preview_extract("m1", "sender_name", r"(.+)")["value"], "IEC")
        self.assertEqual(api.preview_extract("m1", "subject", r"xyz(\d)"),
                         {"ok": True, "value": None})

    def test_preview_extract_bad_regex(self):
        res = self._api().preview_extract("m1", "subject", "(")
        self.assertFalse(res["ok"])
        self.assertIn("regex", res["error"])

    def test_preview_extract_body_fetches_once_and_caches(self):
        api = self._api()
        with mock.patch.object(api, "_fetch_text",
                               return_value={"body": "מספר לקוח 4711 תודה"}) as f:
            self.assertEqual(api.preview_extract("m1", "body", r"לקוח (\d+)")["value"], "4711")
            api.preview_extract("m1", "body", r"(\d+)")
        self.assertEqual(f.call_count, 1)

    def test_preview_extract_body_fetch_failure_is_reported(self):
        api = self._api()
        with mock.patch.object(api, "_fetch_text", side_effect=RuntimeError("offline")):
            res = api.preview_extract("m1", "body", r"(\d+)")
        self.assertFalse(res["ok"])
        self.assertIn("offline", res["error"])

    def test_preview_category_resolves_names_for_this_mail(self):
        res = self._api().preview_category("m1", "elec")
        self.assertEqual(res, {"ok": True, "seller": "אלקטרה", "product": "03_2026",
                               "destination": self.elec})

    def test_preview_category_with_body_spec_degrades_when_offline(self):
        api = self._api()
        with mock.patch.object(api, "_fetch_text", side_effect=RuntimeError("offline")):
            res = api.preview_category("m1", "gas")
        self.assertTrue(res["ok"])
        self.assertEqual(res["seller"], "פזגז")
        self.assertTrue(res["product"])                    # app suggestion

    def test_keyword_suggestions_without_body_needs_no_network(self):
        api = self._api()
        with mock.patch.object(api, "_fetch_text", side_effect=AssertionError("no fetch")):
            res = api.keyword_suggestions("m1", False)
        self.assertTrue(res["ok"])
        self.assertEqual(res["sender"][0], "iec.co.il")
        self.assertIn("IEC", res["sender"])
        self.assertEqual(res["body"], [])

    def test_keyword_suggestions_with_body(self):
        api = self._api()
        with mock.patch.object(api, "_fetch_text",
                               return_value={"body": "מספר הזמנה 77 חברת החשמל לישראל"}):
            res = api.keyword_suggestions("m1", True)
        self.assertEqual(res["body"][0], "מספר הזמנה")

    def test_keyword_suggestions_body_failure_is_reported(self):
        api = self._api()
        with mock.patch.object(api, "_fetch_text", side_effect=RuntimeError("offline")):
            res = api.keyword_suggestions("m1", True)
        self.assertTrue(res["ok"])
        self.assertEqual(res["body"], [])
        self.assertIn("offline", res["body_error"])

    def test_attachment_count_from_log_or_folder(self):
        api = self._api()
        folder = self.tmp / "f"
        folder.mkdir()
        for n in ("email.pdf", "inv.pdf", "logo.png", "b.xlsx"):
            (folder / n).write_text("x", encoding="utf-8")
        self.assertEqual(api.suggest_fallback("m1")["attachment_count"], 2)
        rows = json.loads(self.flog.read_text(encoding="utf-8"))
        rows[0]["attachment_count"] = 0
        self.flog.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(api.suggest_fallback("m1")["attachment_count"], 0)

    def test_destination_suggestions_ranked_by_use(self):
        import receipt_roots
        hist = self.tmp / "history.json"
        hist.write_text(json.dumps([
            {"id": "a", "action": "DOWNLOADED", "folder_path": self.elec + r"\2026 - a"},
            {"id": "b", "action": "RESOLVED", "folder_path": self.elec + r"\2026 - b"},
            {"id": "c", "action": "FALLBACK", "folder_path": str(self.tmp / "manual" / "x")},
        ], ensure_ascii=False), encoding="utf-8")
        roots = [{"label": "קבלות", "path": str(self.tmp / "קבלות")},
                 {"label": "לטיפול ידני", "path": str(self.tmp / "manual")}]
        with mock.patch.object(history_mod, "HISTORY_FILE", hist), \
             mock.patch.object(receipt_roots, "discover_roots", lambda *a, **k: roots), \
             mock.patch.object(receipt_roots, "MANUAL_DIR", Path(roots[1]["path"])):
            out = self._api().destination_suggestions()
        paths = [d["path"] for d in out]
        self.assertEqual(paths[0], self.elec)                  # 1 category + 2 history
        self.assertEqual(out[0]["label"], "קבלות › חשבנות › חשמל")
        self.assertIn(str(self.tmp / "קבלות"), paths)          # roots always offered
        self.assertNotIn(str(self.tmp / "manual"), paths)      # never the fallback dir
        self.assertFalse(any(p.startswith(str(self.tmp / "manual")) for p in paths))


class TestPickFolder(unittest.TestCase):
    def _api(self, dialog):
        api = appmod.Api(scan_fn=lambda run_id, progress_cb: None)
        api._window = mock.Mock()
        api._window.create_file_dialog = dialog
        return api

    def test_returns_selected_path(self):
        res = self._api(lambda *a, **k: ("C:\\Some\\Folder",)).pick_folder()
        self.assertEqual(res, {"ok": True, "path": "C:\\Some\\Folder"})

    def test_cancel_returns_null_path(self):
        self.assertEqual(self._api(lambda *a, **k: None).pick_folder(),
                         {"ok": True, "path": None})
        self.assertEqual(self._api(lambda *a, **k: ()).pick_folder(),
                         {"ok": True, "path": None})

    def test_reports_failure(self):
        def boom(*a, **k):
            raise RuntimeError("no dialog")
        res = self._api(boom).pick_folder()
        self.assertFalse(res["ok"])
        self.assertIn("no dialog", res["error"])

    def test_no_window_is_graceful(self):
        api = appmod.Api(scan_fn=lambda run_id, progress_cb: None)
        res = api.pick_folder()
        self.assertEqual(res, {"ok": True, "path": None})


class TestAppVersion(unittest.TestCase):
    def test_full_version_starts_with_semver(self):
        import version
        v = version.full_version()
        self.assertRegex(v, r"^\d+\.\d+\.\d+")

    def test_api_exposes_version(self):
        api = appmod.Api(scan_fn=lambda run_id, progress_cb: {
            "run_id": run_id, "saved": 0, "fallback": 0, "excluded": 0, "records": []})
        import version
        self.assertEqual(api.app_version(), version.full_version())


class TestSearchReceipts(unittest.TestCase):
    def setUp(self):
        import receipt_roots
        self.tmp = Path(tempfile.mkdtemp())
        self.r1 = self.tmp / "קבלות"
        self.r2 = self.tmp / "נכסים"
        (self.r1 / "חשבנות" / "2026_08_25 - סלקום - חשבונית - ofek").mkdir(parents=True)
        (self.r2 / "2026_07_01 - סלקום - חשבונית - family").mkdir(parents=True)
        (self.r1 / "readme_סלקום.txt").write_text("x", encoding="utf-8")
        self._roots = [{"label": "קבלות", "path": str(self.r1)},
                       {"label": "נכסים", "path": str(self.r2)}]
        self._orig = receipt_roots.discover_roots
        receipt_roots.discover_roots = lambda categories_path=None: self._roots
        self.addCleanup(setattr, receipt_roots, "discover_roots", self._orig)

    def _api(self):
        return appmod.Api(scan_fn=lambda run_id, progress_cb: None)

    def test_finds_matches_across_all_roots(self):
        res = self._api().search_receipts("סלקום")
        names = sorted(r["rel"] for r in res["results"])
        self.assertIn(os.path.join("חשבנות", "2026_08_25 - סלקום - חשבונית - ofek"), names)
        self.assertIn("2026_07_01 - סלקום - חשבונית - family", names)
        kinds = {r["kind"] for r in res["results"] if r["is_dir"]}
        self.assertEqual(kinds, {"receipt-folder"})
        self.assertEqual({r["root_label"] for r in res["results"]}, {"קבלות", "נכסים"})

    def test_short_query_returns_empty(self):
        self.assertEqual(self._api().search_receipts("a")["results"], [])

    def test_limit_and_truncated_flag(self):
        res = self._api().search_receipts("סלקום", limit=1)
        self.assertEqual(len(res["results"]), 1)
        self.assertTrue(res["truncated"])


class TestRunScanTerminates(unittest.TestCase):
    def _run(self, scan_fn):
        api = appmod.Api(scan_fn=scan_fn)
        api._run = {"status": "running", "events": [], "summary": None}
        api._run_scan("RID")
        return api._run

    def test_done_emitted_even_when_scan_raises(self):
        def boom(run_id, progress_cb):
            raise RuntimeError("nope")
        run = self._run(boom)
        dones = [e for e in run["events"] if e["type"] == "done"]
        self.assertEqual(len(dones), 1)
        self.assertEqual(run["status"], "error")

    def test_single_done_on_normal_scan(self):
        def ok(run_id, progress_cb):
            return {"run_id": run_id, "saved": 2, "fallback": 0, "excluded": 0, "records": []}
        run = self._run(ok)
        dones = [e for e in run["events"] if e["type"] == "done"]
        self.assertEqual(len(dones), 1)
        self.assertEqual(dones[0]["saved"], 2)


if __name__ == "__main__":
    unittest.main()
