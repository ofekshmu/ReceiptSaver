import json
import tempfile
import unittest
from pathlib import Path

import history


def _rec(mid, **over):
    r = {
        "id": mid, "run_id": "2026-08-27T10:00:00", "handled_at": "2026-08-27T10:00:01",
        "account": "ofek", "account_email": "o@x.com", "date": "2026_08_25",
        "sender": "a@b.com", "subject": "s", "action": "DOWNLOADED",
        "seller": "S", "product": "P", "category": None,
        "folder_name": "f", "folder_path": "C:\\f", "files": ["email.pdf"],
        "rule_source": "custom",
    }
    r.update(over)
    return r


class TestHistory(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "history.json"

    def test_append_then_load_roundtrip(self):
        history.append(_rec("ofek:1"), path=self.tmp)
        history.append(_rec("ofek:2"), path=self.tmp)
        rows = history.load(path=self.tmp)
        self.assertEqual([r["id"] for r in rows], ["ofek:1", "ofek:2"])

    def test_append_dedups_by_id(self):
        history.append(_rec("ofek:1", seller="First"), path=self.tmp)
        history.append(_rec("ofek:1", seller="Second"), path=self.tmp)
        rows = history.load(path=self.tmp)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["seller"], "First")

    def test_update_patches_matching_row(self):
        history.append(_rec("ofek:1"), path=self.tmp)
        history.update("ofek:1", {"action": "RESOLVED", "seller": "New"}, path=self.tmp)
        row = history.load(path=self.tmp)[0]
        self.assertEqual(row["action"], "RESOLVED")
        self.assertEqual(row["seller"], "New")
        self.assertEqual(row["product"], "P")  # untouched

    def test_update_missing_id_is_noop(self):
        history.append(_rec("ofek:1"), path=self.tmp)
        history.update("ofek:999", {"action": "RESOLVED"}, path=self.tmp)
        self.assertEqual(history.load(path=self.tmp)[0]["action"], "DOWNLOADED")

    def test_page_returns_newest_first(self):
        for i in range(5):
            history.append(_rec(f"ofek:{i}"), path=self.tmp)
        page = history.page(offset=0, limit=2, path=self.tmp)
        self.assertEqual([r["id"] for r in page], ["ofek:4", "ofek:3"])
        page2 = history.page(offset=2, limit=2, path=self.tmp)
        self.assertEqual([r["id"] for r in page2], ["ofek:2", "ofek:1"])

    def test_load_missing_file_returns_empty(self):
        self.assertEqual(history.load(path=self.tmp), [])

    def test_write_is_atomic_valid_json(self):
        history.append(_rec("ofek:1"), path=self.tmp)
        json.loads(self.tmp.read_text(encoding="utf-8"))  # must not raise
        self.assertFalse(self.tmp.with_suffix(".json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
