"""
version.py
----------
Single source of truth for the app version shown in the UI (next to the
"Receipt Saver" wordmark). Bump ``__version__`` on a meaningful release.

``full_version()`` appends the short git commit (best-effort, cached) so the
window always tells you exactly which build is running.
"""

import subprocess
from pathlib import Path

__version__ = "1.1.0"

_HERE = Path(__file__).parent
_cache = None


def _git_short() -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(_HERE), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=2,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return ""


def full_version() -> str:
    """e.g. ``1.1.0 (a1b2c3d)`` — or just ``1.1.0`` if git is unavailable."""
    global _cache
    if _cache is None:
        sha = _git_short()
        _cache = f"{__version__} ({sha})" if sha else __version__
    return _cache
