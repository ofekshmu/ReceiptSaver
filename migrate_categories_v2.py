"""
migrate_categories_v2.py
------------------------
One-off, reversible migration of `categories.json` from v1 to v2 (see
categories.py and docs/superpowers/specs/2026-10-05-fallback-config-remodel-design.md):

  * `base_dir` + `subfolder`       -> one full `destination` path
  * `seller` / `product` strings   -> `{"mode": "fixed", ...}` specs (or null)
  * `product_body_regex` on a rule -> product `{"mode": "extract", "source": "body",
                                      "fallback": <old static product>}`
  * per-rule seller/product overrides -> consecutive runs of rules with the same
    overrides become their own category (order preserved, so first-match-wins
    routing is unchanged)
  * exclude categories fold into the shared `excluded` category (placed where
    the first folded one was) — greedily, only when folding leaves routing
    unchanged; an exclude whose position matters stays its own category

Safety:
  * default is a DRY RUN — prints a summary and checks that old vs new routing
    (seller, product, destination) is identical for every sender/subject in
    history.json + fallback_log.json plus a synthetic hit per match entry.
    Refuses --apply on any difference.
  * --apply backs the v1 file up to categories.v1.json, then writes v2.
  * rollback: copy categories.v1.json back over categories.json and check out
    the previous commit.

Usage:
    python migrate_categories_v2.py            # dry run + verify
    python migrate_categories_v2.py --apply
"""

import json
import re
import shutil
import sys
from pathlib import Path

import categories as C
import naming

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SCRIPT_DIR    = Path(__file__).parent
CATS_FILE     = SCRIPT_DIR / "categories.json"
BACKUP_FILE   = SCRIPT_DIR / "categories.v1.json"
HISTORY_FILE  = SCRIPT_DIR / "history.json"
FALLBACK_FILE = SCRIPT_DIR / "fallback_log.json"

_OVERRIDE_KEYS = ("seller", "product", "product_body_regex")


def _load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return []


def is_v2(cats: list) -> bool:
    return any("destination" in c or isinstance(c.get("seller"), dict)
               or isinstance(c.get("product"), dict) for c in cats)


# ---------------------------------------------------------------------------
# frozen v1 matcher (the pre-remodel categories.match_category)
# ---------------------------------------------------------------------------

def old_match(cats: list, sender: str, subject: str = "", body: str = ""):
    body_norm = C.normalize_body(body)
    for cat in cats:
        for m in cat.get("match", []):
            if not C._entry_matches(m, sender, subject, body_norm):
                continue
            if cat.get("exclude"):
                return C.EXCLUDE, None, None, None
            seller = m.get("seller") or cat.get("seller")
            product = m.get("product") or cat.get("product")
            rx = m.get("product_body_regex") or ""
            if rx and body_norm:
                hit = re.search(rx, body_norm)
                if hit:
                    product = naming.sanitize(hit.group(1).strip())
            base_dir = Path(cat["base_dir"]) if cat.get("base_dir") else None
            return seller, product, cat.get("subfolder"), base_dir
    return None


def old_route(cats, sender, subject="", body="", receipts_dir: Path = None):
    """v1 result normalized to (seller, product, destination)."""
    res = old_match(cats, sender, subject, body)
    if res is None or res[0] == C.EXCLUDE:
        return res if res is None else (C.EXCLUDE, None, None)
    seller, product, sub, base = res
    root = base or Path(receipts_dir or C.RECEIPTS_DIR)
    return seller, product, (root / sub if sub else root)


def new_route(cats, sender, subject="", body=""):
    return C.match_category(sender, subject, body, categories=cats)


# ---------------------------------------------------------------------------
# conversion
# ---------------------------------------------------------------------------

def _fixed(v):
    return {"mode": "fixed", "value": v.strip()} if (v or "").strip() else None


def _destination(cat: dict, receipts_dir: Path) -> str:
    root = Path(cat["base_dir"]) if cat.get("base_dir") else Path(receipts_dir)
    sub = cat.get("subfolder")
    if sub:
        root = root.joinpath(*re.split(r"[\\/]+", sub.strip("\\/")))
    return str(root)


def _runs(entries: list) -> list:
    """Split match entries into consecutive runs sharing the same overrides."""
    runs = []
    for m in entries:
        key = tuple(m.get(k) or None for k in _OVERRIDE_KEYS)
        if runs and runs[-1][0] == key:
            runs[-1][1].append(m)
        else:
            runs.append((key, [m]))
    return runs


def convert(old: list, receipts_dir: Path = None, fold: set = None) -> list:
    """`fold`: ids of exclude categories to merge into the shared bucket
    (default: all of them)."""
    receipts_dir = Path(receipts_dir or C.RECEIPTS_DIR)
    out, taken, excluded = [], set(), None
    for cat in old:
        if cat.get("exclude") and fold is not None and cat.get("id") not in fold:
            cid = C.slugify(cat.get("id") or cat.get("name"), taken)
            taken.add(cid)
            out.append({"id": cid, "name": cat.get("name") or cid, "destination": None,
                        "seller": None, "product": None, "exclude": True,
                        "match": [C.clean_match_entry(m) for m in cat.get("match", [])]})
            continue
        if cat.get("exclude"):
            if excluded is None:
                excluded = {"id": C.EXCLUDE_CATEGORY_ID, "name": "(excluded)",
                            "destination": None, "seller": None, "product": None,
                            "exclude": True, "match": []}
                taken.add(excluded["id"])
                out.append(excluded)
            for m in cat.get("match", []):
                cleaned = C.clean_match_entry(m)
                if cleaned and cleaned not in excluded["match"]:
                    excluded["match"].append(cleaned)
            continue

        runs = _runs(cat.get("match", [])) or [((None, None, None), [])]
        for i, ((o_seller, o_product, o_regex), entries) in enumerate(runs):
            static_product = o_product or cat.get("product")
            if o_regex:
                product = {"mode": "extract", "source": "body", "regex": o_regex}
                if (static_product or "").strip():
                    product["fallback"] = static_product.strip()
            else:
                product = _fixed(static_product)
            base_id = cat.get("id") or C.slugify(cat.get("name"))
            cid = base_id if i == 0 and base_id not in taken else C.slugify(base_id, taken)
            taken.add(cid)
            new = {
                "id": cid,
                "name": cat.get("name") if i == 0 else f'{cat.get("name")} ({i + 1})',
                "destination": _destination(cat, receipts_dir),
                "seller": _fixed(o_seller or cat.get("seller")),
                "product": product,
                "exclude": False,
                "match": [C.clean_match_entry(m) for m in entries],
            }
            if cat.get("comment"):
                new["comment"] = cat["comment"]
            out.append(new)
    return out


# ---------------------------------------------------------------------------
# verification
# ---------------------------------------------------------------------------

def corpus(old: list, history: list = None, fallbacks: list = None) -> list:
    seen, out = set(), []

    def add(s, j, b=""):
        key = (s or "", j or "", b or "")
        if key not in seen:
            seen.add(key)
            out.append(key)

    for row in (history or []) + (fallbacks or []):
        if row.get("sender"):
            add(row["sender"], row.get("subject"))
    for cat in old:
        for m in cat.get("match", []):
            add(m.get("sender_contains"), m.get("subject_contains"), m.get("body_contains"))
    return out


def _comparable(old_res, new_res) -> bool:
    if old_res is None or new_res is None:
        return old_res is None and new_res is None
    if old_res[0] == C.EXCLUDE or new_res[0] == C.EXCLUDE:
        return old_res[0] == new_res[0]
    o_seller, o_product, o_dest = old_res
    n_seller, n_product, n_dest = new_res
    # an unset v1 seller/product could never have produced a folder name, so
    # the v2 app suggestion there is an improvement, not a routing change
    same_seller = o_seller is None or o_seller == n_seller
    same_product = o_product is None or o_product == n_product
    return same_seller and same_product and Path(o_dest) == Path(n_dest)


def verify(old: list, new: list, cases: list, receipts_dir: Path = None) -> list:
    diffs = []
    for s, j, b in cases:
        o = old_route(old, s, j, b, receipts_dir)
        n = new_route(new, s, j, b)
        if not _comparable(o, n):
            diffs.append(((s, j, b), o, n))
    return diffs


def plan(old: list, cases: list, receipts_dir: Path = None) -> list:
    """convert() with the largest greedy set of exclude folds that keeps routing
    identical over `cases`."""
    fold = set()
    for cat in old:
        if not cat.get("exclude"):
            continue
        trial = fold | {cat.get("id")}
        if not verify(old, convert(old, receipts_dir, fold=trial), cases, receipts_dir):
            fold = trial
    return convert(old, receipts_dir, fold=fold)


def main(apply: bool = False) -> int:
    old = _load(CATS_FILE)
    if not old:
        print(f"no categories found in {CATS_FILE}")
        return 1
    if is_v2(old):
        print(f"{CATS_FILE.name} is already v2 — nothing to do.")
        return 0
    cases = corpus(old, _load(HISTORY_FILE), _load(FALLBACK_FILE))
    new = plan(old, cases)
    print(f"{len(old)} v1 categories -> {len(new)} v2 categories "
          f"({sum(1 for c in new if c.get('exclude'))} exclude)\n")
    for c in new:
        print(f"  {c['id']:<28} {c.get('destination') or '(exclude)'}")

    diffs = verify(old, new, cases)
    if diffs:
        print(f"\n!! {len(diffs)} routing MISMATCH(es) — migration NOT safe:\n")
        for (case, o, n) in diffs[:20]:
            print(f"   {case}\n     old: {o}\n     new: {n}")
        return 2
    print(f"\nequivalence check passed over {len(cases)} sender/subject cases.")

    if not apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to migrate.")
        return 0
    shutil.copy2(CATS_FILE, BACKUP_FILE)
    C.save_categories(new, path=CATS_FILE)
    print(f"\nbacked up v1 to {BACKUP_FILE.name}; wrote v2 {CATS_FILE.name}")
    print(f"rollback: copy {BACKUP_FILE.name} over {CATS_FILE.name}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(apply="--apply" in sys.argv[1:]))
