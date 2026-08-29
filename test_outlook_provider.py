import unittest
from pathlib import Path
from unittest import mock

import outlook_provider
from outlook_provider import _is_relevant


class TestIsRelevant(unittest.TestCase):
    def test_matches_subject_keyword_with_attachment(self):
        self.assertTrue(_is_relevant("billing@sternum-sec.com", "חשבונית מס 123", True, []))

    def test_no_attachment_no_keyword_match_is_irrelevant(self):
        self.assertFalse(_is_relevant("someone@example.com", "hello there", False, []))

    def test_custom_rule_domain_always_matches(self):
        self.assertTrue(_is_relevant("billing@sternum-sec.com", "unrelated subject", False, ["sternum-sec.com"]))

    def test_japanese_lesson_subject_matches_without_attachment_flag(self):
        self.assertTrue(_is_relevant("teacher@example.com", "סיכום שיעור יפנית 1.6", False, []))

    def test_attachment_alone_without_keyword_or_domain_is_irrelevant(self):
        self.assertFalse(_is_relevant("someone@example.com", "photos from the trip", True, []))

    def test_matching_is_case_insensitive(self):
        self.assertTrue(_is_relevant("BILLING@STERNUM-SEC.COM", "INVOICE for July", True, []))


class TestGetServiceNonInteractive(unittest.TestCase):
    """The automated scan must never fall into MSAL's ~15-minute device-code
    poll: with no usable cached token, get_service(interactive=False) raises
    at once instead of calling the device flow."""

    def _account(self):
        return {"label": "sternum", "email": "x@sternum-sec.com",
                "token_file": Path("does-not-exist.json"),
                "creds_file": Path("does-not-exist.json")}

    def test_raises_without_device_flow_when_silent_fails(self):
        stub_app = mock.Mock()
        stub_app.get_accounts.return_value = []
        stub_app.acquire_token_silent.return_value = None
        with mock.patch.object(outlook_provider, "_build_msal_app", return_value=stub_app):
            with self.assertRaises(RuntimeError) as ctx:
                outlook_provider.get_service(self._account(), interactive=False)
        self.assertIn("re-authorization", str(ctx.exception))
        stub_app.initiate_device_flow.assert_not_called()


if __name__ == "__main__":
    unittest.main()
