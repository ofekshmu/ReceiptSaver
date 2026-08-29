"""
migrate_rules_to_categories.py
------------------------------
One-off, reversible migration: turn the flat `custom_rules.json` (one entry per
sender) into `categories.json` (see categories.py) — **1 rule -> 1 category**,
losslessly. Grouping several senders under one category is a later UI action;
this migration changes *nothing* about what matches where.

Safety:
  * default is a DRY RUN — prints the proposed categories.json and runs an
    equivalence check (old match_custom vs new match_category over every
    sender/subject seen in history.json + fallback_log.json, plus a synthetic
    hit/miss pair per rule). It refuses --apply if any routing differs.
  * --apply writes categories.json and RENAMES custom_rules.json to
    custom_rules.legacy.json (kept, never deleted). Refuses to overwrite an
    existing categories.json / legacy file unless --force.
  * rollback: delete categories.json, rename custom_rules.legacy.json back.

Usage:
    python migrate_rules_to_categories.py            # dry run + verify
    python migrate_rules_to_categories.py --apply
"""

import json
import sys
from pathlib import Path

import categories as C

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SCRIPT_DIR    = Path(__file__).parent
RULES_FILE    = SCRIPT_DIR / "custom_rules.json"
LEGACY_FILE   = SCRIPT_DIR / "custom_rules.legacy.json"
CATS_FILE     = SCRIPT_DIR / "categories.json"
HISTORY_FILE  = SCRIPT_DIR / "history.json"
FALLBACK_FILE = SCRIPT_DIR / "fallback_log.json"

# rule key  ->  match[] entry key
_MATCH_MAP = {
    "match_sender_contains":    "sender_contains",
    "match_subject_contains":   "subject_contains",
    "exclude_subject_contains": "exclude_subject_contains",
    "match_body_contains":      "body_contains",
    "product_body_regex":       "product_body_regex",
}


def _load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return []


def build_categories(rules: list) -> list:
    """1 rule -> 1 category, order preserved (so first-match-wins is identical)."""
    cats, taken = [], set()
    for rule in rules:
        entry = {}
        for rk, mk in _MATCH_MAP.items():
            v = rule.get(rk)
            if v not in (None, "", []):
                entry[mk] = v
        seller = rule.get("seller")
        frag = rule.get("match_sender_contains") or ""
        name = seller or frag or (rule.get("_comment") or "category")
        cid = C.slugify(name, taken)
        taken.add(cid)
        cat = {
            "id": cid,
            "name": (name or cid)[:60],
            "seller": seller,
            "product": rule.get("product"),
            "base_dir": rule.get("base_dir"),
            "subfolder": rule.get("category"),      # old `category` == sub-folder path
            "exclude": bool(rule.get("exclude")),
            "match": [entry],
        }
        if rule.get("_comment"):
            cat["_comment"] = rule["_comment"]
        cats.append(cat)
    return cats


def _corpus(rules: list) -> list:
    """(sender, subject, body) triples to compare routing on."""
    seen, out = set(), []

    def add(s, j, b=""):
        key = (s, j, b)
        if key not in seen:
            seen.add(key)
            out.append(key)

    for row in _load(HISTORY_FILE):
        add(row.get("sender") or "", row.get("subject") or "")
    for e in _load(FALLBACK_FILE):
        add(e.get("sender") or "", e.get("subject") or "")

    # synthetic hit + near-miss per rule
    for r in rules:
        sf = r.get("match_sender_contains") or "x@example.test"
        jf = r.get("match_subject_contains") or ""
        bf = r.get("match_body_contains") or ""
        xf = r.get("exclude_subject_contains") or ""
        add(f"no-reply@{sf}", f"{jf} receipt", f"body {bf}")
        add("someone@unrelated.invalid", "unrelated subject", "unrelated body")
        if xf:
            add(f"no-reply@{sf}", f"{jf} {xf}", f"body {bf}")
    return out


def verify(rules: list, cats: list) -> list:
    """Return a list of (triple, old, new) where routing differs. Empty == safe."""
    import receipt_saver
    mismatches = []
    for (s, j, b) in _corpus(rules):
        old = receipt_saver.match_custom(s, j, b)          # reads custom_rules.json
        new = C.match_category(s, j, b, categories=cats)
        if _norm(old) != _norm(new):
            mismatches.append(((s, j, b), old, new))
    return mismatches


def _norm(res):
    if res is None:
        return None
    seller, product, sub, base = res
    return (seller, product, sub, str(base) if base is not None else None)


def main(apply: bool = False, force: bool = False) -> int:
    rules = _load(RULES_FILE)
    if not rules:
        print(f"no rules found in {RULES_FILE}")
        return 1
    cats = build_categories(rules)

    print(f"{len(rules)} rules -> {len(cats)} categories "
          f"({sum(1 for c in cats if c['exclude'])} exclude)\n")
    print(json.dumps(cats, ensure_ascii=False, indent=2)[:4000])
    print("  ... (truncated)\n" if len(json.dumps(cats)) > 4000 else "")

    diffs = verify(rules, cats)
    if diffs:
        print(f"!! {len(diffs)} routing MISMATCH(es) — migration NOT safe:\n")
        for (triple, old, new) in diffs[:20]:
            print(f"   {triple}\n     old: {_norm(old)}\n     new: {_norm(new)}")
        return 2
    print(f"equivalence check passed over {len(_corpus(rules))} sender/subject cases.")

    if not apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to migrate.")
        return 0

    if CATS_FILE.exists() and not force:
        print(f"\n{CATS_FILE.name} already exists — refusing (use --force).")
        return 3
    if LEGACY_FILE.exists() and not force:
        print(f"\n{LEGACY_FILE.name} already exists — refusing (use --force).")
        return 3

    C.save_categories(cats, path=CATS_FILE)
    RULES_FILE.replace(LEGACY_FILE)
    print(f"\nwrote {CATS_FILE.name}; moved custom_rules.json -> {LEGACY_FILE.name}")
    print("rollback: delete categories.json and rename custom_rules.legacy.json back.")
    return 0


if __name__ == "__main__":
    sys.exit(main(apply="--apply" in sys.argv[1:], force="--force" in sys.argv[1:]))
