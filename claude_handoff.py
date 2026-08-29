"""
claude_handoff.py
-----------------
Open a new terminal running the `claude` CLI, pre-seeded with a prompt that
points at specific unresolved fallback entries, so the user can finish
classifying them together with Claude.
"""

import subprocess
from pathlib import Path

SCRIPT_DIR = Path(r"C:\Users\ofeks\Scripts\ReceiptSaver")

# Appended to every seeded prompt: the initial hand-off must never make the
# session start editing on its own. Investigate and propose only; wait for the
# user's explicit go-ahead before touching any code, file, or setting.
NO_AUTO_CHANGES = (
    " IMPORTANT: do not make any changes yet - no edits to code, files, config, "
    "or anything else in this repo or on the machine. Only investigate and "
    "explain, then propose what you would do and wait for my explicit "
    "instruction before changing anything."
)


def build_prompt(entries: list) -> str:
    bits = []
    for e in entries:
        sender  = str(e.get("sender", "")).replace('"', "'")
        subject = str(e.get("subject", "")).replace('"', "'")
        bits.append(f'[{e.get("account", "?")}] {sender} / {subject}'
                    f' (folder: {e.get("folder_path", "")})')
    listing = "; ".join(bits)
    return ("handle my fallback emails — focus on these unresolved entries "
            f"from fallback_log.json: {listing}.{NO_AUTO_CHANGES}"
            ).replace("\n", " ").replace('"', "'")


def launch(entries: list) -> None:
    prompt = build_prompt(entries)
    # `start` needs a title arg first; keep everything one line.
    subprocess.Popen(
        ["cmd", "/c", "start", "Claude - fallbacks", "cmd", "/k", "claude", prompt],
        cwd=str(SCRIPT_DIR),
    )


def build_error_prompt(message: str) -> str:
    """A one-line debugging prompt seeded with an error the UI surfaced."""
    msg = " ".join(str(message or "").split()).replace('"', "'")
    if len(msg) > 800:
        msg = msg[:800] + " ..."
    return ("help me debug an error from the Receipt Saver app in this repo. "
            "Look at the relevant code and receipt_saver.log, then explain the "
            f"cause and suggest a fix. The error surfaced in the UI was: {msg}"
            f".{NO_AUTO_CHANGES}")


def launch_error(message: str) -> None:
    """Open a `claude` terminal in the repo, pre-seeded to debug `message`."""
    prompt = build_error_prompt(message)
    subprocess.Popen(
        ["cmd", "/c", "start", "Claude - error", "cmd", "/k", "claude", prompt],
        cwd=str(SCRIPT_DIR),
    )


def _clean(v) -> str:
    """Collapse whitespace and neutralise double quotes (they would break the
    single-token `cmd /k claude "<prompt>"` invocation)."""
    return " ".join(str(v or "").split()).replace('"', "'")


def build_receipt_prompt(entry: dict) -> str:
    """One-line prompt seeded with a single Receipts-explorer entry the user
    asked Claude to help with (right-click an entry -> Ask Claude)."""
    title = _clean(entry.get("title") or entry.get("name")) or "(unnamed)"
    bits = [b for b in (_clean(entry.get("account")), _clean(entry.get("date"))) if b]
    meta = f" ({', '.join(bits)})" if bits else ""
    path = _clean(entry.get("path"))
    tail = f" It's located at {path}." if path else ""
    return ("I want your help handling this item from my Receipt Saver archive: "
            f"'{title}'{meta}.{tail} Take a look at it and at the repo, then help "
            f"me with whatever I need for this item.{NO_AUTO_CHANGES}")


def launch_receipt(entry: dict) -> None:
    """Open a `claude` terminal in the repo, pre-seeded to help with `entry`."""
    prompt = build_receipt_prompt(entry)
    subprocess.Popen(
        ["cmd", "/c", "start", "Claude - receipt", "cmd", "/k", "claude", prompt],
        cwd=str(SCRIPT_DIR),
    )
