"""Optional tray icon (Windows / Linux) — needs ``pip install pystray pillow``."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading

log = logging.getLogger(__name__)


def _icon_image():
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((2, 2, 62, 62), radius=14, fill=(37, 99, 235, 255))
    draw.text((12, 20), "Aa", fill=(255, 255, 255, 255))
    draw.text((36, 34), "Яя", fill=(255, 255, 255, 255))
    return image


def _open_folder(path) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(path)])


def run_tray(app) -> bool:
    """Blocks in the tray loop; returns False if the tray is unavailable."""
    if sys.platform == "darwin":
        return False  # the main thread belongs to the input-source loop on macOS
    try:
        import pystray
        from PIL import Image  # noqa: F401
    except ImportError:
        return False
    from .paths import data_dir

    def toggle(icon, item):
        app.post(lambda: app.controller.run_hotkey("toggle"))

    def learn(icon, item):
        threading.Thread(target=app.review_and_notify, daemon=True).start()

    def quit_(icon, item):
        app.stop_event.set()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Автопереключение", toggle, checked=lambda item: app.controller.enabled),
        pystray.MenuItem("Обучить сейчас (Claude)", learn, enabled=lambda item: app.assistant is not None),
        pystray.MenuItem("Папка с профилем", lambda icon, item: _open_folder(data_dir())),
        pystray.MenuItem("Выход", quit_),
    )
    icon = pystray.Icon("switcher", _icon_image(), "Switcher", menu)
    app.backend.notifier = lambda message: icon.notify(message, "Switcher")

    def watch_stop(icon):
        icon.visible = True
        app.stop_event.wait()
        icon.stop()

    try:
        icon.run(setup=lambda icon: threading.Thread(target=watch_stop, args=(icon,), daemon=True).start())
    except Exception:
        log.exception("tray failed")
        app.backend.notifier = None
        return False
    return True
