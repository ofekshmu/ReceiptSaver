import json
import os
import tempfile
import unittest
from pathlib import Path

import receipt_roots


class TestDiscoverRoots(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.rules = self.tmp / "custom_rules.json"

    def _write(self, rules):
        self.rules.write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")

    def test_fixed_roots_present_and_ordered(self):
        self._write([])
        roots = receipt_roots.discover_roots(rules_path=self.rules)
        labels = [r["label"] for r in roots]
        self.assertEqual(labels[:3], ["קבלות", "לטיפול ידני", "Japanologia"])

    def test_custom_base_dirs_appended_first_seen_order(self):
        self._write([
            {"match_sender_contains": "a.com", "base_dir": r"C:\X\נכסים"},
            {"match_sender_contains": "b.com", "base_dir": r"C:\X\נכסים\שלום שבאזי 7"},
            {"match_sender_contains": "c.com", "base_dir": r"C:\X\נכסים"},  # dup
            {"match_sender_contains": "d.com"},                            # no base_dir
        ])
        roots = receipt_roots.discover_roots(rules_path=self.rules)
        tail = [r["label"] for r in roots[3:]]
        self.assertEqual(tail, ["נכסים", "שלום שבאזי 7"])

    def test_base_dir_equal_to_receipts_dir_collapses(self):
        self._write([{"match_sender_contains": "a.com",
                      "base_dir": str(receipt_roots.RECEIPTS_DIR)}])
        roots = receipt_roots.discover_roots(rules_path=self.rules)
        paths = [os.path.normcase(os.path.normpath(r["path"])) for r in roots]
        self.assertEqual(len(paths), len(set(paths)))

    def test_unreadable_rules_falls_back_to_fixed_roots(self):
        self.rules.write_text("{ not json", encoding="utf-8")
        roots = receipt_roots.discover_roots(rules_path=self.rules)
        self.assertEqual([r["label"] for r in roots],
                         ["קבלות", "לטיפול ידני", "Japanologia"])


class TestIsWithinRoots(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "root").mkdir()
        (self.tmp / "root" / "sub").mkdir()
        (self.tmp / "root2").mkdir()
        self.roots = [{"label": "r", "path": str(self.tmp / "root")}]

    def test_root_itself_is_within(self):
        self.assertTrue(receipt_roots.is_within_roots(str(self.tmp / "root"), self.roots))

    def test_nested_path_is_within(self):
        self.assertTrue(receipt_roots.is_within_roots(
            str(self.tmp / "root" / "sub"), self.roots))

    def test_sibling_is_not_within(self):
        self.assertFalse(receipt_roots.is_within_roots(
            str(self.tmp / "root2"), self.roots))

    def test_parent_traversal_is_not_within(self):
        self.assertFalse(receipt_roots.is_within_roots(
            str(self.tmp / "root" / ".." / "root2"), self.roots))

    def test_prefix_lookalike_is_not_within(self):
        (self.tmp / "rootX").mkdir()
        self.assertFalse(receipt_roots.is_within_roots(
            str(self.tmp / "rootX"), self.roots))


if __name__ == "__main__":
    unittest.main()
