"""Tray icon (Windows / Linux) — ``pystray`` + ``pillow``."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

from .paths import log_path

log = logging.getLogger(__name__)


def bundled_icon_path() -> Path | None:
    roots = [getattr(sys, "_MEIPASS", None), str(Path(__file__).resolve().parent.parent / "packaging" / "windows")]
    for root in roots:
        if root and (Path(root) / "switcher.ico").exists():
            return Path(root) / "switcher.ico"
    return None


def icon_image(size: int = 64):
    from PIL import Image, ImageDraw, ImageFont

    path = bundled_icon_path()
    if path is not None:
        try:
            return Image.open(path)
        except OSError:
            pass
    return draw_icon(size)


def draw_icon(size: int = 256):
    """The app icon: a blue rounded square with "A" and "Я"."""
    from PIL import Image, ImageDraw, ImageFont

    scale = size / 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((2 * scale, 2 * scale, 62 * scale, 62 * scale), radius=14 * scale,
                           fill=(37, 99, 235, 255))
    font = None
    for name in ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            font = ImageFont.truetype(name, int(26 * scale))
            break
        except OSError:
            continue
    font = font or ImageFont.load_default()
    draw.text((10 * scale, 8 * scale), "A", font=font, fill=(255, 255, 255, 255))
    draw.text((32 * scale, 28 * scale), "Я", font=font, fill=(191, 219, 254, 255))
    return image


def open_folder(path) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def self_check(app) -> None:
    """Write the self-check to a file, copy it for pasting into a chat, and show it."""
    from .paths import data_dir

    report = app.diagnostics()
    log.info("self-check:\n%s", report)
    path = data_dir() / "проверка.txt"
    path.write_text(report + "\n", encoding="utf-8")
    try:
        import pyperclip

        pyperclip.copy(report)
        app.backend.notify("Проверка готова и скопирована — можно вставить её в чат (Ctrl+V).")
    except Exception:
        log.debug("could not copy the self-check", exc_info=True)
    open_folder(path)


class Tray:
    """Tray icon for the desktop (GUI) mode; menu actions go through the Ui's queue."""

    def __init__(self, app, ui):
        self.app = app
        self.ui = ui
        self.icon = None

    def start(self) -> bool:
        try:
            import pystray
        except Exception:
            log.warning("pystray is unavailable: no tray icon", exc_info=True)
            return False
        app, ui = self.app, self.ui

        def toggle(icon, item):
            app.post(lambda: app.controller.run_hotkey("toggle"))

        def learn(icon, item):
            threading.Thread(target=app.review_and_notify, daemon=True).start()

        def quit_(icon, item):
            app.stop_event.set()
            ui.call(ui.quit)

        def updates_text(item):
            newest = next((r for r in app.releases or [] if r.relation == "newer" and not r.prerelease), None)
            return f"Обновить до версии {newest.version}…" if newest else "Обновления…"

        menu = pystray.Menu(
            pystray.MenuItem("Настройки…", lambda icon, item: ui.call(ui.open_settings), default=True),
            pystray.MenuItem("Автопереключение", toggle, checked=lambda item: app.controller.enabled),
            pystray.MenuItem("Что Switcher знает обо мне…",
                             lambda icon, item: ui.call(lambda: ui.open_settings(tab="stats"))),
            pystray.MenuItem("Разобрать мои исправления (Claude)", learn, enabled=lambda item: app.ai_ready()),
            pystray.MenuItem(updates_text, lambda icon, item: ui.call(lambda: ui.open_settings(tab="updates"))),
            pystray.MenuItem("Проверить, всё ли работает", lambda icon, item: self_check(app)),
            pystray.MenuItem("Журнал работы", lambda icon, item: open_folder(log_path())),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Выход", quit_),
        )
        from . import __version__

        self.icon = pystray.Icon("switcher", icon_image(), f"Switcher {__version__} — умный переключатель раскладки",
                                 menu)
        app.release_listeners.append(lambda releases: self.icon.update_menu())
        app.backend.notifier = lambda message: self.icon.notify(message, "Switcher")
        threading.Thread(target=self.icon.run, name="switcher-tray", daemon=True).start()
        return True

    def stop(self) -> None:
        if self.icon is not None:
            self.app.backend.notifier = None
            try:
                self.icon.stop()
            except Exception:
                log.debug("tray stop failed", exc_info=True)


def run_tray(app) -> bool:
    """Console mode (``switcher run``): block in the tray loop; False if there is no tray."""
    if sys.platform == "darwin":
        return False  # the main thread belongs to the input-source loop on macOS
    try:
        import pystray
        from PIL import Image  # noqa: F401
    except Exception:
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
        pystray.MenuItem("Обучить сейчас (Claude)", learn, enabled=lambda item: app.ai_ready()),
        pystray.MenuItem("Папка с профилем", lambda icon, item: open_folder(data_dir())),
        pystray.MenuItem("Выход", quit_),
    )
    icon = pystray.Icon("switcher", icon_image(), "Switcher", menu)
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
