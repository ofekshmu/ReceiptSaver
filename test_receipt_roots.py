import json
import os
import tempfile
import unittest
from pathlib import Path

import receipt_roots


class TestDiscoverRoots(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cats = self.tmp / "categories.json"

    def _write(self, dests):
        cats = [{"id": f"c{i}", "name": f"c{i}", "destination": d, "exclude": False,
                 "seller": None, "product": None, "match": []}
                for i, d in enumerate(dests)]
        self.cats.write_text(json.dumps(cats, ensure_ascii=False), encoding="utf-8")

    def roots(self):
        return receipt_roots.discover_roots(categories_path=self.cats)

    def test_fixed_roots_present_and_ordered(self):
        self._write([])
        self.assertEqual([r["label"] for r in self.roots()][:3],
                         ["קבלות", "לטיפול ידני", "Japanologia"])

    def test_outside_destinations_added_nested_ones_fold_into_parent(self):
        self._write([r"C:\X\נכסים\שלום שבאזי 7\חשבנות", r"C:\X\נכסים", r"C:\Y\מילואים",
                     r"C:\X\נכסים", None])
        self.assertEqual([r["label"] for r in self.roots()[3:]], ["נכסים", "מילואים"])

    def test_destination_under_receipts_dir_is_not_a_new_root(self):
        self._write([str(receipt_roots.RECEIPTS_DIR / "חשבנות" / "חשמל"),
                     str(receipt_roots.RECEIPTS_DIR)])
        self.assertEqual(len(self.roots()), 3)

    def test_unreadable_categories_fall_back_to_fixed_roots(self):
        self.cats.write_text("{ not json", encoding="utf-8")
        self.assertEqual([r["label"] for r in self.roots()],
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
