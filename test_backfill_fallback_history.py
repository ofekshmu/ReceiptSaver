import json
import tempfile
import unittest
from pathlib import Path

import backfill_fallback_history as bf


class TestBackfill(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.flog = self.tmp / "fallback_log.json"
        self.hist = self.tmp / "history.json"
        self.flog.write_text(json.dumps([
            {"message_id": "a", "account": "ofek", "account_email": "o@x.com",
             "date": "2026_08_01", "sender": "s1@shop.co.il", "subject": "one",
             "folder_name": "2026_08_01 - Shop - חשבונית - ofek",
             "folder_path": "C:\\קבלות\\2026_08_01 - Shop - חשבונית - ofek",
             "resolved": True},
            {"message_id": "b", "account": "ofek", "date": "2026_08_02",
             "sender": "s2@promo.com", "subject": "promo", "folder_name": "x",
             "folder_path": "", "resolved": True},               # excluded
            {"message_id": "c", "account": "yuval", "date": "2026_08_03",
             "sender": "s3@x.com", "subject": "still open", "folder_name": "y",
             "folder_path": "C:\\m\\y", "resolved": False},        # unresolved
        ], ensure_ascii=False), encoding="utf-8")

    def _run(self, **kw):
        return bf.backfill(fallback_log_path=self.flog, history_path=self.hist, **kw)

    def _rows(self):
        return json.loads(self.hist.read_text(encoding="utf-8"))

    def test_adds_rows_only_for_resolved_entries(self):
        res = self._run()
        self.assertEqual(res, {"scanned": 3, "added": 2})
        ids = {r["id"] for r in self._rows()}
        self.assertEqual(ids, {"ofek:a", "ofek:b"})

    def test_row_content_and_resolution_kind(self):
        self._run()
        rows = {r["id"]: r for r in self._rows()}
        self.assertEqual(rows["ofek:a"]["action"], "RESOLVED")
        self.assertEqual(rows["ofek:a"]["resolution"], "backfilled")
        self.assertEqual(rows["ofek:a"]["seller"], "Shop")
        self.assertEqual(rows["ofek:a"]["product"], "חשבונית")
        self.assertEqual(rows["ofek:a"]["subject"], "one")
        self.assertEqual(rows["ofek:b"]["resolution"], "exclude")   # empty folder_path

    def test_dry_run_writes_nothing(self):
        res = self._run(dry_run=True)
        self.assertEqual(res["added"], 2)
        self.assertFalse(self.hist.exists())

    def test_patches_old_row_missing_fields_then_idempotent(self):
        # an older RESOLVED row with no resolve stamp gets patched, not skipped
        self.hist.write_text(json.dumps([{"id": "ofek:a", "action": "RESOLVED"}],
                                        ensure_ascii=False), encoding="utf-8")
        res = self._run()
        self.assertEqual(res["added"], 2)                           # ofek:a patched + ofek:b added
        self.assertEqual(len(self._rows()), 2)                      # no duplicate row
        rows = {r["id"]: r for r in self._rows()}
        self.assertEqual(rows["ofek:a"]["resolved_by"], "unknown")
        self.assertEqual(self._run()["added"], 0)                   # now fully stamped → no-op

    def test_resolved_by_stamp_honored_else_unknown(self):
        rows = json.loads(self.flog.read_text(encoding="utf-8"))
        rows[0]["resolved_by"] = "claude"
        rows[0]["resolved_at"] = "2026-08-10T09:00:00"
        self.flog.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        self._run()
        h = {r["id"]: r for r in self._rows()}
        self.assertEqual(h["ofek:a"]["resolved_by"], "claude")
        self.assertEqual(h["ofek:a"]["resolved_at"], "2026-08-10T09:00:00")
        self.assertEqual(h["ofek:b"]["resolved_by"], "unknown")
        self.assertEqual(h["ofek:b"]["resolved_at"], "2026-08-02")   # from entry date

    def test_missing_log_is_graceful(self):
        res = bf.backfill(fallback_log_path=self.tmp / "nope.json",
                          history_path=self.hist)
        self.assertEqual(res, {"scanned": 0, "added": 0})


if __name__ == "__main__":
    unittest.main()
