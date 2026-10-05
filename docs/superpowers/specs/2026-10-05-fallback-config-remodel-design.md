# Fallback / category remodel — design

Date: 2026-10-05 · Status: approved in chat, implementing

## Goal

Make a *category* ("configuration") a complete, self-describing filing recipe —
**one destination folder + how to name the seller + how to name the product +
match rules** — and rebuild the Fallbacks form around it with four options:

1. **File under a category** — existing category, or ＋ New category…
2. **Exclude as promotional**
3. **Move this one only**
4. **Skip** — *dismiss*: drop from the Fallbacks list, folder stays in `_לטיפול ידני`, History records `dismissed`.

Out of scope (remembered follow-up): converting the 11 hardcoded `KNOWN_RULES`.

## Data model (`categories.json`, v2)

```jsonc
{
  "id": "iec", "name": "חברת חשמל",
  "destination": "C:\\…\\קבלות\\חשבנות\\חשמל",   // full path; null only for exclude
  "seller":  { "mode": "fixed", "value": "חברת חשמל לישראל" },
  "product": { "mode": "extract", "source": "body", "regex": "(תלוש .+)", "fallback": "תלוש שכר" },
  "exclude": false,
  "match": [ { "sender_contains": "iec.co.il", "subject_contains": "…",
               "exclude_subject_contains": "…", "body_contains": "…" } ]
}
```

- `seller` / `product` spec: `{"mode":"fixed","value"}` | `{"mode":"extract","source":"subject|body|sender_name","regex","fallback"?}` | `null`.
- Resolution order per mail: fixed value → extract (first capture group, or whole match if the regex has no group; sanitized) → extract `fallback` → **app suggestion**.
- App suggestion = `naming.suggest_names(sender, subject)` (today's `fallback_ops.suggest` heuristic: seller from the sender's registered domain, product from subject keywords). The scan engine and the form share it.
- Match entries carry conditions only (per-entry seller/product overrides are gone).
- First matching category wins (unchanged).

## Fallbacks form

| Option | Fields |
|---|---|
| New category | Name, Destination, Seller, Product (each: value + ☑ *Use for every mail* + ⚙ Extract), Match rule |
| Existing category | Destination read-only; Seller/Product prefilled with what *this mail* resolves to (editable one-off); existing rules listed read-only + one new rule row (prefilled from this mail) added on Apply |
| Move this one only | Destination, Seller, Product as plain values |
| Exclude | Match rule row only (prefilled with the sender domain) |
| Skip | nothing |

- **Destination**: searchable combobox, `📁 Browse…` pinned first (native folder dialog, has *New folder*). Items ranked by usage across categories + History, compact label `root › sub › sub`, full path tooltip.
- **Seller/Product widget**: ☑ = save as `fixed` on the category; ☐ = this mail only (category stores `null` → future mails get the app suggestion). ⚙ Extract → source + regex, value field becomes "If no match"; live preview evaluated in **Python** (`Api.preview_extract`). Body source fetches the mail text from Gmail/Outlook by message id on first use (cached per session).

## Categories tab

Same widgets (destination combobox, seller/product widget without preview, match-rule rows). Regex validated on save. Merge moves match entries into the target (target's naming/destination apply).

## Backend

- `naming.py` (new): domain/seller/product heuristics.
- `categories.py`: v2 shape, `resolve_spec`, `match_category → (seller, product, destination) | EXCLUDE-tuple | None`, `query_terms()` for mailbox queries, spec validation. `to_legacy_rules` removed.
- `receipt_saver.py`: calls `categories.match_category` directly; `match_custom`/`load_custom_rules`/legacy file fallback removed.
- Providers: `list_candidate_ids(service, account)` — terms from `categories.query_terms()`.
- `receipt_roots.discover_roots(categories_path=None)`: fixed roots + every destination not under a fixed root.
- `fallback_ops.apply_decision` new payload (below); skip = dismiss.
- `app.Api`: `destination_suggestions`, `fallback_message_text`, `preview_extract`, `preview_category`; category CRUD on the v2 shape.

Decision payload:

```jsonc
{ "kind": "category|new_category|once|exclude|skip",
  "category_id": "…", "category_name": "…",
  "destination": "C:\\…",
  "seller":  { "value": "…", "every_mail": true, "extract": null | {"source","regex","fallback"} },
  "product": { … },
  "match": { "sender_contains", "subject_contains", "exclude_subject_contains", "body_contains" } }
```

## Migration

`migrate_categories_v2.py` (dry run by default): `base_dir`+`subfolder` → `destination`; seller/product → `fixed` (or `null`); `product_body_regex` → product `extract` from body with the old static product as `fallback`; exclude categories fold into the shared `excluded` category at the first exclude's position. Refuses `--apply` unless old vs new routing (seller, product, destination) is identical over every sender/subject in `history.json` + `fallback_log.json` plus a synthetic hit per match entry. `--apply` backs up to `categories.v1.json`. The one-off `migrate_rules_to_categories.py` (v0→v1, already run) is retired.

Rollback: restore `categories.v1.json` → `categories.json` and check out the previous commit.

## Testing

TDD; existing suites updated to the new shapes; new tests for spec resolution + fallback chain, decision payload kinds, skip-as-dismiss, destination ranking, migration equivalence, provider query terms.
