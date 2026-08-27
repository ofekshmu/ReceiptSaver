"""
tray.py
-------
System-tray icon that keeps the process alive so the window can be reopened.
Menu: Open, Run scan now, Quit. Started on a daemon thread by app.py.
"""

import os
import threading

from PIL import Image, ImageDraw
import pystray


def _icon_image():
    img = Image.new("RGB", (64, 64), "#0f1115")
    d = ImageDraw.Draw(img)
    d.rectangle((14, 12, 50, 52), outline="#4f8cff", width=4)
    d.line((14, 24, 50, 24), fill="#4f8cff", width=3)
    return img


def run(api):
    def _open(icon, item):
        try:
            if api._window:
                api._window.show()
                api._window.restore()
        except Exception:
            pass

    def _scan(icon, item):
        threading.Thread(target=api.start_scan, daemon=True).start()

    def _quit(icon, item):
        icon.stop()
        os._exit(0)

    icon = pystray.Icon(
        "receipt_saver",
        _icon_image(),
        "Receipt Saver",
        menu=pystray.Menu(
            pystray.MenuItem("Open", _open, default=True),
            pystray.MenuItem("Run scan now", _scan),
            pystray.MenuItem("Quit", _quit),
        ),
    )
    icon.run()
