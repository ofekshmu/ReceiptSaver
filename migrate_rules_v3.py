"""
migrate_rules_v3.py
-------------------
One-off, reversible migration from `categories.json` (v2: a list of categories,
each with its own `destination`) to `rules.json` (v3: `roots` + `rules`, see
rules.py and docs/superpowers/specs/2026-10-07-rules-and-roots-design.md):

  * every distinct category destination -> a root, named after its folder
    (the parent folder is prepended when two roots would share a name,
    e.g. `שלום שבאזי 7 › חשמל`), coloured from the pastel palette
  * every category -> a rule (same id, name, naming specs, match alternatives,
    order) pointing at its root; exclude categories -> exclude rules (no root)

Safety:
  * default is a DRY RUN — prints the roots and checks that old vs new routing
    (seller, product, folder) is identical for every sender/subject in
    history.json + fallback_log.json plus a synthetic hit per alternative.
    Refuses --apply on any difference.
  * --apply writes rules.json and renames categories.json -> categories.v2.json.
  * rollback: delete rules.json, rename categories.v2.json back, check out the
    previous commit.

Usage:
    python migrate_rules_v3.py            # dry run + verify
    python migrate_rules_v3.py --apply
"""

import json
import os
import sys
from pathlib import Path

import rules as R

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SCRIPT_DIR    = Path(__file__).parent
CATS_FILE     = SCRIPT_DIR / "categories.json"
BACKUP_FILE   = SCRIPT_DIR / "categories.v2.json"
RULES_FILE    = SCRIPT_DIR / "rules.json"
HISTORY_FILE  = SCRIPT_DIR / "history.json"
FALLBACK_FILE = SCRIPT_DIR / "fallback_log.json"


def _load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return []


def _norm(p) -> str:
    return os.path.normcase(os.path.normpath(str(p)))


def root_names(folders: list) -> list:
    """Folder name, or — when several folders share it — `<ancestor> › <name>`
    using the nearest ancestor that tells them apart (e.g. `קבלות › חשמל` vs
    `שלום שבאזי 7 › חשמל`)."""
    names = [Path(f).name or f for f in folders]
    out = list(names)
    for name in set(names):
        group = [i for i, n in enumerate(names) if n == name]
        if len(group) < 2:
            continue
        chains = [list(Path(folders[i]).parents) for i in group]
        for k in range(max(len(c) for c in chains)):
            labels = [c[k].name if k < len(c) else "" for c in chains]
            if len(set(labels)) == len(labels) and all(labels):
                for i, lbl in zip(group, labels):
                    out[i] = f"{lbl} › {name}"
                break
        else:
            for i in group:
                out[i] = folders[i]
    return out


def convert(cats: list, receipts_dir: Path = None) -> dict:
    receipts_dir = Path(receipts_dir or R.RECEIPTS_DIR)
    folders = []                                   # distinct, first-seen order
    for c in cats:
        if not c.get("exclude"):
            f = str(c.get("destination") or receipts_dir)
            if _norm(f) not in {_norm(x) for x in folders}:
                folders.append(f)

    data = R.empty()
    by_folder = {}
    for f, name in zip(folders, root_names(folders)):
        root = R.new_root(name, f, data=data)
        data["roots"].append(root)
        by_folder[_norm(f)] = root["id"]

    taken = set()
    for c in cats:
        rid = c.get("id") or R.slugify(c.get("name"), taken)
        if rid in taken:
            rid = R.slugify(rid, taken)
        taken.add(rid)
        exclude = bool(c.get("exclude"))
        rule = {
            "id": rid,
            "name": c.get("name") or rid,
            "root": None if exclude else by_folder[_norm(c.get("destination") or receipts_dir)],
            "seller": c.get("seller"),
            "product": c.get("product"),
            "exclude": exclude,
            "match": [R.clean_match_entry(m) for m in c.get("match", [])],
        }
        if c.get("comment"):
            rule["comment"] = c["comment"]
        data["rules"].append(rule)
    return data


# ---------------------------------------------------------------------------
# verification
# ---------------------------------------------------------------------------

def old_route(cats: list, sender, subject="", body="", receipts_dir: Path = None):
    """v2 routing: first category with a matching entry; its own destination."""
    body_norm = R.normalize_body(body)
    for c in cats:
        for m in c.get("match", []):
            if R._entry_matches(m, sender, subject, body_norm):
                if c.get("exclude"):
                    return R.EXCLUDE, None, None
                seller, product = R.resolve_names(c, sender, subject, body)
                return seller, product, Path(c.get("destination") or receipts_dir or R.RECEIPTS_DIR)
    return None


def corpus(cats: list, history: list = None, fallbacks: list = None) -> list:
    seen, out = set(), []

    def add(s, j, b=""):
        key = (s or "", j or "", b or "")
        if key not in seen:
            seen.add(key)
            out.append(key)

    for row in (history or []) + (fallbacks or []):
        if row.get("sender"):
            add(row["sender"], row.get("subject"))
    for c in cats:
        for m in c.get("match", []):
            add(" ".join(R.as_list(m.get("sender_contains"))),
                " ".join(R.as_list(m.get("subject_contains"))),
                " ".join(R.as_list(m.get("body_contains"))))
    return out


def verify(cats: list, data: dict, cases: list, receipts_dir: Path = None) -> list:
    diffs = []
    for s, j, b in cases:
        o = old_route(cats, s, j, b, receipts_dir)
        n = R.match_rule(s, j, b, data=data)
        if o != n:
            diffs.append(((s, j, b), o, n))
    return diffs


def main(apply: bool = False) -> int:
    cats = _load(CATS_FILE)
    if not isinstance(cats, list) or not cats:
        print(f"no v2 categories found in {CATS_FILE}")
        return 1
    data = convert(cats)
    counts = R.rule_counts(data)
    print(f"{len(cats)} categories -> {len(data['roots'])} roots + {len(data['rules'])} rules "
          f"({sum(1 for r in data['rules'] if r['exclude'])} exclude)\n")
    for root in data["roots"]:
        print(f"  {root['name']:<34} {counts.get(root['id'], 0):>2} rules   {root['folder']}")

    cases = corpus(cats, _load(HISTORY_FILE), _load(FALLBACK_FILE))
    diffs = verify(cats, data, cases)
    if diffs:
        print(f"\n!! {len(diffs)} routing MISMATCH(es) — migration NOT safe:\n")
        for (case, o, n) in diffs[:20]:
            print(f"   {case}\n     old: {o}\n     new: {n}")
        return 2
    print(f"\nequivalence check passed over {len(cases)} sender/subject cases.")
    if not apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to migrate.")
        return 0
    if RULES_FILE.exists():
        print(f"\n{RULES_FILE.name} already exists — refusing.")
        return 3
    R.save(data, RULES_FILE)
    CATS_FILE.replace(BACKUP_FILE)
    print(f"\nwrote {RULES_FILE.name}; moved {CATS_FILE.name} -> {BACKUP_FILE.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(apply="--apply" in sys.argv[1:]))
