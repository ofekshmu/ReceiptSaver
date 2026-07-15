# Design: Add Microsoft 365 mailbox as a second email provider

## Goal

Add a fourth mailbox — `sternum` (ofeks@sternum-sec.com), a Microsoft 365 work
account — to Receipt Saver, processed through the same pipeline (hardcoded
rules, `custom_rules.json`, fallback, TickTick, notifications, folder
structure) as the existing three Gmail accounts.

## Why not a shim or a separate script

- **Shimming** Graph API messages into a fake Gmail payload shape (fake
  `parts`, fake base64 MIME) was considered but rejected: Graph's attachment
  and body model differs enough from Gmail's that the shim would leak in
  confusing ways as it's extended.
- **A separate script** per provider was rejected: it would duplicate rule
  matching and fork `custom_rules.json` / `fallback_log.json` /
  `processed_ids.json`, breaking the single pipeline the user relies on.
- **Chosen approach:** normalize both providers behind a small common
  interface, so rule-matching logic (`process_message`) is provider-agnostic.

## Azure AD app registration (manual, one-time, done by the user)

1. Azure Portal → Azure Active Directory → App registrations → New
   registration. Name: `ReceiptSaver`. Single-tenant.
2. Authentication → enable "Allow public client flows" (enables device-code
   flow — no client secret, no redirect URI, no local web server).
3. API permissions → Microsoft Graph → Delegated → add `Mail.Read` and
   `offline_access`. Grant admin consent if available.
   - If the user isn't the tenant admin, per-user consent is attempted first;
     if the tenant blocks it, the resulting AADSTS error names the exact
     permissions to send to the org's admin.
4. User supplies the Application (client) ID and Directory (tenant) ID —
   no secret required.

## Components

- `gmail_provider.py` — existing Gmail auth/listing/parsing logic extracted
  out of `receipt_saver.py` verbatim (pure refactor, no behavior change).
- `outlook_provider.py` — new. MSAL device-code auth with a token cache
  persisted to `token_sternum.json` (silent refresh on subsequent runs,
  mirroring the Gmail token-file UX). Lists Inbox messages via Microsoft
  Graph and converts each to a `NormalizedMessage`.
- `NormalizedMessage`: `{id, sender, subject, date_str, is_sent, body_text,
  body_html, has_attachment, first_attachment_name}` plus an `attachments()`
  callable returning `[(filename, bytes)]`.
- `process_message()` in `receipt_saver.py` is rewritten to consume a
  `NormalizedMessage` instead of a raw Gmail payload dict. All rule logic
  (hardcoded rules, `custom_rules.json`, iCount handling, fallback) is
  unchanged in behavior — it just reads from the normalized shape.
- `ACCOUNTS` entries gain a `"provider": "gmail" | "outlook"` field; `main()`
  dispatches to the matching provider module per account.

## Listing & filtering strategy

Gmail's server-side search query (`-in:sent -subject:פרסומת newer_than:60d
(...)`) has no Graph equivalent. Instead, `outlook_provider.py` fetches all
Inbox messages from the last 60 days via `$filter=receivedDateTime ge
{date}` (paged via `@odata.nextLink`), selecting only `subject, from,
receivedDateTime, hasAttachments, id`. The subject-keyword and
custom-rule-domain filtering Gmail does server-side is applied client-side
in Python instead, using the same predicate logic. Sent mail is excluded
by construction (only the Inbox folder is queried).

## Attachments & body

Simpler than Gmail: Graph's `/me/messages/{id}/attachments` returns
`contentBytes` (base64) directly per attachment — no per-attachment API
round-trip. The message body arrives as `{contentType, content}` directly —
no MIME-part walking needed.

## Edge cases

- iCount's fallback TickTick task embeds a deep link to the email
  (`gmail_link()` today). For non-Gmail accounts this becomes an Outlook web
  link (`https://outlook.office.com/mail/inbox/id/{id}`) instead — low
  priority since iCount is unlikely to email a corporate inbox, but kept
  correct per-provider rather than assumed-Gmail.
- Auth failures (consent required, expired grant) log and notify exactly
  like today: `"⚠️ Receipt Saver" — "שגיאת כניסה לחשבון sternum"`.

## New dependency

`msal` (Microsoft's official Python auth library), added to the pip install
line and `DOCUMENTATION.md`.

## Day-one custom rule: Sternum payslip

Once the `sternum` account is live, add to `custom_rules.json`:

```json
{
  "_comment": "Sternum payslip (billing@sternum-sec.com)",
  "match_sender_contains": "billing@sternum-sec.com",
  "match_body_contains": "תלוש שכר",
  "product_body_regex": "(תלוש שכר לחודש \\S+ \\d{4})",
  "seller": "משכורת",
  "product": "תלוש שכר",
  "category": null,
  "base_dir": "C:\\Users\\ofeks\\OneDrive\\Ofek\\Work\\Sternum\\משכורות"
}
```

Produces folders like `2026_07_09 - משכורת - תלוש שכר לחודש יוני 2026 -
sternum`, with the attached payslip PDF and the usual `email.pdf` printout
inside. No code change required — uses the existing `base_dir` and
`product_body_regex` mechanisms already supported by `custom_rules.json`.
This rule requires no new code — it's a config-only addition, usable
regardless of when the provider refactor lands.

## Testing

- Extract `gmail_provider.py` first and run the existing three Gmail
  accounts end-to-end to confirm the refactor is behavior-preserving before
  adding the Outlook path.
- Manually verify `outlook_provider.py` against the live `sternum` mailbox:
  device-code login completes, a known payslip email is fetched, filtered,
  matched by the new custom rule, and produces the correct folder/PDF.
- Verify token persistence: second run reuses the cached token silently
  (no browser prompt).
