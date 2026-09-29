"""Screenshots of every settings page, light and dark (for reviewing the design, e.g. on CI).

    python tools/gui_screenshots.py OUT_DIR
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGES = ["main", "keys", "ai", "rules", "stats"]


def shoot(mode: str, out: Path) -> None:
    import customtkinter as ctk
    from PIL import ImageGrab

    from switcher import autostart, gui
    from switcher.config import Config
    from switcher.layouts import DEFAULT_KEYBOARD
    from switcher.profile import Profile

    autostart.is_enabled = lambda: True

    class App:
        config = Config()
        profile = Profile(":memory:")
        keyboard = DEFAULT_KEYBOARD
        stop_event = threading.Event()

        def update_config(self, new, save=True):
            pass

        def ai_ready(self):
            return False

    App.profile.add_rule("layout", "ghbdtn", "ru", source="learned")
    App.profile.add_rule("layout", "kubernetes", "en", source="user")
    App.profile.add_rule("replace", "превет", "привет", source="ai")
    root = gui.make_root()
    ctk.set_appearance_mode(mode)
    ui = gui.Ui(App(), root=root)
    ui.open_settings(welcome=mode == "light")
    window = ui.window
    window.geometry("+0+0")

    def step(i: int = 0) -> None:
        if i:
            window.update()
            x, y = window.winfo_rootx(), window.winfo_rooty()
            ImageGrab.grab(bbox=(x, y, x + window.winfo_width(), y + window.winfo_height())).save(
                out / f"{mode}-{PAGES[i - 1]}.png")
        if i == len(PAGES):
            root.destroy()
            return
        window.show(PAGES[i])
        if PAGES[i] == "keys":
            window.start_recording("toggle")
        root.after(800, lambda: step(i + 1))

    root.after(1000, step)
    root.mainloop()


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "screenshots").resolve()
    out.mkdir(parents=True, exist_ok=True)
    if len(sys.argv) > 2:  # child process: one appearance mode per interpreter
        sys.path.insert(0, str(ROOT))
        shoot(sys.argv[2], out)
        return
    env = dict(os.environ, SWITCHER_HOME=tempfile.mkdtemp())
    for mode in ("light", "dark"):
        subprocess.run([sys.executable, __file__, str(out), mode], env=env, check=True, timeout=120)
    print("\n".join(sorted(str(p) for p in out.glob("*.png"))))


if __name__ == "__main__":
    main()
