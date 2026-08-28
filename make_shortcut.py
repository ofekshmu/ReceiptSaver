"""
make_shortcut.py
----------------
Create clickable "Receipt Saver" shortcuts (Desktop + Start Menu + Startup) that
launch the app via pythonw.exe, using assets/receipt_saver.ico.

At login the Startup shortcut runs `pythonw app.py` directly — pythonw has no
console, and a .lnk (unlike a .bat) never flashes a cmd window. Any stale
`run.bat` left in the Startup folder by older installs is removed.

Run once:  python make_shortcut.py
  --startmenu-only   only the Start Menu shortcut
  --no-startup       skip the Startup (run-at-login) shortcut
"""

import os
import sys
import subprocess
from pathlib import Path

HERE     = Path(__file__).parent
APP_PY   = HERE / "app.py"
ICON     = HERE / "assets" / "receipt_saver.ico"

# Launch pythonw.exe directly (no console flash) rather than via run.bat.
PYTHONW  = Path(sys.executable).with_name("pythonw.exe")
if not PYTHONW.exists():
    PYTHONW = Path(sys.executable)

DESKTOP    = Path(os.environ["USERPROFILE"]) / "Desktop"
START_MENU = Path(os.environ["APPDATA"]) / r"Microsoft\Windows\Start Menu\Programs"
STARTUP    = START_MENU / "Startup"


def _ensure_icon():
    if not ICON.exists():
        subprocess.run([sys.executable, str(HERE / "make_icon.py")], check=True)


def _create(lnk: Path):
    lnk.parent.mkdir(parents=True, exist_ok=True)
    try:
        import win32com.client
        shell = win32com.client.Dispatch("WScript.Shell")
        sc = shell.CreateShortcut(str(lnk))
        sc.TargetPath = str(PYTHONW)
        sc.Arguments = f'"{APP_PY}"'
        sc.WorkingDirectory = str(HERE)
        sc.IconLocation = f"{ICON},0"
        sc.Description = "Receipt Saver"
        sc.Save()
    except ImportError:
        # Fallback: drive WScript.Shell through PowerShell (no pywin32 needed).
        ps = (
            f"$w=New-Object -ComObject WScript.Shell;"
            f"$s=$w.CreateShortcut('{lnk}');"
            f"$s.TargetPath='{PYTHONW}';"
            f"$s.Arguments='\"{APP_PY}\"';"
            f"$s.WorkingDirectory='{HERE}';"
            f"$s.IconLocation='{ICON},0';"
            f"$s.Description='Receipt Saver';"
            f"$s.Save()"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
    print(f"Created {lnk}")


def _clean_startup():
    """Remove stale launchers older installs dropped in the Startup folder.
    A .bat there makes cmd.exe pop up a console window at every login."""
    for stale in ("run.bat", "receipt_saver.bat"):
        p = STARTUP / stale
        if p.exists():
            p.unlink()
            print(f"Removed stale {p}")


def main():
    _ensure_icon()
    _create(START_MENU / "Receipt Saver.lnk")
    if "--startmenu-only" not in sys.argv:
        _create(DESKTOP / "Receipt Saver.lnk")
    if "--startmenu-only" not in sys.argv and "--no-startup" not in sys.argv:
        _clean_startup()
        _create(STARTUP / "Receipt Saver.lnk")


if __name__ == "__main__":
    main()
