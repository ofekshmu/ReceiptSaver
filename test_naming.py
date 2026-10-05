import unittest

import naming as N


class TestNaming(unittest.TestCase):
    def test_registered_domain(self):
        self.assertEqual(N.registered_domain("Heshbon@mail.electra-power.co.il"),
                         "electra-power.co.il")
        self.assertEqual(N.registered_domain('"Shop" <a@news.shop.com>'), "shop.com")
        self.assertEqual(N.registered_domain("x@haifa.muni.il"), "haifa.muni.il")

    def test_seller_from_domain(self):
        self.assertEqual(N.seller_from_domain("some-shop.co.il"), "Some-Shop")
        self.assertEqual(N.seller_from_domain("anthropic.com"), "Anthropic")

    def test_product_from_subject(self):
        self.assertEqual(N.product_from_subject("חשבונית מס קבלה 12"), "חשבונית מס קבלה")
        self.assertEqual(N.product_from_subject("אישור תשלום ביט"), "אישור תשלום")
        self.assertEqual(N.product_from_subject("no keywords"), "חשבונית")

    def test_display_name(self):
        self.assertEqual(N.display_name('"חברת חשמל" <a@iec.co.il>'), "חברת חשמל")
        self.assertEqual(N.display_name("billing@iec.co.il"), "billing")

    def test_suggest_names(self):
        self.assertEqual(N.suggest_names("noreply@some-shop.co.il", "הזמנה 9"),
                         ("Some-Shop", "הזמנה"))

    def test_sanitize(self):
        self.assertEqual(N.sanitize(' a/b:c. '), "a_b_c")


if __name__ == "__main__":
    unittest.main()
