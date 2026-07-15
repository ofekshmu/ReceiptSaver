import unittest
from receipt_saver import parse_date


class TestParseDate(unittest.TestCase):
    def test_rfc2822_gmail_date(self):
        self.assertEqual(parse_date("Thu, 9 Jul 2026 14:47:00 +0300"), "2026_07_09")

    def test_iso8601_graph_date(self):
        self.assertEqual(parse_date("2026-07-09T11:47:23Z"), "2026_07_09")

    def test_garbage_falls_back_to_today(self):
        result = parse_date("not a date")
        self.assertRegex(result, r"^\d{4}_\d{2}_\d{2}$")


if __name__ == "__main__":
    unittest.main()
