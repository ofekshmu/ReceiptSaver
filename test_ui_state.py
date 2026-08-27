import json, tempfile, unittest
from pathlib import Path
import ui_state


class TestUiState(unittest.TestCase):
    def setUp(self):
        self.p = Path(tempfile.mkdtemp()) / "ui_state.json"

    def test_load_defaults_when_absent(self):
        s = ui_state.load(path=self.p)
        self.assertEqual(s, {"hidden_roots": [], "fallbacks_simple": False})

    def test_load_defaults_when_corrupt(self):
        self.p.write_text("{bad", encoding="utf-8")
        self.assertEqual(ui_state.load(path=self.p)["fallbacks_simple"], False)

    def test_save_merges_partial_patch(self):
        ui_state.save({"fallbacks_simple": True}, path=self.p)
        s = ui_state.load(path=self.p)
        self.assertTrue(s["fallbacks_simple"])
        self.assertEqual(s["hidden_roots"], [])

    def test_second_save_sees_first(self):
        ui_state.save({"hidden_roots": ["c:\\x"]}, path=self.p)
        merged = ui_state.save({"fallbacks_simple": True}, path=self.p)
        self.assertEqual(merged["hidden_roots"], ["c:\\x"])
        self.assertTrue(merged["fallbacks_simple"])

    def test_write_is_atomic(self):
        ui_state.save({"fallbacks_simple": True}, path=self.p)
        json.loads(self.p.read_text(encoding="utf-8"))
        self.assertFalse(self.p.with_suffix(".json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
