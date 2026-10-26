import unittest

import keywords as K

BODY = """שלום אופק,
תודה על הזמנתך מאיזי טו גיפט בע"מ.
מספר הזמנה: 44572
תאריך: 03/10/2026
סה"כ לתשלום: 120.00 ש"ח
איזי טו גיפט בע"מ — שירות לקוחות
לצפייה בחשבונית מס קבלה לחץ כאן
"""


class TestSender(unittest.TestCase):
    def test_domain_address_and_display_name(self):
        out = K.suggest('"Grow" <noreply@mail.grow.co.il>', "", "")["sender"]
        self.assertEqual(out, ["grow.co.il", "noreply@mail.grow.co.il", "Grow"])

    def test_bare_address_has_no_display_name(self):
        self.assertEqual(K.suggest("billing@iec.co.il", "")["sender"],
                         ["iec.co.il", "billing@iec.co.il"])


class TestSubject(unittest.TestCase):
    def test_phrases_then_words_without_ids_dates_and_stopwords(self):
        out = K.suggest("x@y.com", "חשבונית מס קבלה עבור עסקה 44572 ב- איזי טו גיפט")["subject"]
        self.assertIn("איזי טו גיפט", out)
        self.assertIn("חשבונית מס קבלה", out)
        self.assertIn("חשבונית", out)
        self.assertFalse(any("44572" in k for k in out))
        self.assertNotIn("עבור", out)          # stopword
        self.assertNotIn("ב", out)

    def test_english_and_dates(self):
        out = K.suggest("x@y.com", "Your receipt from Anthropic, Inc. #2231-4417 | 03/10/2026")["subject"]
        self.assertIn("Anthropic", out)
        self.assertIn("receipt", out)
        self.assertFalse(any("2231" in k or "2026" in k for k in out))
        self.assertNotIn("your", [k.lower() for k in out])

    def test_dedup_and_cap(self):
        out = K.suggest("x@y.com", " - ".join(f"word{i}x" for i in range(30)))["subject"]
        self.assertLessEqual(len(out), K.MAX_PER_GROUP)
        self.assertEqual(len(out), len(set(out)))


class TestBody(unittest.TestCase):
    def test_cues_first_then_frequent_phrases(self):
        out = K.suggest("x@y.com", "", BODY)["body"]
        self.assertEqual(out[0], "מספר הזמנה")
        self.assertIn("חשבונית מס קבלה", out)
        self.assertTrue(any("טו גיפט" in k for k in out), out)   # the business name
        self.assertFalse(any(any(ch.isdigit() for ch in k) and
                             sum(ch.isdigit() for ch in k) >= 3 for k in out))
        self.assertLessEqual(len(out), K.MAX_PER_GROUP)

    def test_empty_body(self):
        self.assertEqual(K.suggest("x@y.com", "s", "")["body"], [])

    def test_body_keywords_are_substrings_of_normalised_body(self):
        import rules as C
        norm = C.normalize_body(BODY)
        for k in K.suggest("x@y.com", "", BODY)["body"]:
            self.assertIn(k, norm)


if __name__ == "__main__":
    unittest.main()
