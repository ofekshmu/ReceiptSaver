import unittest
from pathlib import Path

import rules as R

SENDER = '"Grow" <noreply@mail.grow.co.il>'
SUBJECT = "חשבונית מס קבלה עבור עסקה 44572 ב- איזי טו גיפט"
DATE = "2026_10_03"


def render(t, sender=SENDER, subject=SUBJECT, date=DATE):
    return R.render_template(t, sender, subject, date)


class TestTokens(unittest.TestCase):
    def test_sender_tokens(self):
        self.assertEqual(render("{sender_name}"), "Grow")
        self.assertEqual(render("{sender_company}"), "Grow")
        self.assertEqual(render("{sender_domain}"), "grow.co.il")
        self.assertEqual(render("{sender_address}"), "noreply@mail.grow.co.il")

    def test_subject_tokens(self):
        self.assertEqual(render("{subject}"), SUBJECT)
        self.assertEqual(render("{subject_no_numbers}"), "חשבונית מס קבלה עבור עסקה ב- איזי טו גיפט")
        self.assertEqual(render("{subject_first_word}"), "חשבונית")
        self.assertEqual(render("{subject_word:3}"), "קבלה")
        self.assertEqual(render("{subject_words:8-10}"), "איזי טו גיפט")
        self.assertEqual(render("{subject_words:1-2}"), "חשבונית מס")

    def test_date_tokens(self):
        self.assertEqual(render("{year}-{month}"), "2026-10")
        self.assertEqual(render("{year}", date="2026-07-09"), "2026")
        self.assertEqual(render("{year}", date=None), "")

    def test_mixed_with_text_and_sanitized(self):
        self.assertEqual(render("הזמנה {subject_first_word} / {year}"), "הזמנה חשבונית _ 2026")

    def test_out_of_range_words_are_empty_and_edges_tidied(self):
        self.assertEqual(render("{sender_name} - {subject_word:40}"), "Grow")
        self.assertEqual(render("{subject_words:40-42}"), "")

    def test_bare_address_has_no_display_name(self):
        self.assertEqual(render("{sender_name}", sender="billing@iec.co.il"), "billing")


class TestValidate(unittest.TestCase):
    def test_valid_template(self):
        self.assertEqual(R.validate_spec({"mode": "template", "template": " {sender_name} "}),
                         {"mode": "template", "template": "{sender_name}"})

    def test_rejects_unknown_tokens_bad_ranges_and_empty(self):
        for bad in ("{nope}", "{subject_word:0}", "{subject_words:5-2}", "{subject_word}",
                    "{sender_name:3}", "", "   "):
            with self.assertRaises(ValueError, msg=bad):
                R.validate_spec({"mode": "template", "template": bad})


class TestResolve(unittest.TestCase):
    def test_template_spec_in_a_rule(self):
        rule = {"seller": {"mode": "template", "template": "{subject_words:8-10}"},
                "product": {"mode": "template", "template": "{subject_words:1-3}"}}
        self.assertEqual(R.resolve_names(rule, SENDER, SUBJECT, "", DATE),
                         ("איזי טו גיפט", "חשבונית מס קבלה"))

    def test_empty_render_falls_back_to_suggestion(self):
        spec = {"mode": "template", "template": "{subject_word:40}"}
        self.assertEqual(R.resolve_spec(spec, SENDER, SUBJECT, "", "SUG", DATE), "SUG")

    def test_one_rule_many_folders_via_match_rule(self):
        data = {"roots": [{"id": "r", "name": "r", "folder": r"C:\x", "color": "#fff"}],
                "rules": [{"id": "grow", "name": "Grow", "root": "r", "exclude": False,
                           "seller": {"mode": "template", "template": "{subject_words:8-10}"},
                           "product": {"mode": "template", "template": "{subject_first_word}"},
                           "match": [{"sender_contains": "grow.co.il"}]}]}
        a = R.match_rule(SENDER, SUBJECT, data=data, date=DATE)
        b = R.match_rule(SENDER, "קבלה מס קבלה עבור עסקה 9 ב- מאפיית לחם טוב", data=data, date=DATE)
        self.assertEqual(a, ("איזי טו גיפט", "חשבונית", Path(r"C:\x")))
        self.assertEqual(b[:2], ("מאפיית לחם טוב", "קבלה"))


if __name__ == "__main__":
    unittest.main()
