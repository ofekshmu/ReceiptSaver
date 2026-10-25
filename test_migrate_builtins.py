import unittest
from pathlib import Path

import rules as R
import migrate_builtins as M

REC = Path(r"C:\Users\ofeks\OneDrive\Documents\קבלות")
FIXED = lambda v: {"mode": "fixed", "value": v}


def existing():
    return {"roots": [{"id": "main", "name": "קבלות", "folder": str(REC), "color": "#fff"}],
            "rules": [{"id": "iec", "name": "חשמל", "root": "main", "exclude": False,
                       "seller": FIXED("IEC"), "product": None,
                       "match": [{"sender_contains": "iec.co.il"}]}]}


class TestConvert(unittest.TestCase):
    def setUp(self):
        self.before = existing()
        self.after = M.convert(self.before, receipts_dir=REC)

    def test_adds_the_eleven_rules_after_the_existing_ones(self):
        self.assertEqual([r["id"] for r in self.after["rules"]],
                         ["iec", "wolt", "ksp", "paneco", "cellcom", "yes-planet", "meditech",
                          "israel-post", "cardcom", "flymoney", "fattal", "stripe"])
        self.assertEqual(len(self.before["rules"]), 1)           # input untouched

    def test_reuses_a_root_with_the_same_folder_and_adds_the_rest(self):
        roots = {r["name"]: r["folder"] for r in self.after["roots"]}
        self.assertEqual(roots, {"קבלות": str(REC), "Wolt": str(REC / "Wolt"),
                                 "אינטרנט": str(REC / "חשבנות" / "אינטרנט")})
        rule = lambda rid: next(r for r in self.after["rules"] if r["id"] == rid)
        self.assertEqual(rule("ksp")["root"], "main")
        self.assertEqual(R.destination_of(rule("wolt"), self.after), REC / "Wolt")

    def test_rerun_is_a_no_op(self):
        self.assertEqual(M.convert(self.after, receipts_dir=REC), self.after)

    def test_fattal_has_two_alternatives(self):
        fattal = next(r for r in self.after["rules"] if r["id"] == "fattal")
        self.assertEqual(fattal["match"], [{"sender_contains": "fattal.co.il"},
                                           {"subject_contains": "nyx"}])


class TestEquivalence(unittest.TestCase):
    def test_synthetic_corpus_routes_identically(self):
        before = existing()
        after = M.convert(before, receipts_dir=REC)
        self.assertEqual(M.verify(before, after, M.corpus(), receipts_dir=REC), [])

    def test_wolt_product_from_attachment(self):
        after = M.convert(R.empty(), receipts_dir=REC)
        self.assertEqual(M.new_route("info@wolt.com", "x", "19_2026-09-16.pdf", after)[1], "19")
        self.assertEqual(M.old_route("info@wolt.com", "x", "19_2026-09-16.pdf", REC)[1], "19")

    def test_overlap_with_an_existing_rule_is_reported(self):
        before = existing()
        before["rules"].append({"id": "mine", "name": "My Wolt", "root": "main", "exclude": False,
                                "seller": None, "product": None,
                                "match": [{"sender_contains": "wolt.com"}]})
        after = M.convert(before, receipts_dir=REC)
        problems = M.verify(before, after, [("info@wolt.com", "קבלה", "")], receipts_dir=REC)
        self.assertEqual(len(problems), 1)
        self.assertIn("My Wolt", problems[0])

    def test_a_new_rule_taking_another_mail_is_reported(self):
        before = existing()
        before["rules"][0]["match"] = [{"sender_contains": "iec.co.il", "subject_contains": "nyx"}]
        after = M.convert(before, receipts_dir=REC)
        # the old Fattal rule took "nyx" mails first; now it conflicts with the user's rule
        problems = M.verify(before, after, [("bill@iec.co.il", "nyx bill", "")], receipts_dir=REC)
        self.assertEqual(len(problems), 1)


if __name__ == "__main__":
    unittest.main()
