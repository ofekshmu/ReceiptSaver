# Rules + roots — design

Date: 2026-10-07 · Status: approved in chat · Supersedes the "category" model of
`2026-10-05-fallback-config-remodel-design.md` (naming specs, match conditions,
keyword chips and the attachments condition are unchanged).

## Why
One category per mail type made the picker grow with every new sender. Split it:
**rules** identify a mail type (matching + naming); **roots** are the few places
receipts go. The fallback picker shows roots (≈12 tiles), not rules.

## Model — `rules.json`
```jsonc
{
  "roots": [ { "id": "electricity", "name": "חשמל",
               "folder": "C:\\…\\קבלות\\חשבנות\\חשמל", "color": "#d6efcf" } ],
  "rules": [ { "id": "iec", "name": "חברת חשמל לישראל", "root": "electricity",
               "seller": {…spec…}, "product": {…spec…}, "exclude": false,
               "match": [ {…conditions…} ] } ]
}
```
- Root = a place only: id, name, folder, colour (auto from the pastel palette).
- Rule = matching + naming + `root` id. Exclude rules have `root: null`; new
  exclusions go into the shared `excluded` rule.
- First matching rule wins (file order); files into its root's folder (a missing
  root falls back to קבלות).

## Migration — `migrate_rules_v3.py`
Each distinct category destination → a root (named after the folder; parent
added on name clashes, e.g. `שלום שבאזי 7 › חשמל`); each category → a rule.
Dry run + routing-equivalence check over History + fallback log + a synthetic
hit per alternative; `--apply` writes `rules.json` and renames
`categories.json` → `categories.v2.json`. `migrate_categories_v2.py` retires.

## Fallbacks — "File under a root"
Root tile grid (＋ New root first; name, folder, N rules; root colour; search
also matches the rules inside). After a root: rule chips — ＋ New rule (default)
+ that root's rules (the sender's known rule preselected).
- ＋ New root → root name + destination picker.
- ＋ New rule → rule name (defaults to seller), seller/product widgets, match rule + chips.
- Existing rule → this mail's resolved names (one-off), its alternatives listed,
  this mail's conditions added as a new alternative.
Exclude / Move once / Skip unchanged.

Decision payload: `{kind: "rule"|"new_rule"|"once"|"exclude"|"skip",
rule_id, rule_name, root_id | new_root: {name, folder}, destination (once),
seller, product, match}`.

## Rules tab (was Categories)
Search; grouped by root (header: colour, name, folder, count, edit
name/folder/colour, delete — empty only, else move rules to another root,
＋ New rule); rule rows (name, summary, root select, merge within root, save,
delete, ▸ editor with seller/product + alternatives + Add alternative);
＋ New root in the header; Excluded group last.

## Amendment (2026-10-07, v1.1.29)
- The option is labelled **"Change/add rule"** (was "File under a root").
- **＋ New root** opens the Windows folder picker directly; the chosen folder
  becomes a temporary tile (dashed, `NEW ROOT`, next free colour) that is saved
  as a root — with its colour — only when the fallback is applied. No separate
  folder field for new roots.
- Root tiles are smaller and show only `N rules`; the folder line is the
  innermost folder, with parents added only as far as needed to disambiguate.
- Scrollbars app-wide: slim, single-colour thumb, no arrows.
- (v1.1.30) The folder line is shown only when the root's name doesn't already
  contain the folder's name (no duplicate path on derived names).
- (v1.1.32) "Change/add rule" has two steps split by a thin line — ① Root
  (collapses to a summary once chosen; "change" reopens) and ② Rule
  (collapsed until a root is chosen). Unchosen options shrink to one line.
  The email's header and the chosen option's row stay pinned while scrolling.
