"""
make_shortcut.py
----------------
One-off: drop a "Receipt Saver" shortcut into the Start Menu that launches
run.bat. Run manually once:  python make_shortcut.py
"""

import os
from pathlib import Path

RUN_BAT = Path(__file__).with_name("run.bat")
START_MENU = Path(os.environ["APPDATA"]) / r"Microsoft\Windows\Start Menu\Programs"
LNK = START_MENU / "Receipt Saver.lnk"


def main():
    import win32com.client  # from pywin32; install if missing
    shell = win32com.client.Dispatch("WScript.Shell")
    sc = shell.CreateShortcut(str(LNK))
    sc.TargetPath = str(RUN_BAT)
    sc.WorkingDirectory = str(RUN_BAT.parent)
    sc.IconLocation = "shell32.dll,297"
    sc.Save()
    print(f"Created {LNK}")


if __name__ == "__main__":
    main()
