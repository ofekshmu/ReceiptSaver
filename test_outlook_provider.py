import unittest
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


if __name__ == "__main__":
    unittest.main()
