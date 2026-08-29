"""
install_startup.py
------------------
Make the Receipt Saver window open automatically at logon.

Registers a Task Scheduler job (``ReceiptSaverUI``, trigger *At log on* for the
current user, run only when logged on, 15 s delay) rather than dropping a
Startup-folder shortcut: the task fires after the desktop has settled, always in
the interactive session, and isn't shown on — or silenceable from — Task
Manager's Startup tab. It registers under the current user, so **no admin
rights** are needed (this is why it uses PowerShell's ``Register-ScheduledTask``
and not ``schtasks /create``, which wants elevation on some machines).

Run once:  python install_startup.py
           python install_startup.py --uninstall

Idempotent: also clears any leftover Startup-folder launcher (`run.bat`,
`Receipt Saver.lnk`) so the window can't open twice.
"""

import os
import subprocess
import sys
from pathlib import Path

HERE    = Path(__file__).parent
APP_PY  = HERE / "app.py"
TASK    = "ReceiptSaverUI"

PYTHONW = Path(sys.executable).with_name("pythonw.exe")
if not PYTHONW.exists():
    PYTHONW = Path(sys.executable)

STARTUP = Path(os.environ["APPDATA"]) / r"Microsoft\Windows\Start Menu\Programs\Startup"


def _ps(script: str) -> None:
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                       capture_output=True, text=True)
    if r.stdout.strip():
        print(r.stdout.strip())
    if r.returncode != 0:
        raise SystemExit((r.stderr or "PowerShell failed").strip())


def _clear_startup_folder():
    for stale in ("run.bat", "receipt_saver.bat", "Receipt Saver.lnk"):
        p = STARTUP / stale
        if p.exists():
            p.unlink()
            print(f"Removed stale {p}")


def install():
    _clear_startup_folder()
    _ps(f"""
$ErrorActionPreference = 'Stop'
$pw  = '{PYTHONW}'
$app = '{APP_PY}'
$dir = '{HERE}'
$action  = New-ScheduledTaskAction -Execute $pw -Argument ('"' + $app + '"') -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$trigger.Delay = 'PT15S'
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
             -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable
Register-ScheduledTask -TaskName '{TASK}' -Action $action -Trigger $trigger -Settings $settings `
    -Description 'Launch the Receipt Saver window at logon' -Force | Out-Null
'Registered scheduled task {TASK} - the window opens ~15s after logon.'
""")


def uninstall():
    _ps(f"Unregister-ScheduledTask -TaskName '{TASK}' -Confirm:$false; 'Removed {TASK}.'")


if __name__ == "__main__":
    (uninstall if "--uninstall" in sys.argv else install)()
