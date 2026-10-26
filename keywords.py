"""
keywords.py
-----------
Keyword suggestions for building a category's match rule from one email: the
Fallbacks form shows them as chips the user clicks into the rule's
sender / subject / body conditions.

`suggest(sender, subject, body)` -> {"sender": [...], "subject": [...], "body": [...]}

Every subject/body keyword is a verbatim slice of the (whitespace-normalised)
text, so a picked keyword is guaranteed to match the email it came from.
Tokens that carry 2+ digits (order numbers, dates, amounts) and filler words are
never offered — they would make the rule match only this one email.
"""

import re

import naming

MAX_PER_GROUP = 12

# phrase boundaries (a keyword never spans one of these)
_SEPARATORS = r"[\-–—|:,;/\\#()\[\]<>!?•·‏‎]"
_TOKEN = re.compile(r"[^\s\-–—|:,;/\\#()\[\]<>!?•·‏‎]+")
_STRIP = ".'`*_~=+"

_STOPWORDS = {
    # Hebrew
    "של", "את", "על", "עם", "אל", "עבור", "מאת", "לך", "לכם", "שלך", "שלכם", "שלום",
    "תודה", "אנא", "כאן", "לחץ", "לחצו", "או", "גם", "זה", "זו", "הוא", "היא", "אנו",
    "אנחנו", "לצפייה", "כי", "אם", "יש", "אין", "כל", "רק", "עד", "בין", "הנה", "היי",
    "מצ\"ב", "מצורף", "מצורפת", "בברכה", "רב", "ב", "ל", "מ", "ה", "ו", "ש",
    # English
    "the", "a", "an", "your", "you", "from", "for", "to", "of", "and", "or", "in", "on",
    "at", "is", "are", "with", "by", "this", "that", "our", "we", "please", "here",
    "click", "re", "fw", "fwd", "dear", "hi", "hello", "thank", "thanks", "no", "not",
}

# phrases that usually mark a real receipt — offered first when present in the body
_CUES = (
    "מספר הזמנה", "חשבונית מס קבלה", "חשבונית מס", "מספר חשבונית", "קבלה מספר",
    "אישור תשלום", "תלוש שכר", "סה\"כ לתשלום", "order number", "invoice number",
    "receipt", "invoice",
)


def _keep(tok: str) -> bool:
    if len(tok) < 2 or tok.lower() in _STOPWORDS:
        return False
    digits = sum(ch.isdigit() for ch in tok)
    if digits >= 2 or digits == len(tok):
        return False
    return any(ch.isalpha() for ch in tok)


def _runs(text: str) -> list:
    """Maximal runs of kept tokens inside each separator-delimited segment, as
    lists of (start, end) spans into `text`."""
    runs = []
    for seg in re.finditer(r"[^" + _SEPARATORS[1:-1] + r"]+", text):
        cur = []
        for m in _TOKEN.finditer(seg.group(0)):
            raw = m.group(0)
            lead = len(raw) - len(raw.lstrip(_STRIP))
            tok = raw.strip(_STRIP)
            start = seg.start() + m.start() + lead
            if _keep(tok):
                cur.append((start, start + len(tok)))
            else:
                if cur:
                    runs.append(cur)
                cur = []
        if cur:
            runs.append(cur)
    return runs


def _dedup(items: list) -> list:
    seen, out = set(), []
    for it in items:
        key = it.lower()
        if it and key not in seen:
            seen.add(key)
            out.append(it)
    return out


def _subject_keywords(subject: str) -> list:
    text = subject or ""
    phrases, words = [], []
    for run in _runs(text):
        if len(run) >= 2:
            phrases.append(text[run[0][0]:run[-1][1]])
        for s, e in run:
            if e - s >= 3:
                words.append(text[s:e])
    return _dedup(phrases + words)[:MAX_PER_GROUP]


def _body_keywords(body: str) -> list:
    counts, first = {}, {}
    order = 0
    for line in (body or "").splitlines():
        line = re.sub(r"[\s\xa0]+", " ", line).strip()
        for run in _runs(line):
            for n in (1, 2, 3, 4):
                for i in range(len(run) - n + 1):
                    s, e = run[i][0], run[i + n - 1][1]
                    if n == 1 and e - s < 3:
                        continue
                    key = line[s:e]
                    counts[key] = counts.get(key, 0) + 1
                    if key not in first:
                        first[key] = order
                        order += 1
    # drop a phrase when a longer phrase containing it is just as frequent
    cands = [k for k in counts
             if not any(k != o and k in o and counts[o] >= counts[k] for o in counts)]
    cands.sort(key=lambda k: (-(counts[k] * 2 + len(k.split())), first[k]))
    norm = re.sub(r"[\s\xa0]+", " ", body or "")
    cues = [c for c in _CUES if c in norm]
    # a cue already covered by a longer picked cue adds nothing
    cues = [c for c in cues if not any(c != o and c in o for o in cues)]
    cands = [k for k in cands if not any(c in k or k in c for c in cues)]
    return _dedup(cues + cands)[:MAX_PER_GROUP]


def _sender_keywords(sender: str) -> list:
    m = re.search(r"[\w.+-]+@[\w.-]+", sender or "")
    address = m.group(0).lower() if m else ""
    out = [naming.registered_domain(sender), address]
    if re.match(r'^\s*"?[^"<\n]+"?\s*<', sender or ""):
        out.append(naming.display_name(sender))
    return _dedup([o for o in out if o])


def suggest(sender: str, subject: str, body: str = "") -> dict:
    return {"sender": _sender_keywords(sender),
            "subject": _subject_keywords(subject),
            "body": _body_keywords(body)}
