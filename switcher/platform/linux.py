"""Linux (X11): layout groups via XKB (libX11 through ctypes).

Wayland sessions do not let applications read global keystrokes; run the
switcher in an X11 session (or an app running under XWayland only).
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os
import re
import subprocess
import threading
import time
from collections import deque

from ..layouts import EN, RU, letter_lang
from .base import BaseBackend

log = logging.getLogger(__name__)

XkbUseCoreKbd = 0x0100
LockMask = 1 << 1


class XkbStateRec(ctypes.Structure):
    _fields_ = [
        ("group", ctypes.c_ubyte), ("locked_group", ctypes.c_ubyte), ("base_group", ctypes.c_ushort),
        ("latched_group", ctypes.c_ushort), ("mods", ctypes.c_ubyte), ("base_mods", ctypes.c_ubyte),
        ("latched_mods", ctypes.c_ubyte), ("locked_mods", ctypes.c_ubyte), ("compat_state", ctypes.c_ubyte),
        ("grab_mods", ctypes.c_ubyte), ("compat_grab_mods", ctypes.c_ubyte), ("lookup_mods", ctypes.c_ubyte),
        ("compat_lookup_mods", ctypes.c_ubyte), ("ptr_buttons", ctypes.c_ushort),
    ]


def _load_x11():
    lib = ctypes.cdll.LoadLibrary(ctypes.util.find_library("X11") or "libX11.so.6")
    lib.XOpenDisplay.argtypes = (ctypes.c_char_p,)
    lib.XOpenDisplay.restype = ctypes.c_void_p
    lib.XkbGetState.argtypes = (ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(XkbStateRec))
    lib.XkbGetState.restype = ctypes.c_int
    lib.XkbLockGroup.argtypes = (ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint)
    lib.XkbLockGroup.restype = ctypes.c_int
    lib.XFlush.argtypes = (ctypes.c_void_p,)
    lib.XSync.argtypes = (ctypes.c_void_p, ctypes.c_int)
    return lib


def layout_groups(query_output: str) -> list[str | None]:
    """``setxkbmap -query`` output → language of each XKB group."""
    match = re.search(r"^layout:\s*(\S+)", query_output, re.M)
    if not match:
        return []
    langs: list[str | None] = []
    for name in match.group(1).split(","):
        name = name.strip().lower()
        langs.append(RU if name.startswith("ru") else EN if name in ("us", "gb", "uk", "au", "ca") else None)
    return langs


class LinuxBackend(BaseBackend):
    ECHO_GRACE = 0.1  # XTest input comes back unflagged; see BaseBackend._is_echo

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._echo: deque[tuple[str, str | None]] = deque()
        self._echo_deadline = 0.0
        self._x = _load_x11()
        self._display = self._x.XOpenDisplay(None)
        if not self._display:
            raise RuntimeError("Не удалось подключиться к X-серверу (DISPLAY не задан?)")
        self._xlock = threading.Lock()
        self._groups = self._read_groups()
        self._app_cache = (0.0, "")
        self._xlib_display = None
        self._xt = None
        if os.environ.get("WAYLAND_DISPLAY") and os.environ.get("XDG_SESSION_TYPE") == "wayland":
            log.warning("Сеанс Wayland: перехват клавиш работает только в X11-приложениях")

    def _read_groups(self) -> list[str | None]:
        try:
            out = subprocess.run(["setxkbmap", "-query"], capture_output=True, text=True, timeout=2).stdout
        except (OSError, subprocess.SubprocessError):
            out = ""
        groups = layout_groups(out)
        if RU not in groups:
            groups = self._probe_groups() or groups
        if RU not in groups:
            log.warning("В раскладках X11 нет русской: %s", out.strip() or "setxkbmap недоступен")
        return groups or [EN, RU]

    @staticmethod
    def _probe_groups() -> list[str | None]:
        """Read the languages of the XKB groups straight from the keymap (the "g"/"п" key)."""
        try:
            from Xlib import display as xdisplay

            d = xdisplay.Display()
            try:
                keycode = d.keysym_to_keycode(ord("g"))
                groups: list[str | None] = []
                for group in range(4):
                    keysym = d.keycode_to_keysym(keycode, group * 2)
                    if 0x6C0 <= keysym <= 0x6FF or 0x1000400 <= keysym <= 0x10004FF:
                        groups.append(RU)
                    elif 0x61 <= keysym <= 0x7A:
                        groups.append(EN)
                    else:
                        break
                return groups
            finally:
                d.close()
        except Exception:
            return []

    def _state(self) -> XkbStateRec:
        state = XkbStateRec()
        with self._xlock:
            self._x.XkbGetState(self._display, XkbUseCoreKbd, ctypes.byref(state))
        return state

    def current_layout(self) -> str | None:
        group = self._state().group
        return self._groups[group] if group < len(self._groups) else None

    def set_layout(self, lang: str) -> bool:
        if lang not in self._groups:
            self._groups = self._read_groups()
            if lang not in self._groups:
                self.notify(f"Раскладка {lang.upper()} не настроена в X11 (setxkbmap -layout us,ru)")
                return False
        with self._xlock:
            self._x.XkbLockGroup(self._display, XkbUseCoreKbd, self._groups.index(lang))
            self._x.XSync(self._display, 0)
        return True

    # Typing Unicode through pynput is unreliable on X11 (it "borrows" keycodes),
    # so type like a person: pick the layout group, press the physical keys.

    def _xtest(self):
        if self._xt is None:
            from Xlib import XK, display as xdisplay

            d = xdisplay.Display()
            column = self._groups.index(EN) * 2 if EN in self._groups else 0
            keycodes: dict[str, int] = {}
            for keycode in range(d.display.info.min_keycode, d.display.info.max_keycode + 1):
                keysym = d.keycode_to_keysym(keycode, column)
                if 0x20 < keysym < 0x7F:
                    keycodes.setdefault(chr(keysym), keycode)
            specials = {name: d.keysym_to_keycode(getattr(XK, f"XK_{name}"))
                        for name in ("space", "Return", "Tab", "Shift_L", "BackSpace")}
            self._xt = (d, keycodes, specials)
        return self._xt

    def _tap(self, keycode: int, shift: bool) -> None:
        from Xlib import X
        from Xlib.ext import xtest

        d, _, specials = self._xtest()
        if shift:
            xtest.fake_input(d, X.KeyPress, specials["Shift_L"])
        xtest.fake_input(d, X.KeyPress, keycode)
        xtest.fake_input(d, X.KeyRelease, keycode)
        if shift:
            xtest.fake_input(d, X.KeyRelease, specials["Shift_L"])
        d.sync()

    # -- telling our own keys from the user's ---------------------------------
    # XTest input comes back through the hook unflagged.  Instead of ignoring
    # every key for a while (which loses keys the user types meanwhile, e.g.
    # right after a switch in the middle of a word), we list the keys we are
    # about to press and drop exactly those, in order.

    def _expect(self, keys: list[tuple[str, str | None]]) -> None:
        with self._lock:
            self._echo.extend(keys)
            self._echo_deadline = time.monotonic() + 2.0

    def _consume_echo(self, ident: tuple[str, str | None]) -> bool:
        with self._lock:
            if self._echo and time.monotonic() > self._echo_deadline:
                self._echo.clear()  # some never came back; stop waiting for them
            if self._echo and self._echo[0] == ident:
                self._echo.popleft()
                return True
            return False

    def _on_press(self, key, injected: bool = False) -> None:
        try:
            name, char, code = self._describe(key)
            if self._consume_echo((name, code)):
                return
            if self._is_echo():  # caps lock, copy/paste shortcuts: short blanket window
                return
            self._emit("press", name, char, code)
        except Exception:  # never let an exception kill the OS hook
            log.exception("press handler failed")

    def backspace(self, count: int) -> None:
        _, _, specials = self._xtest()
        self._expect([("backspace", None)] * count)
        for _ in range(count):
            self._tap(specials["BackSpace"], False)
            if self.delay:
                time.sleep(self.delay)

    def type_text(self, text: str) -> None:
        d, keycodes, specials = self._xtest()
        start = current = self._state().group
        try:
            for ch in text:
                special = {" ": "space", "\n": "Return", "\t": "Tab"}.get(ch)
                if special:
                    self._expect([({"space": "space", "Return": "enter", "Tab": "tab"}[special], None)])
                    self._tap(specials[special], False)
                    continue
                here = self._groups[current] if current < len(self._groups) else EN
                order = [letter_lang(ch) or here]
                order += [lang for lang in (EN, RU) if lang not in order]
                for lang in order:
                    stroke = self.keyboard.layouts[lang].stroke(ch)
                    if stroke is not None and lang in self._groups and stroke.code in keycodes:
                        break
                else:
                    self._busy()  # a character no key types: pynput remaps a spare key
                    self._out.type(ch)
                    self._settle()
                    continue
                group = self._groups.index(lang)
                if group != current:
                    with self._xlock:
                        self._x.XkbLockGroup(self._display, XkbUseCoreKbd, group)
                        self._x.XSync(self._display, 0)
                    current = group
                self._expect(([("shift", None)] if stroke.shift else []) + [("char", stroke.code)])
                self._tap(keycodes[stroke.code], stroke.shift)
                if self.delay:
                    time.sleep(self.delay)
        finally:
            if current != start:
                with self._xlock:
                    self._x.XkbLockGroup(self._display, XkbUseCoreKbd, start)
                    self._x.XSync(self._display, 0)

    def caps_lock_on(self) -> bool | None:
        return bool(self._state().locked_mods & LockMask)

    def _describe(self, key):
        """pynput ignores the XKB group and reports group-1 symbols; recover the real char."""
        if isinstance(key, self._pk.Key):
            return super()._describe(key)
        char = key.char if key.char and len(key.char) == 1 else None
        if char is None:
            return "char", None, None
        if char == " ":
            return "space", None, None
        first = self._groups[0] if self._groups and self._groups[0] else EN
        stroke = self.keyboard.layouts[first].stroke(char)
        if stroke is None:
            return "char", char, None
        state = self._state()
        lang = self._groups[state.group] if state.group < len(self._groups) else None
        real = self.keyboard.layouts[lang or first].char(stroke)
        if state.locked_mods & LockMask and real.isalpha():
            real = real.swapcase()
        return "char", real, stroke.code

    def active_app(self) -> str:
        now = time.monotonic()
        stamp, name = self._app_cache
        if now - stamp < 0.3:
            return name
        name = ""
        try:
            from Xlib import X, display as xdisplay

            if self._xlib_display is None:
                self._xlib_display = xdisplay.Display()
            d = self._xlib_display
            root = d.screen().root
            prop = root.get_full_property(d.intern_atom("_NET_ACTIVE_WINDOW"), X.AnyPropertyType)
            if prop and prop.value and prop.value[0]:
                window = d.create_resource_object("window", prop.value[0])
                wm_class = window.get_wm_class()
                if wm_class:
                    name = wm_class[-1]
        except Exception:
            log.debug("active window lookup failed", exc_info=True)
        self._app_cache = (now, name)
        return name

    def detect_ru_variant(self) -> str | None:
        return "pc"
