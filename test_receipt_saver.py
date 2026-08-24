import shutil
import tempfile
import unittest
from pathlib import Path

from receipt_saver import parse_date, match_custom, unique_folder


class TestParseDate(unittest.TestCase):
    def test_rfc2822_gmail_date(self):
        self.assertEqual(parse_date("Thu, 9 Jul 2026 14:47:00 +0300"), "2026_07_09")

    def test_iso8601_graph_date(self):
        self.assertEqual(parse_date("2026-07-09T11:47:23Z"), "2026_07_09")

    def test_garbage_falls_back_to_today(self):
        result = parse_date("not a date")
        self.assertRegex(result, r"^\d{4}_\d{2}_\d{2}$")


class TestSternumPayslipRule(unittest.TestCase):
    def test_extracts_month_and_year_from_body(self):
        result = match_custom(
            "billing@sternum-sec.com",
            "some subject line",
            'היי אופק,\n\nמצ"ב תלוש שכר לחודש יוני 2026.\n\nבברכה,',
        )
        seller, product, category, base_dir = result
        self.assertEqual(seller, "משכורת")
        self.assertEqual(product, "תלוש שכר לחודש יוני 2026")
        self.assertEqual(base_dir, Path(r"C:\Users\ofeks\OneDrive\Ofek\Work\Sternum\משכורות"))

    def test_falls_back_to_static_product_if_regex_does_not_match(self):
        result = match_custom(
            "billing@sternum-sec.com",
            "some subject line",
            "תלוש שכר בפורמט שונה לגמרי",
        )
        seller, product, category, base_dir = result
        self.assertEqual(product, "תלוש שכר")

    def test_extracts_month_and_year_despite_nbsp_and_line_wrap(self):
        # Outlook's HTML-to-text body conversion can leave non-breaking
        # spaces (U+00A0) and mid-sentence newlines in place of normal spaces.
        body = 'היי אופק,\n\nמצ"ב תלוש\xa0שכר\nלחודש\xa0יוני 2026.\n\nבברכה,'
        result = match_custom("billing@sternum-sec.com", "some subject line", body)
        seller, product, category, base_dir = result
        self.assertEqual(product, "תלוש שכר לחודש יוני 2026")


class TestUpappRule(unittest.TestCase):
    # hyp.co.il is a shared payment platform used by many merchants, so the
    # rule must not match on sender domain alone — a real incident tagged an
    # unrelated Ichilov WELL payment as the Icon gym.
    def test_matches_genuine_upapp_receipt(self):
        result = match_custom("noreply@hyp.co.il", "חשבונית מס / קבלה עבור תשלום ל-upapp")
        self.assertIsNotNone(result)
        seller, product, category, base_dir = result
        self.assertEqual(seller, "upapp")
        self.assertEqual(product, "כניסה לחדר כושר אייקון")

    def test_does_not_match_unrelated_hyp_sender(self):
        result = match_custom("noreply@hyp.co.il", "אישור תשלום - איכילוב WELL")
        self.assertIsNone(result)


class TestUniqueFolder(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_returns_requested_name_when_free(self):
        folder, name = unique_folder(self.tmp, "2026_04_12 - upapp - x - yuval")
        self.assertEqual(name, "2026_04_12 - upapp - x - yuval")
        self.assertEqual(folder, self.tmp / name)

    def test_appends_suffix_on_collision_instead_of_nesting(self):
        (self.tmp / "2026_04_12 - upapp - x - yuval").mkdir()
        folder, name = unique_folder(self.tmp, "2026_04_12 - upapp - x - yuval")
        self.assertEqual(name, "2026_04_12 - upapp - x - yuval (2)")
        self.assertEqual(folder, self.tmp / name)

    def test_increments_past_multiple_collisions(self):
        (self.tmp / "dup").mkdir()
        (self.tmp / "dup (2)").mkdir()
        folder, name = unique_folder(self.tmp, "dup")
        self.assertEqual(name, "dup (3)")


if __name__ == "__main__":
    unittest.main()
