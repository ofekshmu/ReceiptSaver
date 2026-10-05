import shutil
import tempfile
import unittest
from pathlib import Path

from receipt_saver import parse_date, unique_folder
from categories import match_category
import receipt_saver
import history as history_mod


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
        result = match_category(
            "billing@sternum-sec.com",
            "some subject line",
            'היי אופק,\n\nמצ"ב תלוש שכר לחודש יוני 2026.\n\nבברכה,',
        )
        seller, product, dest = result
        self.assertEqual(seller, "משכורת")
        self.assertEqual(product, "תלוש שכר לחודש יוני 2026")
        self.assertEqual(dest, Path(r"C:\Users\ofeks\OneDrive\Ofek\Work\Sternum\משכורות"))

    def test_falls_back_to_static_product_if_regex_does_not_match(self):
        result = match_category(
            "billing@sternum-sec.com",
            "some subject line",
            "תלוש שכר בפורמט שונה לגמרי",
        )
        seller, product, dest = result
        self.assertEqual(product, "תלוש שכר")

    def test_extracts_month_and_year_despite_nbsp_and_line_wrap(self):
        # Outlook's HTML-to-text body conversion can leave non-breaking
        # spaces (U+00A0) and mid-sentence newlines in place of normal spaces.
        body = 'היי אופק,\n\nמצ"ב תלוש\xa0שכר\nלחודש\xa0יוני 2026.\n\nבברכה,'
        result = match_category("billing@sternum-sec.com", "some subject line", body)
        seller, product, dest = result
        self.assertEqual(product, "תלוש שכר לחודש יוני 2026")


class TestUpappRule(unittest.TestCase):
    # hyp.co.il is a shared payment platform used by many merchants, so the
    # rule must not match on sender domain alone — a real incident tagged an
    # unrelated Ichilov WELL payment as the Icon gym.
    def test_matches_genuine_upapp_receipt(self):
        result = match_category("noreply@hyp.co.il", "חשבונית מס / קבלה עבור תשלום ל-upapp")
        self.assertIsNotNone(result)
        seller, product, dest = result
        self.assertEqual(seller, "upapp")
        self.assertEqual(product, "כניסה לחדר כושר אייקון")

    def test_does_not_match_unrelated_hyp_sender(self):
        result = match_category("noreply@hyp.co.il", "אישור תשלום - איכילוב WELL")
        self.assertIsNone(result)


class TestCategoryRouting(unittest.TestCase):
    """process_message files a category match into its destination."""

    def setUp(self):
        import json, categories
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.dest = self.tmp / "bills" / "חשמל"
        cats_file = self.tmp / "categories.json"
        cats_file.write_text(json.dumps([
            {"id": "elec", "name": "חשמל", "destination": str(self.dest),
             "seller": {"mode": "fixed", "value": "חברת חשמל"},
             "product": {"mode": "extract", "source": "subject", "regex": r"חשבון (\d+)"},
             "exclude": False, "match": [{"sender_contains": "iectest.co.il"}]},
            {"id": "excluded", "name": "(excluded)", "exclude": True, "destination": None,
             "seller": None, "product": None,
             "match": [{"sender_contains": "spam.example", "subject_contains": "מבצע"}]},
        ], ensure_ascii=False), encoding="utf-8")
        orig = categories.CATEGORIES_FILE
        categories.CATEGORIES_FILE = cats_file
        self.addCleanup(setattr, categories, "CATEGORIES_FILE", orig)
        for name in ("save_email_pdf", "create_ticktick_task"):
            self.addCleanup(setattr, receipt_saver, name, getattr(receipt_saver, name))
            setattr(receipt_saver, name, lambda *a, **k: None)
        self.addCleanup(setattr, history_mod, "HISTORY_FILE", history_mod.HISTORY_FILE)
        history_mod.HISTORY_FILE = self.tmp / "history.json"

    def _msg(self, sender, subject):
        return {"id": "m1", "sender": sender, "subject": subject,
                "date_raw": "Thu, 9 Jul 2026 14:47:00 +0300", "is_sent": False,
                "body_text": "", "body_html": "<p>x</p>", "first_attachment_name": "",
                "attachments": lambda: [], "link": ""}

    def test_saved_into_destination_with_resolved_names(self):
        res = receipt_saver.process_message(self._msg("bill@iectest.co.il", "חשבון 8812"),
                                            {"label": "ofek", "email": "o@x"})
        self.assertEqual(res["status"], "saved")
        rec = res["record"]
        self.assertEqual((rec["seller"], rec["product"]), ("חברת חשמל", "8812"))
        self.assertEqual(Path(rec["folder_path"]).parent, self.dest)
        self.assertEqual(rec["rule_source"], "custom")

    def test_exclude(self):
        res = receipt_saver.process_message(self._msg("x@spam.example", "מבצע ענק"),
                                            {"label": "ofek", "email": "o@x"})
        self.assertEqual(res["status"], "excluded")


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


class TestProcessMessageRecord(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # Redirect every filesystem side effect into the temp dir.
        self._orig = {}
        for name, val in {
            "RECEIPTS_DIR": self.tmp / "קבלות",
            "MANUAL_DIR": self.tmp / "קבלות" / "_לטיפול ידני",
            "JAPANOLOGIA_DIR": self.tmp / "jp",
            "FALLBACK_LOG_FILE": self.tmp / "fallback_log.json",
        }.items():
            self._orig[name] = getattr(receipt_saver, name)
            setattr(receipt_saver, name, val)
        receipt_saver.RECEIPTS_DIR.mkdir(parents=True, exist_ok=True)
        receipt_saver.MANUAL_DIR.mkdir(parents=True, exist_ok=True)
        # Neutralise external effects.
        self._pdf = receipt_saver.save_email_pdf
        self._tt = receipt_saver.create_ticktick_task
        receipt_saver.save_email_pdf = lambda *a, **k: None
        receipt_saver.create_ticktick_task = lambda *a, **k: None
        self._hist = history_mod.HISTORY_FILE
        history_mod.HISTORY_FILE = self.tmp / "history.json"

    def tearDown(self):
        for name, val in self._orig.items():
            setattr(receipt_saver, name, val)
        receipt_saver.save_email_pdf = self._pdf
        receipt_saver.create_ticktick_task = self._tt
        history_mod.HISTORY_FILE = self._hist

    def _msg(self, **over):
        m = {
            "id": "abc123", "sender": "noreply@electra-power.co.il",
            "subject": "חשבונית חשמל 555", "date_raw": "Thu, 9 Jul 2026 14:47:00 +0300",
            "is_sent": False, "body_text": "", "body_html": "<p>x</p>",
            "first_attachment_name": "", "attachments": lambda: [],
            "link": "http://mail/abc123",
        }
        m.update(over)
        return m

    def _account(self):
        return {"label": "ofek", "email": "ofek.shmuel1@gmail.com"}

    def test_hardcoded_match_returns_full_record(self):
        # electra-power is a custom rule, not hardcoded; use cellcominv (hardcoded).
        res = receipt_saver.process_message(
            self._msg(sender="billing@cellcominv.co.il", subject="חשבונית חודשית"),
            self._account(), run_id="RID",
        )
        self.assertEqual(res["status"], "saved")
        rec = res["record"]
        self.assertEqual(rec["id"], "ofek:abc123")
        self.assertEqual(rec["run_id"], "RID")
        self.assertEqual(rec["action"], "DOWNLOADED")
        self.assertEqual(rec["seller"], "סלקום")
        self.assertEqual(rec["category"], "חשבנות/אינטרנט")
        self.assertEqual(rec["rule_source"], "hardcoded")
        self.assertEqual(rec["account"], "ofek")

    def test_fallback_returns_record_and_logs_history(self):
        res = receipt_saver.process_message(
            self._msg(sender="who@unknown-xyz.com", subject="mystery"),
            self._account(), run_id="RID",
        )
        self.assertEqual(res["status"], "fallback")
        rec = res["record"]
        self.assertEqual(rec["action"], "FALLBACK")
        self.assertIsNone(rec["seller"])
        self.assertEqual(rec["rule_source"], None)

    def test_sent_mail_returns_skipped_no_record(self):
        res = receipt_saver.process_message(
            self._msg(is_sent=True), self._account(), run_id="RID",
        )
        self.assertEqual(res["status"], "skipped")
        self.assertNotIn("record", res)


class TestMainProgressCallback(unittest.TestCase):
    def setUp(self):
        # Never touch real mailboxes / OneDrive from a test.
        self._accounts = receipt_saver.ACCOUNTS
        self._notify = receipt_saver.notify
        receipt_saver.ACCOUNTS = []
        receipt_saver.notify = lambda *a, **k: None

    def tearDown(self):
        receipt_saver.ACCOUNTS = self._accounts
        receipt_saver.notify = self._notify

    def test_main_accepts_progress_cb_and_returns_summary(self):
        events = []
        summary = receipt_saver.main(run_id="RID", progress_cb=events.append)
        self.assertEqual(summary["run_id"], "RID")
        self.assertEqual(summary["saved"], 0)
        self.assertEqual(summary["fallback"], 0)
        self.assertEqual([e["type"] for e in events], ["done"])


if __name__ == "__main__":
    unittest.main()
