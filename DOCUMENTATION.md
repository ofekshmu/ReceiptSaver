# Receipt Saver — System Documentation

## Overview

Receipt Saver is an automated Python-based system that runs on Windows startup and scans three Gmail accounts for receipt and invoice emails. It saves all attachments and a PDF printout of each email into a structured folder hierarchy on OneDrive. Unrecognized emails are flagged for manual review via TickTick tasks. The system grows smarter over time through manual review sessions with Claude.

---

## Folder Structure

### Receipts Directory
```
C:\Users\ofeks\OneDrive\Documents\קבלות\
│
├── חשבנות\                              ← utility bills category
│   ├── חשמל\                            ← electricity (אלקטרה פאוור)
│   │   └── YYYY_MM_DD - Seller - Product - [account]\
│   ├── מיים\                            ← water
│   │   └── YYYY_MM_DD - Seller - Product - [account]\
│   ├── ארנונה\                          ← municipal tax (עיריית ראשון לציון)
│   │   └── YYYY_MM_DD - Seller - Product - [account]\
│   ├── אינטרנט\                         ← internet (סלקום)
│   │   └── YYYY_MM_DD - Seller - Product - [account]\
│   └── גז\                              ← gas (פזגז)
│       └── YYYY_MM_DD - Seller - Product - [account]\

│
├── YYYY_MM_DD - Seller - Product - [account]\   ← uncategorized receipts
│   ├── attachment.pdf
│   ├── attachment2.pdf
│   └── email.pdf                        ← always present, printout of the email
│
└── _לטיפול ידני\                        ← fallback folder
    └── YYYY_MM_DD - Sender - Subject - [account]\
        ├── attachment.pdf
        └── email.pdf
```

### Japanese Lessons Directory
```
C:\Users\ofeks\OneDrive\Ofek\Japanese Lessons\Japanologia\
│
└── YYYY_MM_DD\                          ← lesson date from subject (e.g. 2026_06_01)
    ├── סיכום שיעור יפנית 泉 1.6.pdf
    └── תרגיל מסכם פרק 37.pdf
```
Populated by `receipt_saver.py` for every new "סיכום שיעור יפנית D.M" email received on the `ofek` account. Use `japanologia_backfill.py` to backfill historical emails.

### Folder Naming Format
```
YYYY_MM_DD - Seller Name - Product Description - [account]
```

**Account labels:**
- `ofek` → ofek.shmuel1@gmail.com
- `family` → shmuelfamily21@gmail.com
- `yuval` → yuvalritsker@gmail.com

**Examples:**
```
2026_03_25 - סלקום - חשבונית חודשית - ofek
2026_03_20 - Wolt - Shi-Shi - family
2026_03_13 - יפנולוגי - חשבונית מס קבלה - ofek
2026_04_02 - אלקטרה פאוור - חשבונית חשמל - family
```

---

## Scripts Folder

**Location:** `C:\Users\ofeks\Scripts\ReceiptSaver\`

| File | Purpose |
|------|---------|
| `receipt_saver.py` | Scan engine. Provider-agnostic: dispatches each account to `gmail_provider` or `outlook_provider` based on its `"provider"` field, then processes a normalized message dict. `main(run_id, progress_cb)` accepts an optional progress callback and returns a run summary; `process_message()` returns a structured record per handled mail. Still runs standalone (`python receipt_saver.py`); at login it is driven by `app.py` instead |
| `app.py` | Startup window (pywebview). Opens at login, drives the scan on a worker thread, streams results into the UI, serves history + fallback data, applies fallback decisions. Launched by `run.bat`. `RECEIPT_SAVER_UI_DRYRUN=1` boots the window without touching any mailbox |
| `history.py` | Append-only `history.json` store — one record per handled mail, backs the History view |
| `fallback_ops.py` | Heuristic `suggest()` for unresolved fallbacks + `apply_decision()` (rule / once / exclude / skip): writes `custom_rules.json`, moves the folder out of `_לטיפול ידני`, marks `fallback_log.json` resolved, patches the history row |
| `receipt_roots.py` | Discovers every destination root (main `קבלות`, the fallback dir, Japanologia, and each `base_dir` in `custom_rules.json`) and guards `Api.browse` against filesystem access outside them. Backs the Receipts tab |
| `claude_handoff.py` | Opens a pre-seeded `claude` terminal for fallbacks that need manual classification |
| `tray.py` | Resident system-tray icon (Open / Run scan now / Quit) |
| `ui/` | Frontend for `app.py` — `index.html`, `app.css`, `app.js`. No build step |
| `make_shortcut.py` | One-off: creates Desktop + Start Menu shortcuts that launch `pythonw app.py` with the app icon (`python make_shortcut.py`) |
| `make_icon.py` | One-off: generates `assets/receipt_saver.ico` |
| `assets/receipt_saver.ico` | App icon (7 sizes, 16–256 px) used by the shortcuts |
| `history.json` | Structured log of every handled mail since the UI shipped |
| `requirements.txt` | Pinned dependency list |
| `gmail_provider.py` | Gmail-specific implementation of the provider interface (`get_service`, `list_candidate_ids`, `fetch_message`) — houses `build_gmail_query()` and the Gmail payload parsing that used to live in `receipt_saver.py` |
| `outlook_provider.py` | Microsoft 365 provider implementation (`get_service`, `list_candidate_ids`, `fetch_message` via Microsoft Graph + MSAL device-code auth) — not yet wired to a live Outlook account in `ACCOUNTS` |
| `test_receipt_saver.py` | Unit tests for `parse_date()`, the structured record shape, and `main()`'s progress callback |
| `test_history.py` | Unit tests for `history.py` (append/dedup/update/page) |
| `test_fallback_ops.py` | Unit tests for `suggest()` and `apply_decision()` |
| `test_claude_handoff.py` | Unit tests for the Claude handoff prompt builder |
| `test_app_api.py` | Unit tests for the `app.Api` data methods and scan orchestration |
| `japanologia_backfill.py` | One-time script — backfills Japanese lesson attachments since April 15, 2026 |
| `custom_rules.json` | User-defined sender rules — grows over time |
| `fallback_log.json` | Log of all unrecognized emails |
| `processed_ids.json` | Tracks every email already seen — prevents duplicates |
| `receipt_saver.log` | Full activity log with timestamps, full paths, and saved filenames |
| `credentials_ofek.json` | Google OAuth credentials for ofek account |
| `credentials_family.json` | Google OAuth credentials for family account |
| `credentials_yuval.json` | Google OAuth credentials for yuval account |
| `token_ofek.json` | Auto-refreshing Gmail access token for ofek |
| `token_family.json` | Auto-refreshing Gmail access token for family |
| `token_yuval.json` | Auto-refreshing Gmail access token for yuval |
| `ticktick_token.json` | TickTick API access token |
| `ticktick_auth.py` | One-time TickTick authorization script |
| `run.bat` | Launches `pythonw app.py` (the startup window, which runs the scan). Shortcut placed in Windows startup folder |
| `setup.bat` | One-time installer — registers Task Scheduler job (replaced by run.bat) |

---

## Email Decision State Machine

Every email found in Gmail goes through the following pipeline:

```
┌─────────────────────────────────────────────────────┐
│                   Email Arrives                      │
└─────────────────────┬───────────────────────────────┘
                      │
                      ▼
           ┌─────────────────────┐
           │  Is it in SENT?     │──── YES ──→ SKIP (ignore silently)
           └─────────┬───────────┘
                     │ NO
                     ▼
           ┌──────────────────────────┐
           │  Is subject "סיכום       │
           │  שיעור יפנית D.M"?       │
           │  (ofek account only)     │
           └─────────┬────────────────┘
                     │ YES
                     ▼
        ┌─────────────────────────────────────────┐
        │  JAPANOLOGIA PATH                        │
        │  • Create folder YYYY_MM_DD under        │
        │    Japanese Lessons\Japanologia\         │
        │  • Save all attachments (no email.pdf)   │
        └─────────────────────────────────────────┘
                     │ NO
                     ▼
           ┌─────────────────────┐
           │  Is sender iCount?  │
           │  (icount.co.il)     │
           └─────────┬───────────┘
                     │ YES
                     ▼
        ┌────────────────────────────┐
        │  ICOUNT PATH               │
        │  • Create folder in קבלות  │
        │  • Save email.pdf          │
        │  • NO attachments saved    │
        │  • TickTick task (medium   │
        │    priority) with direct   │
        │    Gmail link to download  │
        │    the PDF manually        │
        └────────────────────────────┘
                     │ NO (not iCount)
                     ▼
           ┌─────────────────────┐
           │  Matches a          │
           │  HARDCODED RULE?    │──── YES ──→ KNOWN PATH (see below)
           └─────────┬───────────┘
                     │ NO
                     ▼
           ┌─────────────────────┐
           │  Matches a          │
           │  CUSTOM RULE?       │──── YES ──→ KNOWN PATH (see below)
           └─────────┬───────────┘
                     │ NO
                     ▼
        ┌────────────────────────────┐
        │  FALLBACK PATH             │
        │  • Save to _לטיפול ידני\   │
        │  • Save all attachments    │
        │  • Save email.pdf          │
        │  • Log to fallback_log.json│
        │  • TickTick task (low      │
        │    priority) asking to     │
        │    open Claude and say     │
        │    "handle my fallback     │
        │    emails"                 │
        │  • Desktop notification    │
        └────────────────────────────┘

KNOWN PATH:
        ┌────────────────────────────┐
        │  • Create folder in קבלות  │
        │  • Save all attachments    │
        │  • Save email.pdf          │
        │  • Mark as processed       │
        └────────────────────────────┘
```

**Folder name collisions:** if two unrelated emails compute the same
`date - seller - product - label` (e.g. two separate Hyp payment
confirmations for the same gym visit, on the same day), the second one gets
a `" (2)"`, `" (3)"`, ... suffix appended (`unique_folder()` in
`receipt_saver.py`) instead of silently nesting into the first folder or
overwriting its `email.pdf`.

---

## Registered Senders

### Hardcoded Rules (built into the script)

These are permanent rules that never need updating:

| Sender Domain | Seller Name | Product | Category | Notes |
|---------------|-------------|---------|----------|-------|
| `wolt.com` | Wolt | Restaurant name | Wolt | Extracted from attachment filename |
| `ksp.co.il` | KSP | חשבונית וקבלה | — | Electronics store |
| `paneco.com` | פאנקו | הזמנה | — | Wine/drinks store |
| `cellcominv.co.il` | סלקום | חשבונית חודשית | חשבנות/אינטרנט | Monthly internet bill |
| `yesplanet.co.il` | Yes Planet | כרטיסים | — | Cinema tickets |
| `mhc.org.il` | מדיטק | הזמנה | — | Culture center |
| `israelpost.co.il` | דואר ישראל | Extracted from subject | — | Israel Post |
| `cardcom.co.il` | Extracted from subject | Extracted from subject | — | Generic Israeli invoicing platform |
| `flymoney.com` | FlyMoney | מט"ח | — | Currency exchange |
| `fattal.co.il` / NYX | Display name from sender | חשבונית | — | Fattal hotel chain |
| `stripe.com` | Extracted from subject | מנוי | — | Stripe-powered subscriptions |
| `icount.co.il` | Extracted from subject | חשבונית מס קבלה | — | **Special handling** — see iCount section |

### Custom Rules (in custom_rules.json)

These were added through manual review sessions with Claude:

| Sender Domain | Subject Contains | Seller | Product | Category | Base Dir |
|---------------|-----------------|--------|---------|----------|----------|
| `morning.co` | מקס ברנר | מקס ברנר | חשבונית | — | — |
| `morning.co` | בר סרוסי | בר סרוסי השקעות | חשבונית | — | — |
| `morning.co` | אמריקן דיגיטקס | אמריקן דיגיטקס | חשבונית | — | — |
| `ecom.gov.il` | — | שירות התשלומים הממשלתי | תשלום | — | — |
| `haifa.muni.il` | שובר תשלום | — | — | — | — | **excluded** (payment voucher notices, not receipts) |
| `tranzila.com` | baby-land | Baby Land | חשבונית | — | — |
| `iec.co.il` | אישור הפעלת שירות | — | — | — | — | **excluded** (service activation notices, not receipts) |
| `iec.co.il` | — | חברת חשמל לישראל | חשבונית חשמל | חשבנות/חשמל | — |
| `mg.driivz.com` | — | on-ev | טעינה חשמלית | — | — |
| `inter-il.com` | — | Interactive Broker | אישור הפקדה | Interactive Broker | — |
| `ladpc.co.il` | — | עיריית ראשון לציון | אישור תשלום | חשבנות/ארנונה | — |
| `onecity.co.il` (sender contains חיפה) | — | עיריית חיפה | קבלת תשלום | חשבנות/ארנונה | נכסים\שלום שבאזי 7 |
| `onecity.co.il` (sender contains ראשון לציון) | — | ראשון לציון החברה לב | קבלת תשלום | חשבנות/ארנונה | — |
| `icount.co.il` | יפנולוגי | יפנולוגי | חשבונית מס קבלה | יפנולוגי | — |
| `electra-power.co.il` | — | אלקטרה פאוור | חשבונית חשמל | חשבנות/חשמל | — |
| `printernet.co.il` | פזגז | פזגז | חשבונית גז | חשבנות/גז | — |
| `elalinfo.co.il` | — | אל על | כרטיס טיסה | — | — |
| `mail.anthropic.com` | — | Anthropic | Claude Pro מנוי | — | — |
| `ace.co.il` | — | ACE | הזמנה | — | — |
| `webmaster@icmega.org` | — | — | — | — | — | **excluded** (promotional newsletters) |
| `icmega.org` | — | חבר | הזמנה | — | — |
| `abirsport.co.il` | — | אביר ספורט | כדור פיזיו | — | — |
| `hyp.co.il` | upapp | upapp | כניסה לחדר כושר אייקון | — | — | Hyp is a shared payment platform used by many merchants — subject must contain `upapp` or unrelated Hyp senders get mislabeled as the gym |
| `planetcinema.co.il` | — | Planet Cinema | כרטיסים | — | — |
| `smartbee.co.il` | — | גן ילדים דיסני ראשון | שכר לימוד | — | — |
| `billing@sternum-sec.com` | — | משכורת | תלוש שכר (extracted from body: `תלוש שכר לחודש <month> <year>`) | — | Work\Sternum\משכורות |

---

## iCount Special Handling

iCount is an Israeli invoicing platform used by many businesses (e.g. יפנולוגי). The actual invoice PDF is not attached to the email — it is accessible only via a link inside the email body that requires a browser session to click.

**What the script does:**
1. Detects the email is from iCount (`icount.co.il`)
2. Extracts the seller name from the subject: `"חשבונית מס קבלה 7721 מאת יפנולוגי"` → `יפנולוגי`
3. Creates a folder in the main `קבלות\` directory
4. Saves `email.pdf` (printout of the email body)
5. Skips all attachments (they are just company logo images, not useful)
6. Creates a TickTick task (medium priority) with:
   - Title: `הורד PDF: [folder name]`
   - Direct link to the Gmail message
   - Instructions to open the email, click the "לצפייה" link, and save the PDF to the folder

---

## Desktop Notifications

The script shows three types of Windows toast notifications:

| When | Title | Message |
|------|-------|---------|
| Script starts | `Receipt Saver מופעל` | `בודק תיבות דואר לקבלות חדשות...` |
| Receipts saved | `📥 N קבלות נשמרו` | Comma-separated list of seller names |
| Nothing new | `Receipt Saver` | `לא נמצאו קבלות חדשות.` |
| Fallback found | `⚠️ קבלה לא זוהתה` | `[account] מאת: Sender / Subject` (one per email, stays 10 sec) |
| Auth error | `⚠️ Receipt Saver` | Which account failed |

---

## Startup UI (`app.py`)

At login `run.bat` launches `pythonw app.py` — a borderless, centered window
(pywebview). It replaces the old headless `python receipt_saver.py` startup run;
`receipt_saver.py` still runs standalone for manual/scheduled use. The window
opens, shows a scanning state, and drives the scan itself on a worker thread.

**Four views:**

| View | What it shows |
|------|---------------|
| **This run** | Live results of the scan that runs when the window opens: a card per handled mail (action pill, `seller · product` or `sender · subject`, account, date, Open folder), per-account progress lines, and a `N saved · M fallback` summary. |
| **History** | Every mail handled since the UI shipped, newest first, lazy-loaded on scroll, with a text filter over sender/subject/seller. Backed by `history.json`. |
| **Fallbacks** | Unresolved `fallback_log.json` entries (badge shows the count). Each row has a form pre-filled by a heuristic guess (`fallback_ops.suggest`, sender + subject only — no body, no network, no AI). Pick **Make a rule** / **Move this one only** / **Exclude as promotional** / **Skip**, adjust fields, **Apply**. Multi-select + **Handle selected with Claude →** opens a pre-seeded `claude` terminal for the hard ones. |
| **Receipts** | Read-only explorer. Left rail lists every destination root (`receipt_roots.discover_roots` — main `קבלות`, `_לטיפול ידני`, Japanologia, and each custom-rule `base_dir`; roots not yet created are dimmed). The right pane is a breadcrumb navigator over the selected root: click a folder to descend, a crumb to go back, double-click a file to open it in its default app, or **Open in Explorer** for the current folder. Dated `YYYY_MM_DD - …` folders sort newest-first with a 🧾 glyph. No writes — `Api.browse` refuses any path outside the known roots. |

**Applying a fallback decision** (`fallback_ops.apply_decision`):

- `rule` — append a rule to `custom_rules.json`, move + rename the folder from
  `_לטיפול ידני` to the computed destination, mark resolved, set the history row
  to `RESOLVED`.
- `once` — same, minus the `custom_rules.json` write.
- `exclude` — append an `{"exclude": true}` rule, delete the folder, log to
  `cleanup_log.json`, mark resolved.
- `skip` — nothing; the row stays for next time.

**Tray:** a resident tray icon (Open / Run scan now / Quit). Closing the window
hides it to the tray; Quit ends the process.

**Clickable icon:** run `python make_shortcut.py` once to drop **Receipt Saver**
shortcuts on the Desktop and in the Start Menu. They launch `pythonw app.py`
directly (no console flash) with `assets/receipt_saver.ico`. Regenerate the icon
with `python make_icon.py`. `--startmenu-only` skips the Desktop shortcut.

**`history.json` record shape:** `id` (`account:messageId`), `run_id`,
`handled_at`, `account`, `account_email`, `date`, `sender`, `subject`, `action`
(`DOWNLOADED | ICOUNT | JAPANOLOGIA | FALLBACK | EXCLUDED | RESOLVED`), `seller`,
`product`, `category`, `folder_name`, `folder_path`, `files`, `rule_source`
(`hardcoded | custom | icount | japanologia | null`). Resolved fallbacks also get
`resolution` (`rule | once | exclude`).

---

## Gmail Search Query

The script builds the Gmail query dynamically at runtime:

```
-in:sent -subject:פרסומת newer_than:60d (
  (has:attachment AND (subject:receipt OR subject:invoice OR subject:קבלה OR subject:קבלת
   OR subject:חשבונית OR subject:אישור OR subject:הזמנה
   OR subject:תשלום OR subject:purchase OR subject:payment))
  OR from:morning.co
  OR from:ecom.gov.il
  OR (from:haifa.muni.il -subject:...)
  OR from:tranzila.com
  OR from:iec.co.il
  OR from:mg.driivz.com
  OR from:inter-il.com
  OR from:ladpc.co.il
  OR from:icount.co.il
  OR from:electra-power.co.il
  OR from:printernet.co.il
  OR from:elalinfo.co.il
  OR from:icmega.org
  OR from:abirsport.co.il
  OR from:hyp.co.il
  OR from:planetcinema.co.il
  OR from:smartbee.co.il
  OR (has:attachment AND subject:"סיכום שיעור יפנית")
  ...
)
```

The `from:` exceptions are generated automatically from every domain-based `match_sender_contains` entry in `custom_rules.json`. Adding a new custom rule with a domain automatically updates the query — no manual changes needed. The Japanese lesson clause is hardcoded in `build_gmail_query()`, which now lives in `gmail_provider.py` (called via `gmail_provider.list_candidate_ids()`).

**Key behaviors:**
- Emails with attachments matching subject keywords are always included
- Known senders (from custom_rules.json) are always included even without attachments — their email body is saved as `email.pdf`
- SENT folder is always excluded
- Looks back 60 days on every run
- Already-processed email IDs are stored in `processed_ids.json` — each email is processed only once regardless of how many times the script runs
- IDs are account-scoped (`ofek:messageId`) to prevent cross-account collisions

---

## TickTick Integration

The script creates two types of TickTick tasks automatically:

### Fallback Task (low priority)
Created when an email doesn't match any rule.
- **Title:** `טפל בקבלה: [folder name]`
- **Content:** Account label, folder path, instruction to open Claude

### iCount Task (medium priority)
Created for every iCount email.
- **Title:** `הורד PDF: [folder name]`
- **Content:** Direct Gmail link, instruction to click "לצפייה" and save PDF to folder

---

## Capabilities with Claude (Manual Sessions)

### "handle my fallback emails"

Trigger this by opening this chat and saying the phrase. You will need to paste the contents of `fallback_log.json`.

**What Claude does:**
1. Reads each unresolved entry in the log
2. Pulls the actual email from Gmail via MCP to read its content
3. Classifies each email — is it a receipt? Who is the seller? What is the product?
4. Presents a classification table for your approval
5. On approval:
   - Provides an updated `custom_rules.json` so the sender is recognized automatically next time
   - Provides a `move_fallbacks.py` script that renames and moves the folders from `_לטיפול ידני\` to the main `קבלות\` directory with correct names
   - Updates all resolved entries in `fallback_log.json`

### "add a rule for X"

You can tell Claude directly to add a rule, for example:
- *"emails from noreply@bezeq.co.il are receipts from בזק - חשבונית חודשית"*
- *"emails from amazon.com with 'order' in the subject are from Amazon - הזמנה"*

Claude will provide an updated `custom_rules.json` to replace in your scripts folder.

### Asking about your receipts

Since Claude has Gmail MCP access to your `ofek` account, you can ask things like:
- *"Did I get a Cellcom bill this month?"*
- *"Show me all my Wolt receipts from March"*
- *"How much did I spend on KSP last year?"*

---

## custom_rules.json Format

```json
[
  {
    "_comment": "Optional description",
    "match_sender_contains": "domain.co.il",
    "match_subject_contains": null,
    "seller": "Seller Name",
    "product": "Product Description",
    "category": "חשבנות/חשמל"
  }
]
```

- `match_sender_contains` — required, substring match on the sender email address
- `match_subject_contains` — optional, substring match on the subject line (use when same platform sends for multiple sellers, e.g. iCount)
- `exclude_subject_contains` — optional, skip the email if the subject contains this string (e.g. `פרסומת` to skip promotional emails)
- `match_body_contains` — optional, skip the email if the plain-text body does NOT contain this string (e.g. `מספר הזמנה` to require an actual order number)
- `product_body_regex` — optional, regex with one capture group to extract the product name from the email body (overrides the static `product` field when matched)
- `seller` — the name that appears in the folder
- `product` — the product/service description in the folder name
- `category` — optional, subdirectory path under the base directory (e.g. `חשבנות/ארנונה`). Omit or set to `null` for no subcategory.
- `base_dir` — optional, absolute path to a different root directory. If omitted, defaults to `קבלות\`. Use for receipts belonging to a specific property or project.
- `exclude` — optional, set to `true` to silently skip matching emails (e.g. promotional newsletters). No folder is created, logged as EXCLUDED.

### Categories

Receipts can be routed into subcategories under `קבלות\חשבנות\`:

| Category path | Hebrew | Description |
|---------------|--------|-------------|
| `חשבנות/חשמל` | חשמל | Electricity bills |
| `חשבנות/מיים` | מיים | Water bills |
| `חשבנות/ארנונה` | ארנונה | Municipal tax |
| `חשבנות/אינטרנט` | אינטרנט | Internet bills |
| `חשבנות/גז` | גז | Gas bills |

Both hardcoded rules (4th tuple element) and custom rules (`category` field) support categories.

---

## fallback_log.json Format

```json
[
  {
    "message_id": "19cd976b02585b03",
    "account": "ofek",
    "account_email": "ofek.shmuel1@gmail.com",
    "date": "2026_03_10",
    "sender": "noreply@somesite.co.il",
    "subject": "אישור תשלום",
    "folder_name": "2026_03_10 - noreply - אישור תשלום - ofek",
    "folder_path": "C:\\Users\\ofeks\\OneDrive\\Documents\\קבלות\\_לטיפול ידני\\...",
    "resolved": false
  }
]
```

Entries are marked `"resolved": true` after being handled in a Claude session.

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `google-auth` | Google OAuth token management |
| `google-auth-oauthlib` | OAuth flow for desktop apps |
| `google-auth-httplib2` | HTTP transport for Google APIs |
| `google-api-python-client` | Gmail API client |
| `requests` | TickTick API calls |
| `plyer` | Windows desktop toast notifications |
| `weasyprint` | HTML → PDF conversion for email printouts |
| `msal` | Microsoft 365 device-code auth (Outlook provider) |
| `pywebview` | Frameless startup window hosting the HTML/CSS/JS UI |
| `pystray` | System-tray icon |
| `Pillow` | Tray icon image generation |
| `pywin32` *(optional)* | Only needed by `make_shortcut.py` |

Install all: `pip install -r requirements.txt`

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Window not appearing at startup | Check startup folder (`shell:startup`) — `run.bat` shortcut should be there |
| Startup window is blank | Check `receipt_saver.log` for an `[app.py]` line; run `python app.py` (not `pythonw`) once to see console errors |
| Want to open the window without scanning | `set RECEIPT_SAVER_UI_DRYRUN=1` then run `app.py` |
| Gmail auth error | Delete `token_[account].json` and run `receipt_saver.py` manually to re-authorize |
| TickTick tasks not created | Check `ticktick_token.json` exists; re-run `ticktick_auth.py` if needed |
| No notifications | Run `pip install plyer` |
| No email.pdf created | Run `pip install weasyprint` |
| Email not picked up | Check `receipt_saver.log` — may be a subject keyword mismatch |
| Duplicate folders | Should not happen — `processed_ids.json` prevents reprocessing |
