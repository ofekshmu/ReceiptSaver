# Receipt Saver — System Documentation

## Overview

Receipt Saver is an automated Python-based system that runs on Windows startup and scans three Gmail accounts for receipt and invoice emails. It saves all attachments and a PDF printout of each email into a structured folder hierarchy on OneDrive. Unrecognized emails are flagged for manual review via TickTick tasks. The system grows smarter over time through manual review sessions with Claude.

---

## Folder Structure

### Output Directory
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
| `receipt_saver.py` | Main script — runs at every login |
| `custom_rules.json` | User-defined sender rules — grows over time |
| `fallback_log.json` | Log of all unrecognized emails |
| `processed_ids.json` | Tracks every email already seen — prevents duplicates |
| `receipt_saver.log` | Full activity log with timestamps |
| `credentials_ofek.json` | Google OAuth credentials for ofek account |
| `credentials_family.json` | Google OAuth credentials for family account |
| `credentials_yuval.json` | Google OAuth credentials for yuval account |
| `token_ofek.json` | Auto-refreshing Gmail access token for ofek |
| `token_family.json` | Auto-refreshing Gmail access token for family |
| `token_yuval.json` | Auto-refreshing Gmail access token for yuval |
| `ticktick_token.json` | TickTick API access token |
| `ticktick_auth.py` | One-time TickTick authorization script |
| `setup.bat` | One-time installer — registers Task Scheduler job |

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

---

## Registered Senders

### Hardcoded Rules (built into the script)

These are permanent rules that never need updating:

| Sender Domain | Seller Name | Product | Category | Notes |
|---------------|-------------|---------|----------|-------|
| `wolt.com` | Wolt | Restaurant name | — | Extracted from attachment filename |
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

| Sender Domain | Subject Contains | Seller | Product | Category |
|---------------|-----------------|--------|---------|----------|
| `ladpc.co.il` | — | עיריית ראשון לציון | אישור תשלום | חשבנות/ארנונה |
| `icount.co.il` | יפנולוגי | יפנולוגי | חשבונית מס קבלה | יפנולוגי |
| `electra-power.co.il` | — | אלקטרה פאוור | חשבונית חשמל | חשבנות/חשמל |
| `printernet.co.il` | פזגז | פזגז | חשבונית גז | חשבנות/גז |
| `elalinfo.co.il` | — | אל על | כרטיס טיסה | — |

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

## Gmail Search Query

The script searches each account using this Gmail query:

```
-in:sent has:attachment newer_than:60d
(subject:receipt OR subject:invoice OR subject:קבלה OR subject:חשבונית
OR subject:אישור OR subject:הזמנה OR subject:purchase OR subject:payment)
```

**Key behaviors:**
- Only emails with attachments are considered
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
- `seller` — the name that appears in the folder
- `product` — the product/service description in the folder name
- `category` — optional, subdirectory path under `קבלות\` (e.g. `חשבנות/חשמל`). Omit or set to `null` for uncategorized receipts.

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

Install all: `pip install google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client requests plyer weasyprint`

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Script not running at startup | Check Task Scheduler → `ReceiptSaver` task exists and is enabled |
| Gmail auth error | Delete `token_[account].json` and run `receipt_saver.py` manually to re-authorize |
| TickTick tasks not created | Check `ticktick_token.json` exists; re-run `ticktick_auth.py` if needed |
| No notifications | Run `pip install plyer` |
| No email.pdf created | Run `pip install weasyprint` |
| Email not picked up | Check `receipt_saver.log` — may be a subject keyword mismatch |
| Duplicate folders | Should not happen — `processed_ids.json` prevents reprocessing |
