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


def build_prompt(entries: list) -> str:
    bits = []
    for e in entries:
        sender  = str(e.get("sender", "")).replace('"', "'")
        subject = str(e.get("subject", "")).replace('"', "'")
        bits.append(f'[{e.get("account", "?")}] {sender} / {subject}'
                    f' (folder: {e.get("folder_path", "")})')
    listing = "; ".join(bits)
    return ("handle my fallback emails — focus on these unresolved entries "
            f"from fallback_log.json: {listing}").replace("\n", " ").replace('"', "'")


def launch(entries: list) -> None:
    prompt = build_prompt(entries)
    # `start` needs a title arg first; keep everything one line.
    subprocess.Popen(
        ["cmd", "/c", "start", "Claude - fallbacks", "cmd", "/k", "claude", prompt],
        cwd=str(SCRIPT_DIR),
    )
