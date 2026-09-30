"""Keyboard/mouse hooks and text injection shared by all platforms (via pynput).

Subclasses only answer OS questions: which layout is active, how to switch
it, which app is in front.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable

from ..controller import KeyEvent
from ..layouts import Keyboard

log = logging.getLogger(__name__)

_MODIFIER_NAMES = {
    "shift": "shift", "shift_l": "shift", "shift_r": "shift",
    "ctrl": "ctrl", "ctrl_l": "ctrl", "ctrl_r": "ctrl",
    "alt": "alt", "alt_l": "alt", "alt_r": "alt", "alt_gr": "alt",
    "cmd": "cmd", "cmd_l": "cmd", "cmd_r": "cmd",
}


class BaseBackend:
    #: OS virtual key code → physical key (US QWERTY unshifted char), when the
    #: OS reports layout-independent codes.
    VK_CODES: dict[int, str] = {}
    #: extra time to ignore echoes of our own input (X11 does not flag them)
    ECHO_GRACE = 0.0
    COPY_SHORTCUT = ("ctrl", "c")
    PASTE_SHORTCUT = ("ctrl", "v")

    def __init__(self, keyboard: Keyboard, typing_delay_ms: float = 2.0):
        from pynput import keyboard as pkeyboard

        self.keyboard = keyboard
        self.delay = typing_delay_ms / 1000.0
        self._pk = pkeyboard
        self._out = pkeyboard.Controller()
        self._sink: Callable[[KeyEvent], None] | None = None
        self._listeners: list = []
        self._shift = False
        self._lock = threading.RLock()
        self._busy_until = 0.0
        self._restore_clipboard: str | None = None
        self.notifier: Callable[[str], None] | None = None
        # Automated end-to-end checks type with SendInput, which Windows flags as
        # injected; this makes the backend treat such input as the user's.
        self.accept_injected = os.environ.get("SWITCHER_ACCEPT_INJECTED") == "1"
        if self.accept_injected:
            self.ECHO_GRACE = max(self.ECHO_GRACE, 0.3)

    # -- OS hooks (override) -------------------------------------------------

    def current_layout(self) -> str | None:
        return None

    def set_layout(self, lang: str) -> bool:
        return False

    def active_app(self) -> str:
        return ""

    def caps_lock_on(self) -> bool | None:
        return None

    def detect_ru_variant(self) -> str | None:
        return None

    def main_loop(self, stop: threading.Event) -> None:
        """Runs on the main thread until ``stop`` is set."""
        while not stop.wait(0.5):
            pass

    def permissions_hint(self) -> str:
        return ""

    # -- listening -----------------------------------------------------------

    def start(self, sink: Callable[[KeyEvent], None]) -> None:
        from pynput import mouse

        self._sink = sink
        kl = self._pk.Listener(on_press=self._on_press, on_release=self._on_release, **self._listener_options())
        ml = mouse.Listener(on_click=self._on_click)
        for listener in (kl, ml):
            listener.daemon = True
            listener.start()
        self._listeners = [kl, ml]

    def _listener_options(self) -> dict:
        """Platform-specific pynput listener options (e.g. an event filter)."""
        return {}

    def stop(self) -> None:
        for listener in self._listeners:
            listener.stop()
        self._listeners = []

    def _describe(self, key) -> tuple[str, str | None, str | None]:
        """(name, char, physical code) for a pynput key."""
        if isinstance(key, self._pk.Key):
            name = _MODIFIER_NAMES.get(key.name, key.name)
            return name, None, None
        vk = getattr(key, "vk", None)
        code = self.VK_CODES.get(vk) if vk is not None else None
        char = key.char
        if char is not None and len(char) != 1:
            char = None
        if code is None and char is not None:
            found = self.keyboard.stroke_for_char(char, self.current_layout())
            code = found[0].code if found else None
        if char == " ":
            return "space", None, None
        return "char", char, code

    def _is_echo(self) -> bool:
        """Our own injected input coming back (for OSes that do not flag it)."""
        with self._lock:
            return time.monotonic() < self._busy_until

    def _emit(self, kind: str, name: str, char: str | None, code: str | None) -> None:
        if name == "shift":
            self._shift = kind == "press"
        shift = self._shift
        if char and char.isalpha():
            shift = char.isupper()
        ev = KeyEvent(kind, name, char=char, code=code, shift=shift, layout=self.current_layout(),
                      app=self.active_app(), time=time.monotonic())
        if self._sink:
            self._sink(ev)

    def _on_press(self, key, injected: bool = False) -> None:
        try:
            if (injected and not self.accept_injected) or self._is_echo():
                return
            name, char, code = self._describe(key)
            self._emit("press", name, char, code)
        except Exception:  # never let an exception kill the OS hook
            log.exception("press handler failed")

    def _on_release(self, key, injected: bool = False) -> None:
        try:
            if injected and not self.accept_injected:
                return
            # Modifier releases always pass: a lost release would leave Ctrl "held" forever.
            if isinstance(key, self._pk.Key) and _MODIFIER_NAMES.get(key.name):
                self._emit("release", _MODIFIER_NAMES[key.name], None, None)
        except Exception:
            log.exception("release handler failed")

    def _on_click(self, x, y, button, pressed, injected: bool = False) -> None:
        if pressed and not injected and self._sink:
            self._sink(KeyEvent("press", "mouse", app=self.active_app(), time=time.monotonic()))

    # -- acting --------------------------------------------------------------

    def _busy(self) -> None:
        with self._lock:
            self._busy_until = time.monotonic() + 30

    def _settle(self) -> None:
        with self._lock:
            self._busy_until = time.monotonic() + self.ECHO_GRACE

    def backspace(self, count: int) -> None:
        self._busy()
        try:
            for _ in range(count):
                self._out.press(self._pk.Key.backspace)
                self._out.release(self._pk.Key.backspace)
                if self.delay:
                    time.sleep(self.delay)
        finally:
            self._settle()

    def type_text(self, text: str) -> None:
        self._busy()
        try:
            for ch in text:
                if ch == "\n":
                    self._out.press(self._pk.Key.enter)
                    self._out.release(self._pk.Key.enter)
                elif ch == "\t":
                    self._out.press(self._pk.Key.tab)
                    self._out.release(self._pk.Key.tab)
                else:
                    self._out.type(ch)
                if self.delay:
                    time.sleep(self.delay)
        finally:
            self._settle()

    def caps_lock_off(self) -> None:
        if self.caps_lock_on() is False:
            return
        self._busy()
        try:
            self._out.press(self._pk.Key.caps_lock)
            self._out.release(self._pk.Key.caps_lock)
        finally:
            self._settle()

    def _shortcut(self, combo: tuple[str, str]) -> None:
        mod = getattr(self._pk.Key, combo[0])
        self._busy()
        try:
            with self._out.pressed(mod):
                self._out.press(combo[1])
                self._out.release(combo[1])
        finally:
            self._settle()

    def copy_selection(self) -> str | None:
        try:
            import pyperclip
        except ImportError:
            self.notify("Для работы с выделением установите pyperclip: pip install pyperclip")
            return None
        try:
            saved = pyperclip.paste()
        except pyperclip.PyperclipException:
            saved = None
        marker = f"switcher-clipboard-probe-{time.time()}"
        try:
            pyperclip.copy(marker)
            self._shortcut(self.COPY_SHORTCUT)
            deadline = time.monotonic() + 0.6
            text = marker
            while time.monotonic() < deadline:
                time.sleep(0.03)
                text = pyperclip.paste()
                if text != marker:
                    break
        finally:
            self._restore_clipboard = saved
        return None if text == marker else text

    def restore_clipboard(self) -> None:
        """Put back what the clipboard held before copy_selection, when nothing gets pasted."""
        saved, self._restore_clipboard = self._restore_clipboard, None
        if saved is None:
            return
        try:
            import pyperclip

            pyperclip.copy(saved)
        except Exception:
            log.debug("clipboard restore failed", exc_info=True)

    def paste_text(self, text: str) -> None:
        try:
            import pyperclip
        except ImportError:
            return
        saved = self._restore_clipboard
        pyperclip.copy(text)
        self._shortcut(self.PASTE_SHORTCUT)
        time.sleep(0.25)
        if saved is not None:
            pyperclip.copy(saved)
            self._restore_clipboard = None

    def notify(self, message: str) -> None:
        log.info("%s", message)
        if self.notifier:
            try:
                self.notifier(message)
            except Exception:
                log.debug("notifier failed", exc_info=True)
