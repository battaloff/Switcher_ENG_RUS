"""End-to-end check of the real Linux backend on a virtual X server.

    xvfb-run -a -s "-screen 0 800x600x24" python tools/x11_smoke.py

A tiny X window plays the text field; synthetic key presses play the user;
the real App (pynput hooks + XKB layout switching) does the rest.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("SWITCHER_HOME", tempfile.mkdtemp(prefix="switcher-smoke-"))

from Xlib import X, XK, display as xdisplay  # noqa: E402

_SPECIAL = {"shorti": "SHORT I", "softsign": "SOFT SIGN", "hardsign": "HARD SIGN", "io": "IO"}


def keysym_to_char(keysym: int) -> str | None:
    if 0x20 <= keysym <= 0xFF:
        return chr(keysym)
    xname = _names.get(keysym)
    if xname and xname.startswith("Cyrillic_"):
        suffix = xname[len("Cyrillic_"):]
        case = "CAPITAL" if suffix[0].isupper() else "SMALL"
        letter = _SPECIAL.get(suffix.lower(), suffix.upper())
        try:
            return unicodedata.lookup(f"CYRILLIC {case} LETTER {letter}")
        except KeyError:
            return None
    if 0x01000000 <= keysym <= 0x0110FFFF:
        return chr(keysym - 0x01000000)
    return None


XK.load_keysym_group("cyrillic")
_names = {getattr(XK, n): n[3:] for n in dir(XK) if n.startswith("XK_")}


def char_to_keysym(ch: str) -> int:
    if ord(ch) <= 0xFF:
        return ord(ch)
    for keysym, name in _names.items():
        if name.startswith("Cyrillic_") and keysym_to_char(keysym) == ch:
            return keysym
    return 0x01000000 + ord(ch)


def install_us_ru_keymap(d) -> None:
    """Give every letter/punctuation key a second (Russian) group, like `setxkbmap us,ru`.

    Xvfb ignores uploaded XKB keymaps here, so use the core mapping: columns
    3-4 become XKB group 2.
    """
    from switcher.layouts import DEFAULT_KEYBOARD, KEY_CODES, Stroke

    en, ru = DEFAULT_KEYBOARD.layouts["en"], DEFAULT_KEYBOARD.layouts["ru"]
    for code in KEY_CODES:
        keycode = d.keysym_to_keycode(ord(code))
        row = [char_to_keysym(layout.char(Stroke(code, shift))) for layout in (en, ru) for shift in (False, True)]
        d.change_keyboard_mapping(keycode, [row])
    d.sync()


class TextField:
    def __init__(self):
        self.d = xdisplay.Display()
        screen = self.d.screen()
        self.win = screen.root.create_window(0, 0, 400, 100, 0, screen.root_depth,
                                             event_mask=X.KeyPressMask | X.FocusChangeMask)
        self.win.set_wm_class("smoke", "SmokeField")
        self.win.map()
        self.d.sync()
        self.win.set_input_focus(X.RevertToParent, X.CurrentTime)
        self.d.sync()
        self.text = ""
        self.lock = threading.Lock()

    def loop(self):
        while True:
            ev = self.d.next_event()
            if ev.type == X.MappingNotify:
                self.d.refresh_keyboard_mapping(ev)
                continue
            if ev.type != X.KeyPress:
                continue
            # XKB-unaware clients see group 2 as Mod5 (Mode_switch)
            group = ((ev.state >> 13) & 3) or (1 if ev.state & X.Mod5Mask else 0)
            shift = 1 if ev.state & X.ShiftMask else 0
            keysym = self.d.keycode_to_keysym(ev.detail, group * 2 + shift) or \
                self.d.keycode_to_keysym(ev.detail, shift)
            if keysym == XK.XK_BackSpace:
                with self.lock:
                    self.text = self.text[:-1]
                continue
            if keysym == XK.XK_Return:
                ch = "\n"
            else:
                ch = keysym_to_char(keysym)
            if ch:
                with self.lock:
                    self.text += ch


def main() -> int:
    field = TextField()
    install_us_ru_keymap(field.d)
    threading.Thread(target=field.loop, daemon=True).start()

    from pynput.keyboard import Controller, Key

    from switcher.app import App
    from switcher.config import Config
    from switcher.layouts import EN, RU

    from switcher.paths import profile_path

    for suffix in ("", "-wal", "-shm"):  # start from a blank profile
        Path(str(profile_path()) + suffix).unlink(missing_ok=True)
    config = Config()
    config.ai.enabled = False
    app = App(config)
    runner = threading.Thread(target=app.run, kwargs={"tray": False}, daemon=True)
    runner.start()
    time.sleep(1.0)
    user = Controller()

    def type_keys(physical: str):
        for ch in physical:
            if ch == " ":
                user.press(Key.space)
                user.release(Key.space)
            elif ch == "\b":
                user.press(Key.backspace)
                user.release(Key.backspace)
            else:
                # press the physical key; the active XKB group decides the letter
                keycode = field.d.keysym_to_keycode(ord(ch))
                from Xlib.ext import xtest

                xtest.fake_input(field.d, X.KeyPress, keycode)
                xtest.fake_input(field.d, X.KeyRelease, keycode)
                field.d.sync()
            time.sleep(0.08)
        time.sleep(0.6)

    results = []

    def check(label, expected):
        with field.lock:
            got = field.text
        ok = got == expected
        results.append(ok)
        print(f"{'OK ' if ok else 'FAIL'} {label}: {got!r} (ждали {expected!r}), раскладка {app.backend.current_layout()}")

    app.backend.set_layout(EN)
    time.sleep(0.2)
    type_keys("ghbdtn ")
    check("RU-слово на EN-раскладке", "привет ")
    type_keys("rfr ltkf ")
    check("дальше печатается уже по-русски", "привет как дела ")
    type_keys("\b" * 16)
    app.backend.set_layout(RU)
    time.sleep(0.2)
    type_keys("hello ")
    check("EN-слово на RU-раскладке", "hello ")
    # undo with double Shift
    type_keys("\b" * 6)
    app.backend.set_layout(EN)
    time.sleep(0.2)
    type_keys("ghbdtn ")
    for _ in range(2):
        user.press(Key.shift)
        user.release(Key.shift)
        time.sleep(0.05)
    time.sleep(0.6)
    check("отмена двойным Shift", "ghbdtn ")
    rule = app.profile.layout_rule("ghbdtn", app.backend.active_app())
    print(f"{'OK ' if rule else 'FAIL'} выучено правило: {rule}")
    results.append(bool(rule))

    app.stop_event.set()
    runner.join(timeout=3)
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
