"""
migrate_builtins.py
-------------------
One-off migration that turns the 11 rules that used to be hard-coded in
receipt_saver.py (`KNOWN_RULES`: Wolt, KSP, פאנקו, סלקום, Yes Planet, מדיטק,
דואר ישראל, Cardcom, FlyMoney, Fattal, Stripe) into ordinary rules in
rules.json, so they show in the Rules tab, can be searched and edited, are
linked from History and take part in rule conflicts. See
docs/superpowers/specs/2026-10-25-builtin-rules-conversion-design.md.

  * roots are reused when one already points at the folder; otherwise created:
    `קבלות` (קבלות), `Wolt` (קבלות\\Wolt), `אינטרנט` (קבלות\\חשבנות\\אינטרנט)
  * rules that already exist (same id) are left alone, so a re-run is a no-op

Safety:
  * default is a DRY RUN: it checks that the old built-in routing (a frozen
    copy below) and the new rules give the same seller, product and folder for
    every built-in mail in history.json + fallback_log.json plus synthetic
    cases, and that no other mail changes routing (no new conflicts, nothing
    taken from an existing rule). Refuses --apply on any difference.
  * --apply backs rules.json up to rules.pre-builtins.json, then writes it.
  * rollback: copy rules.pre-builtins.json back over rules.json and check out
    the previous commit (which still has KNOWN_RULES).

Usage:
    python migrate_builtins.py            # dry run + verify
    python migrate_builtins.py --apply
"""

import copy
import json
import os
import re
import shutil
import sys
from pathlib import Path

import naming
import rules as R

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SCRIPT_DIR    = Path(__file__).parent
RULES_FILE    = SCRIPT_DIR / "rules.json"
BACKUP_FILE   = SCRIPT_DIR / "rules.pre-builtins.json"
HISTORY_FILE  = SCRIPT_DIR / "history.json"
FALLBACK_FILE = SCRIPT_DIR / "fallback_log.json"

FIXED = lambda v: {"mode": "fixed", "value": v}


def EXTRACT(source, regex, fallback=None):
    spec = {"mode": "extract", "source": source, "regex": regex}
    if fallback:
        spec["fallback"] = fallback
    return spec


# (id, name, root folder relative to קבלות, root name, seller, product, match)
BUILTINS = [
    ("wolt", "Wolt", "Wolt", "Wolt",
     FIXED("Wolt"), EXTRACT("attachment_name", r"^([^_]+)", "משלוח"),
     [{"sender_contains": "wolt.com"}]),
    ("ksp", "KSP", "", "קבלות", FIXED("KSP"), FIXED("חשבונית וקבלה"),
     [{"sender_contains": "ksp.co.il"}]),
    ("paneco", "פאנקו", "", "קבלות", FIXED("פאנקו"), FIXED("הזמנה"),
     [{"sender_contains": "paneco.com"}]),
    ("cellcom", "סלקום", r"חשבנות\אינטרנט", "אינטרנט",
     FIXED("סלקום"), FIXED("חשבונית חודשית"),
     [{"sender_contains": "cellcominv.co.il"}]),
    ("yes-planet", "Yes Planet", "", "קבלות", FIXED("Yes Planet"), FIXED("כרטיסים"),
     [{"sender_contains": "yesplanet.co.il"}]),
    ("meditech", "מדיטק", "", "קבלות", FIXED("מדיטק"), FIXED("הזמנה"),
     [{"sender_contains": "mhc.org.il"}]),
    ("israel-post", "דואר ישראל", "", "קבלות", FIXED("דואר ישראל"),
     # the subject without a leading/trailing "דואר ישראל"
     EXTRACT("subject", r"^(?:\s*דואר ישראל[\s\-–]*)?(.*?)(?:[\s\-–]*דואר ישראל\s*)?$", "הזמנה"),
     [{"sender_contains": "israelpost.co.il"}]),
    ("cardcom", "Cardcom", "", "קבלות",
     EXTRACT("subject", r"מ(.+?)(?:\s*[-–]\s*עבור|$)", "{sender_name}"),
     EXTRACT("subject", r"(חשבונית[^\d]*)", "חשבונית"),
     [{"sender_contains": "cardcom.co.il"}]),
    ("flymoney", "FlyMoney", "", "קבלות", FIXED("FlyMoney"), FIXED('מט"ח'),
     [{"sender_contains": "flymoney.com"}]),
    ("fattal", "Fattal", "", "קבלות",
     {"mode": "template", "template": "{sender_name}"}, FIXED("חשבונית"),
     [{"sender_contains": "fattal.co.il"}, {"subject_contains": "nyx"}]),
    ("stripe", "Stripe", "", "קבלות",
     EXTRACT("subject", r"(?i)receipt from (.+?)(?:\s#|\s*$)", "{sender_name}"), FIXED("מנוי"),
     [{"sender_contains": "stripe.com"}]),
]


def _norm(p) -> str:
    return os.path.normcase(os.path.normpath(str(p)))


def convert(data: dict, receipts_dir: Path = None) -> dict:
    """A copy of `data` with the built-in rules (and any roots they need) added."""
    receipts_dir = Path(receipts_dir or R.RECEIPTS_DIR)
    d = copy.deepcopy(data)

    def root_for(rel: str, name: str) -> str:
        folder = receipts_dir / rel if rel else receipts_dir
        for root in d["roots"]:
            if _norm(root.get("folder")) == _norm(folder):
                return root["id"]
        taken_names = {r.get("name") for r in d["roots"]}
        if name in taken_names:                     # e.g. another "Wolt" elsewhere
            name = f"{folder.parent.name} › {name}"
        root = R.new_root(name, str(folder), data=d)
        d["roots"].append(root)
        return root["id"]

    existing = {r.get("id") for r in d["rules"]}
    for rid, name, rel, root_name, seller, product, match in BUILTINS:
        if rid in existing:
            continue
        rule = R.new_rule(name, root=root_for(rel, root_name), seller=seller,
                          product=product, match=match, data=d)
        rule["id"] = rid
        d["rules"].append(rule)
    return d


# ---------------------------------------------------------------------------
# frozen copy of the old receipt_saver.KNOWN_RULES (v1.1.42), for verification
# ---------------------------------------------------------------------------

def _sanitize(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip(" .")


def _display_name(sender: str) -> str:
    m = re.match(r'^"?([^"<\n]+)"?\s*<', sender)
    return _sanitize(m.group(1).strip()) if m else _sanitize(sender.split("@")[0])


def _has(sender, *frags):
    return any(f in sender.lower() for f in frags)


def _wolt_product(subject, att):
    return _sanitize(re.split(r"_", att)[0].strip()) if att else "משלוח"


def _cardcom_seller(sender, subject):
    m = re.search(r"מ(.+?)(?:\s*[-–]\s*עבור|$)", subject)
    return _sanitize(m.group(1).strip()) if m else _display_name(sender)


def _cardcom_product(subject, att):
    m = re.search(r"(חשבונית[^\d]*)", subject)
    return _sanitize(m.group(1).strip()) if m else "חשבונית"


def _israelpost_product(subject, att):
    cleaned = re.sub(r"דואר ישראל[\s\-–]*", "", subject, flags=re.IGNORECASE)
    return _sanitize(re.sub(r"\s{2,}", " ", cleaned).strip(" -–")) or "הזמנה"


def _stripe_seller(sender, subject):
    m = re.search(r"receipt from (.+?)(?:\s#|\s*$)", subject, re.IGNORECASE)
    return _sanitize(m.group(1).strip()) if m else _display_name(sender)


OLD_RULES = [
    (lambda s, j: _has(s, "wolt.com"), "Wolt", _wolt_product, "Wolt"),
    (lambda s, j: _has(s, "ksp.co.il"), "KSP", lambda j, a: "חשבונית וקבלה", None),
    (lambda s, j: _has(s, "paneco.com"), "פאנקו", lambda j, a: "הזמנה", None),
    (lambda s, j: _has(s, "cellcominv.co.il"), "סלקום", lambda j, a: "חשבונית חודשית", "חשבנות/אינטרנט"),
    (lambda s, j: _has(s, "yesplanet.co.il"), "Yes Planet", lambda j, a: "כרטיסים", None),
    (lambda s, j: _has(s, "mhc.org.il"), "מדיטק", lambda j, a: "הזמנה", None),
    (lambda s, j: _has(s, "israelpost.co.il"), "דואר ישראל", _israelpost_product, None),
    (lambda s, j: _has(s, "cardcom.co.il"), _cardcom_seller, _cardcom_product, None),
    (lambda s, j: _has(s, "flymoney.com"), "FlyMoney", lambda j, a: 'מט"ח', None),
    # the original passed `extract_display_name` itself, which crashed when called
    # with (sender, subject) — this is what it meant to do
    (lambda s, j: _has(s, "fattal.co.il") or "nyx" in j.lower(), lambda s, j: _display_name(s),
     lambda j, a: "חשבונית", None),
    (lambda s, j: _has(s, "stripe.com"), _stripe_seller, lambda j, a: "מנוי", None),
]


def old_route(sender: str, subject: str, attachment: str = "", receipts_dir: Path = None):
    """(seller, product, folder) the old built-in rules gave, or None."""
    receipts_dir = Path(receipts_dir or R.RECEIPTS_DIR)
    for match_fn, seller, product_fn, category in OLD_RULES:
        if match_fn(sender, subject):
            seller = seller(sender, subject) if callable(seller) else seller
            product = _sanitize(product_fn(subject, attachment))
            folder = receipts_dir / category if category else receipts_dir
            return _sanitize(seller), product, _norm(folder)
    return None


def new_route(sender: str, subject: str, attachment: str, data: dict):
    hits = R.match_all(sender, subject, data=data)
    if len(hits) != 1:
        return [h["rule"]["name"] for h in hits]
    rule = hits[0]["rule"]
    if rule.get("exclude"):
        return "excluded"
    seller, product = R.resolve_names(rule, sender, subject, "", None, attachment)
    return _sanitize(seller), _sanitize(product), _norm(R.destination_of(rule, data))


# ---------------------------------------------------------------------------
# corpus + verification
# ---------------------------------------------------------------------------

SYNTHETIC = [
    ("Wolt <info@wolt.com>", "קבלת רכישה ב-Wolt", "2026-09-08_04_29_31.693_6a9f8f.pdf"),
    ("Wolt <info@wolt.com>", "קבלת רכישה ב-Wolt", "19_2026-09-16_19_18_47.813_6aa.pdf"),
    ("Wolt <info@wolt.com>", "קבלת רכישה ב-Wolt", ""),
    ("KSP <invoice@ksp.co.il>", "חשבונית מס קבלה 12345", "inv.pdf"),
    ("פאנקו <orders@paneco.com>", "אישור הזמנה #998", ""),
    ("Cellcom <cellcom-monthly-invoice@cellcominv.co.il>",
     "שמואל אופק , חשבונית סלקום לתקופה 01/10/2026 - 02/09/2026 מחכה לך במייל ובאתר סלקום", ""),
    ("Yes Planet <tickets@yesplanet.co.il>", "הכרטיסים שלך לסרט", "tickets.pdf"),
    ("מדיטק <shop@mhc.org.il>", "אישור הזמנה", ""),
    ("דואר ישראל <noreply@israelpost.co.il>", "דואר ישראל - אישור תשלום מכס", ""),
    ("דואר ישראל <noreply@israelpost.co.il>", "אישור תשלום מכס - דואר ישראל", ""),
    ("דואר ישראל <noreply@israelpost.co.il>", "דואר ישראל", ""),
    ("noreply@israelpost.co.il", "אישור הזמנת משלוח", ""),
    ("Cardcom <noreply@cardcom.co.il>", "חשבונית מס קבלה מסטודיו יוגה - עבור מנוי חודשי", ""),
    ("Cardcom <noreply@cardcom.co.il>", "חשבונית מס קבלה 5521 ממכון כושר", ""),
    ("Cardcom <noreply@cardcom.co.il>", "receipt 5521", ""),
    ("billing@cardcom.co.il", "Your invoice", ""),
    ("FlyMoney <fx@flymoney.com>", "Your order", ""),
    ("Fattal Hotels <res@fattal.co.il>", "אישור הזמנה", ""),
    ("NYX Hotel <res@nyx-hotels.com>", "Your NYX booking", ""),
    ("WaniKani <invoice+statements+X@stripe.com>", "Your receipt from WaniKani #2762-3531", ""),
    ("Some SaaS <receipts@stripe.com>", "Your Receipt From Acme Inc", ""),
    ("Some SaaS <receipts@stripe.com>", "Payment confirmation", ""),
]


def _load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return []


def _first_attachment(files) -> str:
    return next((f for f in files or [] if str(f).lower() != "email.pdf"), "")


def corpus(history: list = None, fallbacks: list = None) -> list:
    out = []
    for row in (history or []) + (fallbacks or []):
        sender, subject = row.get("sender") or "", row.get("subject") or ""
        if sender:
            att = row.get("attachment_name")
            att = att if att is not None else _first_attachment(row.get("files"))
            case = (sender, subject, att)
            if case not in out:
                out.append(case)
    return out + [c for c in SYNTHETIC if c not in out]


def verify(before: dict, after: dict, cases: list, receipts_dir: Path = None) -> list:
    """Every difference between the old routing and the new rules, as text."""
    problems = []
    for sender, subject, att in cases:
        old = old_route(sender, subject, att, receipts_dir)
        if old:
            new = new_route(sender, subject, att, after)
            if new != old:
                problems.append(f"{sender} | {subject[:50]} | {att[:30]}\n"
                                f"      old {old}\n      new {new}")
        else:
            was = [h["rule"]["id"] for h in R.match_all(sender, subject, data=before)]
            now = [h["rule"]["id"] for h in R.match_all(sender, subject, data=after)]
            if was != now:
                problems.append(f"{sender} | {subject[:50]}: rules {was} -> {now}")
    return problems


def main(apply: bool = False) -> int:
    before = R.load(RULES_FILE)
    after = convert(before)
    added = [r for r in after["rules"] if r["id"] not in {x["id"] for x in before["rules"]}]
    new_roots = [r for r in after["roots"] if r["id"] not in {x["id"] for x in before["roots"]}]
    print(f"rules to add: {len(added)}  ({', '.join(r['name'] for r in added) or 'none'})")
    for root in new_roots:
        print(f"new root: {root['name']}  ->  {root['folder']}")
    cases = corpus(_load(HISTORY_FILE), _load(FALLBACK_FILE))
    problems = verify(before, after, cases)
    checked = sum(1 for s, j, a in cases if old_route(s, j, a))
    print(f"checked {len(cases)} mails ({checked} handled by the old built-in rules)")
    if problems:
        print(f"\n{len(problems)} DIFFERENCES — not applying:")
        for p in problems:
            print("  " + p)
        return 1
    print("identical routing for every mail")
    if not apply:
        print("dry run — run with --apply to write rules.json")
        return 0
    if not added:
        print("nothing to do")
        return 0
    if RULES_FILE.exists():
        shutil.copy2(RULES_FILE, BACKUP_FILE)
        print(f"backup: {BACKUP_FILE.name}")
    R.save(after, RULES_FILE)
    print(f"wrote {RULES_FILE.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(apply="--apply" in sys.argv))
