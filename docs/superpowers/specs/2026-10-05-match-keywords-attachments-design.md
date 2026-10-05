# Match keywords, attachment condition, inline option fields — design

Date: 2026-10-05 · Status: approved in chat · Builds on `2026-10-05-fallback-config-remodel-design.md`

## 1. Fields under the selected option
The Fallbacks form's fields block is moved inside the chosen option (expands
directly under it). Apply stays at the bottom.

## 2. Match rule: lists + negative body + attachments
```jsonc
{ "sender_contains": ["grow.co.il"],
  "subject_contains": ["איזי טו גיפט", "חשבונית"],   // ALL must appear
  "exclude_subject_contains": ["פרסומת"],           // ANY present -> no match
  "body_contains": ["מספר הזמנה"],                  // ALL (case-sensitive, normalised)
  "exclude_body_contains": [],                       // ANY present -> no match
  "attachments": "one" }                             // "none" | "one" | "many"; absent = any
```
- A plain string is still accepted everywhere (= one value); stored as a string
  when there is exactly one value, a list otherwise — no data migration.
- Attachment count = **documents only** (images ignored). Providers add
  `attachment_count` to the normalized message from attachment names; fallback
  entries store it; older entries fall back to counting the folder's files
  (excluding `email.pdf` and images). `None` (unknown) = condition not checked.
- `query_terms()` emits one term per sender fragment with the list of
  subject exclusions; Gmail adds every `-subject:` term.

## 3. Keyword suggestions (`keywords.py`)
`suggest(sender, subject, body)` → `{sender, subject, body}` lists:
- sender: registered domain, full address, display name
- subject: separator-split phrases + single words; drops tokens with 3+ digits
  (IDs, dates, amounts) and stopwords
- body: 1–3-word phrases by frequency, known receipt cues first, same filters

`Api.keyword_suggestions(message_id, include_body)`; the UI asks without body
first (instant) then with body (network, cached). Chips: click adds to / removes
from the matching list; **≠** on a subject/body pill flips positive/negative.
Shown for a new category, Exclude, and the rule added to an existing category;
the Categories tab's Add-rule row gets the same pill fields + attachments select
(no chips).
