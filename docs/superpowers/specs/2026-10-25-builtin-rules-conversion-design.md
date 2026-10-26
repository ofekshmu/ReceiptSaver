# Built-in rules → ordinary rules, plus the attachment name (2026-10-25)

## Problem

Eleven rules were hard-coded in `receipt_saver.py` (`KNOWN_RULES`): Wolt, KSP,
פאנקו, סלקום, Yes Planet, מדיטק, דואר ישראל, Cardcom, FlyMoney, Fattal, Stripe.
They ran before `rules.json`, so they were invisible in the Rules tab (searching
"wolt" found nothing), couldn't be edited, weren't linked from History and never
took part in rule conflicts. Two of them couldn't be expressed as rules: Wolt
names its product from the attachment's file name, and Cardcom / Stripe fall
back to the sender's display name when the subject regex misses.

## Decisions

- **Convert all 11 into ordinary rules** (`migrate_builtins.py`, one-off).
  iCount and Japanologia stay built in: they do more than name and file a mail.
- **Search: treat them like every other rule (option 2).** Their senders join the
  mailbox search's `from:` terms, so all mail from those 10 domains is fetched.
  Ads are still skipped by the `פרסומת` subject check (Israeli law requires it
  in marketing mail); a stray non-receipt is fixed per rule with a "subject must
  NOT contain". The alternatives considered were a per-rule "fetch every mail
  from this sender" toggle (off for these) and requiring an attachment.
- **No precedence any more:** an overlap with an existing rule is a rule
  conflict like any other (none today).
- **Roots:** reuse the root whose folder matches, else create one —
  `Wolt` (`קבלות\Wolt`) and `אינטרנט` (`קבלות\חשבנות\אינטרנט`).

## New capabilities

- `{attachment_name}` template token and an `attachment_name` extraction source:
  the first attachment's file name without its extension ("" if none). The scan
  passes `first_attachment_name`; fallback entries record `attachment_name` so
  previews and the conflict re-check see it (older entries: first saved file in
  the folder that isn't `email.pdf`).
- An extraction's `fallback` may be a template (`"{sender_name}"`), validated as
  one when it contains a token — gives Cardcom / Stripe their old behaviour.

## Mapping

| Rule | Seller | Product |
|------|--------|---------|
| Wolt | Wolt | extract attachment_name `^([^_]+)` else משלוח |
| דואר ישראל | דואר ישראל | extract subject without a leading/trailing "דואר ישראל" else הזמנה |
| Cardcom | extract subject `מ(.+?)(?:\s*[-–]\s*עבור\|$)` else `{sender_name}` | extract subject `(חשבונית[^\d]*)` else חשבונית |
| Fattal (`fattal.co.il` **or** subject `nyx`) | template `{sender_name}` | חשבונית |
| Stripe | extract subject `(?i)receipt from (.+?)(?:\s#\|\s*$)` else `{sender_name}` | מנוי |
| the other six | fixed | fixed |

Known difference: דואר ישראל removed the phrase anywhere in the subject; the
rule removes it at the start or end only (where it appears in practice).

## Migration and verification

Dry run by default. A frozen copy of the old rules is compared with the new
rules (seller, product, folder) over History, the fallback log and synthetic
cases; any other mail must keep its routing. `--apply` refuses on a difference,
backs `rules.json` up to `rules.pre-builtins.json`, then writes it. A re-run is
a no-op. Applied 2026-10-25: 31 built-in cases identical, 99 others unchanged.

The frozen copy surfaced a latent bug: the old Fattal rule passed
`extract_display_name` as its seller and called it with `(sender, subject)`,
which raised — any Fattal / "nyx" mail crashed its processing. The converted
rule works.

## History

Rows filed by the built-ins (`rule_source: hardcoded`) are matched against
today's rules like older custom rows, so they link to the converted rule
(`Rule: Wolt · Root: Wolt`). `receipt_saver.hardcoded_label` is gone.
