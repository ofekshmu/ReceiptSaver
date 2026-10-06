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
| `app.py` | Startup window (pywebview). Drives the scan on a worker thread, streams results into the UI, serves history + fallback data, applies fallback decisions. Launched at login by the **`ReceiptSaverUI`** scheduled task (`pythonw app.py --autostart`, no console); a manual launch (no `--autostart`) opens idle instead of scanning immediately — see [Startup UI](#startup-ui-apppy). Writes `[app.py] …` breadcrumb lines (launch / window created / window shown / FATAL+traceback) to `receipt_saver.log`. `RECEIPT_SAVER_UI_DRYRUN=1` boots the window without touching any mailbox |
| `version.py` | Single source of the app version string shown next to the wordmark. Bump `__version__`; `full_version()` appends the short git commit |
| `install_startup.py` | Registers/removes the `ReceiptSaverUI` logon task (`--uninstall`). The task action is `pythonw app.py --autostart` — that flag is what tells `app.py` to scan immediately instead of opening idle. Uses PowerShell `Register-ScheduledTask` (no admin needed) and clears any leftover Startup-folder launcher |
| `history.py` | Append-only `history.json` store — one record per handled mail, backs the History view. `append` (dedup by `id`), `update` (patch matching rows only), `upsert` (patch if present, else append — used for fallback resolutions) |
| `fallback_ops.py` | Heuristic `suggest()` for unresolved fallbacks (sender + subject only; reuses a category that already knows the sender) + `apply_decision()` (`category` / `new_category` / `once` / `exclude` / `skip`): creates or extends a category in `categories.json`, moves the folder out of `_לטיפול ידני`, marks `fallback_log.json` resolved, and upserts a `RESOLVED` history row (created from the fallback entry if no scan-time row exists). See [Applying a fallback decision](#startup-ui-apppy) |
| `receipt_roots.py` | Discovers every destination root — main `קבלות`, the fallback dir, Japanologia, plus every category `destination` that isn't already inside one of those (a destination nested in another folds into the outermost one) — and guards `Api.browse` against filesystem access outside them. Backs the Receipts tab |
| `categories.py` | A *category* is a complete filing recipe: it **matches** incoming mail (OR-list `match[]` of `{sender_contains, subject_contains, exclude_subject_contains, body_contains}`) and says **where** the receipt goes (one full `destination` folder) and **how** it is named (`seller` / `product` specs: fixed text, a regex extraction from the subject / body / sender name with an optional fallback, or `null` = the app's suggestion). `match_category()` returns `(seller, product, destination)` / `(EXCLUDE, None, None)` / `None`; first matching category wins. Also `resolve_spec`, `extract`, `validate_spec`, `query_terms()` (the providers' mailbox search terms) and the CRUD helpers behind the Categories tab. See [categories.json Format](#categoriesjson-format) |
| `naming.py` | The app's default seller/product guess from the sender + subject only (seller from the registered domain, product from subject keywords). Shared by the scan engine (unset or missed seller/product) and the Fallbacks form prefill |
| `keywords.py` | Keyword chips for building a match rule from one email: `suggest(sender, subject, body)` → sender (domain, address, display name), subject (separator-split phrases + words) and body (known receipt cues such as `מספר הזמנה` first, then frequent 1–4-word phrases). Tokens with 2+ digits (IDs, dates, amounts) and filler words are never offered, and every keyword is a verbatim slice of the email so it is guaranteed to match it |
| `ui_state.py` | Persists small window UI preferences (`hidden_roots`, `fallbacks_simple`, `rx_sort`) to `ui_state.json` (atomic write) |
| `ui_state.json` | Runtime UI preferences — git-ignored |
| `claude_handoff.py` | Opens a pre-seeded `claude` terminal — `launch()` for fallbacks that need manual classification, `launch_error()` for an error the UI surfaced, `launch_receipt()` for one right-clicked Receipts entry (all `cwd` the repo). Every seeded prompt ends with `NO_AUTO_CHANGES` — an instruction that the session must only investigate and propose, and change nothing until the user says so |
| `tray.py` | Resident system-tray icon (Open / Run scan now / Quit) |
| `ui/` | Frontend for `app.py` — `index.html`, `app.css`, `app.js`. No build step |
| `make_shortcut.py` | One-off: creates the **Receipt Saver** Desktop + Start Menu shortcuts (`pythonw app.py`, app icon). Launch-at-login is handled separately by `install_startup.py`. `--startmenu-only` limits scope |
| `make_icon.py` | One-off: generates `assets/receipt_saver.ico` |
| `assets/receipt_saver.ico` | App icon (7 sizes, 16–256 px) used by the shortcuts |
| `history.json` | Structured log of every handled mail since the UI shipped |
| `requirements.txt` | Pinned dependency list |
| `gmail_provider.py` | Gmail-specific implementation of the provider interface (`get_service`, `list_candidate_ids`, `fetch_message`) — houses `build_gmail_query()` and the Gmail payload parsing that used to live in `receipt_saver.py` |
| `outlook_provider.py` | Microsoft 365 provider (`get_service`, `list_candidate_ids`, `fetch_message` via Microsoft Graph + MSAL). During the scan `get_service(interactive=False)` uses **only** the silent/cached token and raises at once if it is stale — it never enters MSAL's ~15-minute device-code poll |
| `outlook_auth.py` | One-time interactive sign-in for the Outlook account(s) — run `python outlook_auth.py` when the scan reports "&lt;account&gt; needs re-authorization" |
| `test_receipt_saver.py` | Unit tests for `parse_date()`, the structured record shape, and `main()`'s progress callback |
| `test_history.py` | Unit tests for `history.py` (append/dedup/update/page) |
| `test_fallback_ops.py` | Unit tests for `suggest()` and `apply_decision()` |
| `test_categories.py` / `test_categories_mutations.py` / `test_naming.py` / `test_keywords.py` / `test_migrate_categories_v2.py` | Unit tests for category matching + seller/product resolution, the CRUD helpers, the naming heuristic, the keyword extractor, and the v2 migration + its verifier |
| `test_claude_handoff.py` | Unit tests for the Claude handoff prompt builder |
| `test_app_api.py` | Unit tests for the `app.Api` data methods and scan orchestration |
| `japanologia_backfill.py` | One-time script — backfills Japanese lesson attachments since April 15, 2026 |
| `backfill_fallback_history.py` | One-off — writes `RESOLVED` history rows for fallbacks resolved before `history.upsert` existed (walks `fallback_log.json` for `resolved: true`, skips messages already in `history.json`). `--dry-run` to preview. Idempotent |
| `migrate_categories_v2.py` | One-off, reversible — `categories.json` v1 (`base_dir` + `subfolder`, string seller/product, per-rule overrides) → v2 (one `destination`, seller/product specs). `product_body_regex` becomes a body extraction with the old static product as its fallback; exclude categories fold into the shared `excluded` category greedily, only where that doesn't change routing. Dry run by default, with an equivalence check of old vs new (seller, product, destination) over every sender/subject in `history.json` + `fallback_log.json` plus a synthetic hit per rule; refuses `--apply` on any difference. `--apply` backs v1 up to `categories.v1.json`. **Applied 2026-10-05** (34 → 31 categories, 112 cases identical). The earlier v0→v1 `migrate_rules_to_categories.py` was retired (it's in git history) |
| `categories.json` | The categories (see `categories.py`) — grows as you file fallbacks; edited in the **Categories** tab |
| `custom_rules.legacy.json` / `categories.v1.json` | Pre-migration backups of the old rule formats. Nothing reads them any more |
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
| `run.bat` | Convenience double-click launcher — `start "" pythonw app.py`. **Not** used at login (a `.bat` in the Startup folder pops up a console window); login uses the `ReceiptSaverUI` scheduled task |
| `setup.bat` | Legacy one-time installer — registered an `ONLOGON` Task Scheduler job running the old headless `receipt_saver.py`. Superseded by `install_startup.py`; not used |

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
        │    (or the category's      │
        │    destination)            │
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
           │  CATEGORY?          │──── YES ──→ KNOWN PATH (see below)
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
        │    (or the category's      │
        │    destination)            │
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

### Categories (in categories.json)

Managed in the app's **Categories** tab, and grown by filing fallbacks. The
table below is the set migrated from the old `custom_rules.json`; since the
2026-10-05 remodel each category has a single destination folder (the old
*Category* sub-folder and *Base Dir* columns combined), and the excluded
senders live in the shared `excluded` category (except the `sternum-sec.com`
one, which must stay after the salary category to keep routing identical):

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
| `payngo.co.il` | *(body must contain `מחסני חשמל`)* | מחסני חשמל | הזמנה | — | — | `sales@payngo.co.il` is a shared Matrix/Tafnit mail platform used by many retailers — the sender alone doesn't identify the seller, so the rule requires `מחסני חשמל` in the body (`match_body_contains`) |

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

At login the **`ReceiptSaverUI`** scheduled task (trigger *At log on*, current
user, ~15 s delay) launches `pythonw app.py --autostart` — a borderless,
centered window (pywebview). `pythonw.exe` has no console, so nothing but the
UI appears; the window is created `on_top` briefly so it surfaces above the
other apps that start at login. It replaces the old headless `python
receipt_saver.py` startup run; `receipt_saver.py` still runs standalone for
manual/scheduled use.

The `--autostart` flag is what makes the scan start immediately — it's only
passed by the logon task. A manual launch (Desktop/Start Menu shortcut, no
flag) opens **idle**: the **This run** tab shows a "Run scan" button instead
of scanning right away, so opening the app to browse History/Fallbacks/Receipts
doesn't always trigger a mailbox scan. The titlebar's ⟲ button and the tray's
"Run scan now" start a scan the same way (`Api.start_scan`) regardless of how
the app was launched.

A **Task Scheduler job** is used instead of a Startup-folder shortcut because it
fires after the desktop has settled, always runs in the interactive session, and
isn't shown on (or silenceable from) Task Manager's Startup tab. Register or
remove it with:

```
python install_startup.py            # register (no admin needed)
python install_startup.py --uninstall
```

Every launch appends `[app.py] …` lines to `receipt_saver.log` — `launch vX.Y.Z
(sha)`, `window created at (x,y) size WxH`, `window shown — starting scan
(autostart)` / `window shown — manual launch, waiting for user to start scan`,
or `FATAL during startup` + traceback. If the window doesn't appear at login,
that trail says how far it got. Test without rebooting: `schtasks /run /tn
ReceiptSaverUI`.

The window title bar shows the running version (`v1.1.0`) next to the wordmark,
from `version.py` via `Api.app_version()`; hover it for the full `X.Y.Z (sha)`.

**Resizing.** The window is frameless, so it has no OS resize border; a small
grip (`#win-resize-grip`, bottom-right corner, drag cursor) drives
`Api.resize_by` the same way the titlebar drag drives `Api.move_by` —
origin-independent pointer deltas, since the backend's `screenX/Y` is
window-relative. Both drags use **backpressure** rather than a
`requestAnimationFrame` flush: each `move_by` / `resize_by` is a
JS↔Python bridge round-trip that can outlast a frame, so firing one per
frame regardless let stale calls queue up and the window replayed a
growing backlog behind the cursor. Now at most one call is in flight;
pointer movement that arrives while it's pending accumulates (sub-pixel
remainder carried) and the next call sends the whole delta at once, so
the window stays a single constant IPC latency behind the mouse instead
of drifting further back the longer you drag. `window.events.resized` persists the final size (debounced
0.5 s so a drag doesn't spam writes) to `ui_state.json` (`win_w`/`win_h`,
default `980`/`680`, floor `820`/`520`); the next launch opens at that size
instead of always resetting to the default. The floor was measured with
Playwright against `ui/index.html` — the titlebar clips below ~550px wide —
with margin added so the window can be shrunk without ever clipping the
titlebar or toolbars; `html, body { overflow-x: hidden }` is a second,
belt-and-suspenders guarantee that no horizontal scrollbar can appear.

**Five views:**, switched via the titlebar tabs (a faded vertical divider separates each tab so they don't read as merged together).

| View | What it shows |
|------|---------------|
| **This run** | On a manual launch, opens idle with a **"Run scan"** button (no scan happens until it's clicked, the titlebar ⟲ is pressed, or the tray's "Run scan now" is used); on an `--autostart` login launch, the scan starts immediately and this tab shows it live. Live results: a card per handled mail, plus a **per-account status list** (`ofek ✓ · yuval ✓ · sternum ⚠ needs re-authorization …`) driven by `connecting` / `account` / `error` / `done` events — so a slow or failing account is visible immediately instead of the view looking stuck. The scan always resolves to a definite sentence — `Scan complete — N new receipts saved` / `…no new mail found` / `Scan stopped early — see errors above`; re-opening the tab reconciles it from `Api.get_run()`. Each surfaced error (scan-error rows and error toasts) carries an **Ask Claude** button (Claude-mark icon) — it calls `Api.ask_claude_error(text)`, which opens a `claude` terminal in the repo pre-seeded to debug that error. |
| **History** | Every mail handled since the UI shipped, newest first, lazy-loaded on scroll, with a text filter over sender/subject/seller. Backed by `history.json`. Resolved-fallback rows show `· resolved <date> by you/Claude/backfill`. The card's folder link (shared `#tpl-card`, so **This run** too) is the icon-only outlined-folder glyph, not an "Open folder" text link. |
| **Fallbacks** | Unresolved `fallback_log.json` entries (badge shows the count). Each row has a form pre-filled by `fallback_ops.suggest` (sender + subject only — no body, no network, no AI; a category that already knows the sender is preselected). Four options: **File under a category** / **Exclude as promotional** / **Move this one only** / **Skip**, then **Apply**. The fields block expands **directly under the selected option** (it is moved into that option's `.opt-slot`; clicking anywhere on an option selects it). **File under a category** shows the categories as a **grid of pastel tiles** (`.cat-picker.f-cat-assign`, built by `makeCatGrid`): the first tile is always **＋ New category** (dashed outline), then one tile per non-excluded category with its name, its folder (`קבלות › חשבנות › חשמל`, full path on hover) and `seller · N rules`. Each category gets a fixed colour from an 8-hue pastel palette (`PASTELS`, picked by a hash of its id, so it never changes). The selected tile gets an accent ring and a ✓ corner badge. The **Search categories…** box above filters tiles live by name, folder, seller or sender domain (＋ New stays visible; "No category matches" when nothing does); Enter picks the first match. The grid shows at most three rows and scrolls inside itself beyond that; a pre-selected category (one that already knows the sender) is scrolled into view only if it's below the fold. Picking a tile also switches the option to **File under a category**. The fields block (`.fb-fields`, contextual heading `.fb-fields-head`) changes with the choice: **New category** — name, **Destination**, **Seller**, **Product** and a **Match rule**, all editable; **existing category** — destination pinned to the category, Seller/Product prefilled with what *this mail* resolves to under that category (`Api.preview_category`; editable as a one-off for this mail, the category's settings stay as they are), its current rules listed read-only plus one new rule row that gets added; **Move this one only** — destination + plain seller/product, no rule; **Exclude** — the match rule only (sender prefilled; add e.g. a subject condition to narrow it); **Skip** — nothing. **Destination** is a searchable combobox (`makeDestPicker`): **📁 Browse…** pinned first (`Api.pick_folder` → the Windows folder dialog, which can also create a new folder), then every folder from `Api.destination_suggestions` — category destinations + folders History filed into + every root (never `_לטיפול ידני`), most-used first, shown compactly as `קבלות › חשבנות › חשמל` with the full path on hover. The **Match rule** is a widget (`#tpl-matchrule`, `makeMatchRule`): one pill list per condition — *Sender contains*, *Subject contains*, *Subject must NOT contain*, *Body contains*, *Body must NOT contain* (type + Enter adds a pill; text typed but not Enter-ed is still included on Apply) — plus an **Attachments** select (Any / None / Exactly 1 / More than 1, with a *this email: N documents* hint; images such as logos don't count). Below it, **keyword chips** from `Api.keyword_suggestions` in three groups — Sender / Subject / Body: click a chip to add it to the matching condition (click again to remove); **≠** on a subject/body pill flips it between *contains* and *must NOT contain*. Sender and subject chips appear at once; body chips need the mail text, so they load the first time you click or focus inside that email's form. Chips show for a new category, Exclude, and the rule added to an existing category. **Seller** / **Product** each use the same widget (`#tpl-namefield`, `makeNameField`): a value prefilled with the app's suggestion, a **Use for every mail** tick (ticked → saved on the category as fixed text; unticked → this mail only, future mails get the app's suggestion) and **⚙ Extract** — pick Subject / Body / Sender name and type a regex; the value box becomes *If no match* and a live preview (`Api.preview_extract`, run in Python so it behaves exactly like the scan) shows the result, a miss, or a regex error. The Body source fetches the mail text from Gmail/Outlook by message id on first use and caches it for the session. Multi-select + the icon-only **Claude-mark → button** (`#fb-handoff`) opens a pre-seeded `claude` terminal for the hard ones; each row also has its own icon-only Claude button (`fallbackClaudeButton`) and an icon-only outlined-folder link. A **Simple view** toggle collapses every entry to a one-line row (subject + `sender · account · date` + confidence); click a row to expand its full form. The toggle persists in `ui_state.json`. |
| **Categories** | Every category in `categories.json`, one row each: name, a summary (`destination · seller: … · product: …`, where each is the fixed text, `from subject/body/sender name`, or `app suggestion`), the rule count, **merge into…** (moves this category's rules into another; the target's destination and naming apply from then on), **Save** and **✕**. **▸** expands an editor with the same Destination picker and Seller/Product widgets as the Fallbacks form (no preview — there's no mail to test against; an invalid regex is rejected on Save), the match rules (each removable) and an **Add rule** row — the same match-rule widget as the Fallbacks form (pill lists + attachments), without keyword chips. **Add category** creates one filing into `קבלות` with no rules yet. The shared `excluded` category (and any other exclude category) shows only its rules. |
| **Receipts** | Read-only explorer. Left rail lists every destination root (`receipt_roots.discover_roots` — main `קבלות`, `_לטיפול ידני`, Japanologia, and every category destination outside those; roots not yet created are dimmed), each entry separated from the next by a faded divider line. The right pane is a breadcrumb navigator over the selected root: click a folder to descend, a crumb to jump to an ancestor, the **‹** button (or **Alt+←**) to step back through visited folders, double-click a file to open it in its default app, or the outlined-folder icon button to open the current folder in Windows Explorer (an inline SVG using `currentColor`, so it matches the app's palette exactly instead of the mismatched colors of a Windows folder emoji). The breadcrumb trail stays on one line — a crumb too long for the available width is clipped with an ellipsis, and hovering any crumb (or a row) shows its full path in a native tooltip. Two **sort** buttons in the toolbar toggle the field (**Name** / **Date**) and direction (**↑** / **↓**); folders always sort before files, the choice persists in `ui_state.json` (`rx_sort`, default `date_desc`), and Date order uses the `YYYY_MM_DD` prefix of dated folders, otherwise the filesystem mtime. Dated `YYYY_MM_DD - Seller - Product - label` folders are parsed for display: each row shows the cleaned **Seller - Product** title, a human date (`25 Aug 2026`) and an account chip, with a 📁 folder glyph (they're still real folders — one PDF, occasionally more, inside; glyphs are desaturated with CSS `grayscale` to match the app's monochrome look), each in a shaded box. Plain folders and files show their raw name. A search box at the top runs `Api.search_receipts` — a recursive, depth-capped walk of **every** root — once 2+ characters are typed, and lists matches as `root / relative\path`; clearing it back below 2 characters restores the plain (non-recursive) current-folder listing. Clicking a folder in the results leaves search mode and navigates into that folder — `rxExitSearch({ noBrowse: true })` suppresses its usual re-browse of the pre-search folder so the two navigations don't race (the stale re-browse used to win and snap you back to where you searched from); the Back button while searching uses the same guard. Any root can be hidden with its `⊘` button (it moves to a **Hidden** section) and restored with `＋`; the set persists in `ui_state.json`. **Right-click any row** for a one-item **Ask Claude** menu that opens a `claude` terminal seeded with a prompt about that entry (see *Capabilities with Claude → Right-click an explorer entry*). No writes — `Api.browse` refuses any path outside the known roots. |

**Applying a fallback decision** (`fallback_ops.apply_decision`):

The form sends `{kind, category_id | category_name, destination, seller,
product, match}`, where `seller` / `product` are `{value, every_mail, extract}` —
`value` is always what *this* mail's folder is named with; `every_mail` /
`extract` only shape what a **new** category remembers.

- `new_category` — create a category (destination, seller/product specs from the
  tick / extraction, the match rule), move + rename the folder from
  `_לטיפול ידני` into the destination, mark resolved, history row `RESOLVED`
  (`resolution: category`). An invalid regex or a rule with neither a sender nor
  a subject condition is rejected before anything moves.
- `category` — add the match rule to an existing category and file into *its*
  destination (its seller/product settings are left as they are).
- `once` — move + rename into the chosen destination; no category is written.
- `exclude` — add the match rule to the shared `excluded` category, delete the
  folder, log to `cleanup_log.json`, mark resolved.
- `skip` — **dismiss**: mark resolved and record `resolution: dismissed` in
  History; the folder stays in `_לטיפול ידני` and nothing is remembered, so a
  similar mail later lands in fallbacks again.

The `RESOLVED` history write is an **upsert** (`history.upsert`), not a plain
`history.update`: it patches the fallback's existing history row if there is one,
otherwise it appends a fresh row seeded from the `fallback_log.json` entry
(`id`, account, date, sender, subject, folder). Every resolution also records
`resolved_at` (now, ISO seconds) and `resolved_by` — `apply_decision` defaults it
to `user`; callers pass `resolved_by="claude"` for batch resolves. Plain `update` silently dropped
the write whenever no scan-time `FALLBACK` row existed (e.g. `history.json` not
yet created, or the fallback logged before scan-time history recording), so
hand-resolved fallbacks never showed up in the History tab.

After a successful **Apply**, the UI also calls `refreshHistory()` (drops the
cached page state and re-fetches from the top), so the new `RESOLVED` row shows
without reopening the window. Fallbacks resolved *before* this fix left no
history trace — run `python backfill_fallback_history.py` once to create their
rows from `fallback_log.json`.

**Tray:** a resident tray icon (Open / Run scan now / Quit). Closing the window
hides it to the tray; Quit ends the process.

**Clickable icon:** run `python make_shortcut.py` once to drop **Receipt Saver**
shortcuts on the Desktop and in the Start Menu. They launch `pythonw app.py`
directly (no console flash) with `assets/receipt_saver.ico`. Regenerate the icon
with `python make_icon.py`. `--startmenu-only` limits it to the Start Menu.
Launch-at-login is a separate step — `python install_startup.py`.

**`history.json` record shape:** `id` (`account:messageId`), `run_id`,
`handled_at`, `account`, `account_email`, `date`, `sender`, `subject`, `action`
(`DOWNLOADED | ICOUNT | JAPANOLOGIA | FALLBACK | EXCLUDED | RESOLVED`), `seller`,
`product`, `category`, `folder_name`, `folder_path`, `files`, `rule_source`
(`hardcoded | custom | icount | japanologia | null`). Resolved fallbacks also get
`resolution` (`rule | once | exclude`, or `backfilled` for pre-fix rows),
`resolved_at` (ISO-8601 seconds — a bare `YYYY-MM-DD` for date-only backfills),
and `resolved_by`: **`user`** (worked through the Fallbacks tab form),
**`claude`** (batch-resolved by `move_fallbacks.py`), or **`unknown`**
(backfilled, source not recorded). The same `resolved_at` / `resolved_by` are
also written back onto the `fallback_log.json` entry. The History card shows
`· resolved <date> by you/Claude/backfill` on these rows.

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

The `from:` exceptions are generated automatically from every domain-based `sender_contains` in `categories.json` (`categories.query_terms()`, exclude categories included). Adding a category rule with a domain automatically updates the query — no manual changes needed. The Japanese lesson clause is hardcoded in `build_gmail_query()`, which now lives in `gmail_provider.py` (called via `gmail_provider.list_candidate_ids()`).

**Key behaviors:**
- Emails with attachments matching subject keywords are always included
- Known senders (from categories.json) are always included even without attachments — their email body is saved as `email.pdf`
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
   - Adds the sender to `categories.json` (or tells you what to enter in the Fallbacks form) so it is recognized automatically next time
   - Provides a `move_fallbacks.py` script that renames and moves the folders from `_לטיפול ידני\` to the main `קבלות\` directory with correct names
   - Updates all resolved entries in `fallback_log.json`

### "add a rule for X"

You can tell Claude directly to add a rule, for example:
- *"emails from noreply@bezeq.co.il are receipts from בזק - חשבונית חודשית"*
- *"emails from amazon.com with 'order' in the subject are from Amazon - הזמנה"*

Claude will update `categories.json` (you can also do it yourself in the **Categories** tab).

### Asking about your receipts

Since Claude has Gmail MCP access to your `ofek` account, you can ask things like:
- *"Did I get a Cellcom bill this month?"*
- *"Show me all my Wolt receipts from March"*
- *"How much did I spend on KSP last year?"*

### Right-click an explorer entry → "Ask Claude"

In the **Receipts** view, right-clicking any row (folder or file, in the plain
listing or in search results) opens a one-item menu. **Ask Claude** calls
`Api.ask_claude_receipt(entry)` → `claude_handoff.launch_receipt`, which opens a
`claude` terminal in the repo (window title `Claude - receipt`) pre-seeded with a
one-line prompt naming that entry — its cleaned title, the account/date shown on
the row, and its full path — so you can pick up the conversation from there. Same
single-token `cmd /k claude "<prompt>"` mechanism as the fallback / error
hand-offs; double quotes in any field are neutralised to `'`. The menu is a
single `#rx-ctx-menu` element in `index.html`, positioned at the cursor (clamped
to the viewport) and dismissed on outside click, `Escape`, window blur, or
scroll.

---

## categories.json Format

```jsonc
[
  {
    "id": "electricity",                       // stable slug
    "name": "חשמל",                            // display name
    "destination": "C:\\Users\\ofeks\\OneDrive\\Documents\\קבלות\\חשבנות\\חשמל",
    "seller":  { "mode": "fixed", "value": "חברת חשמל לישראל" },
    "product": { "mode": "extract", "source": "body",
                 "regex": "(תלוש שכר לחודש \\S+ \\d{4})", "fallback": "תלוש שכר" },
    "exclude": false,
    "match": [
      { "sender_contains": "iec.co.il" },
      { "sender_contains": "onecity.co.il", "subject_contains": ["חשמל", "חשבון"],
        "exclude_subject_contains": "פרסומת", "body_contains": "מספר חשבון",
        "exclude_body_contains": "בוטל", "attachments": "one" }
    ]
  }
]
```

- `destination` — the full folder path matched receipts are filed into (`null` → `קבלות`). Several categories may share one (e.g. every electricity provider → `קבלות\חשבנות\חשמל`).
- `seller` / `product` — a spec:
  - `{"mode": "fixed", "value": …}` — always this text
  - `{"mode": "extract", "source": "subject" | "body" | "sender_name", "regex": …, "fallback"?: …}` — the first `( )` group of the regex (or the whole match), folder-sanitized; `fallback` when it doesn't match
  - `null` — the app's suggestion (`naming.suggest_names`)

  Resolution per mail: fixed → extraction → `fallback` → app suggestion, so a folder name is never empty.
- `match` — OR-list; a mail matches an entry when **all** its conditions hold. Text conditions take a string or a list of strings: `sender_contains` / `subject_contains` (case-insensitive, **every** listed value must appear), `exclude_subject_contains` (**none** of the values may appear in the subject), `body_contains` / `exclude_body_contains` (the same, case-sensitive, against the whitespace-normalised plain-text body). `attachments` — `"none"` / `"one"` / `"many"` (2+); counts documents only (images such as logos are ignored); omitted = any. Providers report `attachment_count` from attachment names (no download) and fallback entries record it; older fallback entries are counted from their folder. Each entry needs a sender or subject condition. One value is stored as a string, several as a list.
- `exclude` — `true` silently skips matching mail (no folder, logged as `EXCLUDED`). New exclusions from the Fallbacks form go into the shared `excluded` category.
- Categories are checked in file order; the first category with a matching entry wins.

The hardcoded `KNOWN_RULES` in `receipt_saver.py` still run *before* categories (and still file into `קבלות` sub-folders such as `חשבנות/אינטרנט`); converting them into categories is a planned follow-up.

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
| Window not appearing at login | Check `receipt_saver.log` for the `[app.py]` trail. No `launch` line → the task isn't registered: run `python install_startup.py`. `launch` but no `window created` → read the `FATAL` traceback. All four lines but still no window → run `schtasks /run /tn ReceiptSaverUI` and check which monitor it opened on. Confirm the task exists: `schtasks /query /tn ReceiptSaverUI` |
| A terminal/console pops up at login instead of the window | A stale `run.bat` (or other `.bat`) is in `shell:startup`. Delete it, then `python install_startup.py` (it clears leftover Startup-folder launchers and uses the console-free task) |
| Startup window is blank | Check `receipt_saver.log` for an `[app.py]` line; run `python app.py` (not `pythonw`) once to see console errors |
| Want to open the window without scanning | `set RECEIPT_SAVER_UI_DRYRUN=1` then run `app.py` |
| Gmail auth error | Delete `token_[account].json` and run `receipt_saver.py` manually to re-authorize |
| Scan says "&lt;account&gt; needs re-authorization" (Outlook/sternum) | Run `python outlook_auth.py` and complete the device-code sign-in at microsoft.com/device |
| TickTick tasks not created | Check `ticktick_token.json` exists; re-run `ticktick_auth.py` if needed |
| No notifications | Run `pip install plyer` |
| No email.pdf created | Run `pip install weasyprint` |
| Email not picked up | Check `receipt_saver.log` — may be a subject keyword mismatch |
| Duplicate folders | Should not happen — `processed_ids.json` prevents reprocessing |
