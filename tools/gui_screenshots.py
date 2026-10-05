"""Screenshots of every settings page, light and dark (for reviewing the design, e.g. on CI).

    python tools/gui_screenshots.py OUT_DIR [--preview]

--preview also prints a small JPEG contact sheet as base64, for reviewers who can
read the CI log but cannot download artifacts.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGES = ["main", "keys", "snippets", "ahk", "ahk_keys", "ai", "rules", "stats", "updates", "editor"]
SAMPLE_SCRIPT = """#Requires AutoHotkey v2.0
#SingleInstance Force

; Ctrl+Alt+D — сегодняшняя дата
^!d::SendText FormatTime(, "dd.MM.yyyy")

; Win+N — Блокнот
#n::Run "notepad.exe"

; Ctrl+Alt+S — приостановить скрипт (это сочетание и у Switcher)
^!s::Suspend

::@@::me@example.com   ; адрес по "@@"
::мб::может быть

/* Caps Lock — переключить раскладку */
CapsLock::Send "{Alt down}{Shift}{Alt up}"
"""


def shoot(mode: str, out: Path) -> None:
    import customtkinter as ctk
    from PIL import ImageGrab

    from switcher import ahk, autostart, gui, updater
    from switcher.config import Config
    from switcher.layouts import DEFAULT_KEYBOARD
    from switcher.profile import Profile

    autostart.is_enabled = lambda: True
    major, minor, *_ = updater.current_version() + (0, 0)

    def sample(version, notes, date):  # made-up versions around the running one, for the layout only
        return updater.Release(version=version, key=updater.parse_version(version), title=version, notes=notes,
                               date=date, url="", asset_url="", asset_name="", size=31_000_000)

    class App:
        config = Config(snippets={"015": "015-510-400_4_", "525": "525-459_4_", "745": "745-605_4_"})
        profile = Profile(":memory:")
        keyboard = DEFAULT_KEYBOARD
        stop_event = threading.Event()
        release_listeners: list = []
        releases = [
            sample(f"{major}.{minor + 1}.0", ["Пример: новая функция", "Пример: исправленная ошибка"], "2026-10-12"),
            sample(updater.__version__, ["Пример: то, что вошло в эту версию"], "2026-09-29"),
            sample(f"{major}.{max(minor - 1, 0)}.9", ["Пример: прежняя версия"], "2026-09-01"),
        ]

        def check_updates(self, force=False):
            return self.releases

        def update_config(self, new, save=True):
            pass

        def ai_ready(self):
            return False

    folder = Path(tempfile.mkdtemp()) / "AutoHotkey"
    folder.mkdir()
    scripts = {}
    for name, auto in (("Мои клавиши", True), ("Буфер обмена", False), ("Игры", False)):
        (folder / f"{name}.ahk").write_text(SAMPLE_SCRIPT, encoding="utf-8-sig")
        scripts[str(folder / f"{name}.ahk")] = auto
    App.config.ahk_scripts = scripts

    class Scripts(ahk.System):  # two of them running; AutoHotkey v2 "installed"
        supported = True

        def running(self):
            return {path: 1 for path in list(scripts)[:2]}

        def install_roots(self):
            return [r"C:\Program Files\AutoHotkey"]

    App.ahk = ahk.AhkManager(App.config, system=Scripts())
    App.ahk._interpreters = [ahk.Interpreter(r"C:\Program Files\AutoHotkey\v2\AutoHotkey64.exe", 2)]
    App.profile.add_rule("layout", "ghbdtn", "ru", source="learned")
    App.profile.add_rule("layout", "kubernetes", "en", source="user")
    App.profile.add_rule("replace", "превет", "привет", source="ai")
    root = gui.make_root()
    ctk.set_appearance_mode(mode)
    ui = gui.Ui(App(), root=root)
    ui.open_settings(welcome=mode == "light")
    window = ui.window
    window.geometry("+0+0")

    shown = {"window": window}

    def step(i: int = 0) -> None:
        if i:
            target = shown["window"]
            target.update()
            x, y = target.winfo_rootx(), target.winfo_rooty()
            ImageGrab.grab(bbox=(x, y, x + target.winfo_width(), y + target.winfo_height())).save(
                out / f"{mode}-{PAGES[i - 1]}.png")
        if i == len(PAGES):
            root.destroy()
            return
        if PAGES[i] == "editor":
            editor = ui.open_script(next(iter(scripts)))
            editor.geometry("+0+0")
            shown["window"] = editor
            root.after(1200, lambda: step(i + 1))
            return
        window.show(PAGES[i])
        if PAGES[i] == "keys":
            window.start_recording("toggle")
        root.after(800, lambda: step(i + 1))

    root.after(1000, step)
    root.mainloop()


def preview(out: Path, names: list[str]) -> None:
    import base64
    import io

    from PIL import Image

    images = [Image.open(out / f"{name}.png").convert("RGB") for name in names]
    width, height = max(i.width for i in images), max(i.height for i in images)
    sheet = Image.new("RGB", (width * 2, height * ((len(images) + 1) // 2)), "white")
    for n, image in enumerate(images):
        sheet.paste(image, ((n % 2) * width, (n // 2) * height))
    sheet.thumbnail((1200, 1000))
    buf = io.BytesIO()
    sheet.save(buf, "JPEG", quality=60)
    data = base64.b64encode(buf.getvalue()).decode()
    print("-----BEGIN PREVIEW JPEG-----")
    print("\n".join(data[i:i + 1000] for i in range(0, len(data), 1000)))
    print("-----END PREVIEW JPEG-----")


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--preview"]
    out = Path(args[0] if args else "screenshots").resolve()
    out.mkdir(parents=True, exist_ok=True)
    if len(args) > 1:  # child process: one appearance mode per interpreter
        sys.path.insert(0, str(ROOT))
        shoot(args[1], out)
        return
    env = dict(os.environ, SWITCHER_HOME=tempfile.mkdtemp())
    for mode in ("light", "dark"):
        subprocess.run([sys.executable, __file__, str(out), mode], env=env, check=True, timeout=120)
    print("\n".join(sorted(str(p) for p in out.glob("*.png"))))
    if "--preview" in sys.argv:
        preview(out, ["light-main", "dark-keys"])


if __name__ == "__main__":
    main()
