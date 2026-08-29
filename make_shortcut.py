"""
make_shortcut.py
----------------
Create clickable "Receipt Saver" shortcuts (Desktop + Start Menu) that launch
the app via pythonw.exe, using assets/receipt_saver.ico.

This is only about the manual launch icons. To make the window open at logon,
run  python install_startup.py  (registers a Task Scheduler job).

Run once:  python make_shortcut.py
  --startmenu-only   only the Start Menu shortcut
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


def main():
    _ensure_icon()
    _create(START_MENU / "Receipt Saver.lnk")
    if "--startmenu-only" not in sys.argv:
        _create(DESKTOP / "Receipt Saver.lnk")


if __name__ == "__main__":
    main()
