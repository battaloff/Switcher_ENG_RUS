"""macOS: input sources via Carbon's Text Input Sources API (ctypes).

TIS calls must happen on the main thread, so the main thread runs
:meth:`MacBackend.main_loop`: it refreshes the cached layout / front app and
performs queued layout switches.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import queue
import subprocess
import threading
import time

from ..layouts import EN, RU
from .base import BaseBackend

log = logging.getLogger(__name__)

_carbon = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/Carbon.framework/Carbon")
_cf = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")

_carbon.TISCopyCurrentKeyboardInputSource.restype = ctypes.c_void_p
_carbon.TISCreateInputSourceList.argtypes = (ctypes.c_void_p, ctypes.c_bool)
_carbon.TISCreateInputSourceList.restype = ctypes.c_void_p
_carbon.TISGetInputSourceProperty.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
_carbon.TISGetInputSourceProperty.restype = ctypes.c_void_p
_carbon.TISSelectInputSource.argtypes = (ctypes.c_void_p,)
_carbon.TISSelectInputSource.restype = ctypes.c_int32
_cf.CFArrayGetCount.argtypes = (ctypes.c_void_p,)
_cf.CFArrayGetCount.restype = ctypes.c_long
_cf.CFArrayGetValueAtIndex.argtypes = (ctypes.c_void_p, ctypes.c_long)
_cf.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
_cf.CFStringGetCString.argtypes = (ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32)
_cf.CFStringGetCString.restype = ctypes.c_bool
_cf.CFRelease.argtypes = (ctypes.c_void_p,)

_kTISPropertyInputSourceID = ctypes.c_void_p.in_dll(_carbon, "kTISPropertyInputSourceID")
_UTF8 = 0x08000100

# kVK_ANSI_* hardware key codes → physical key
_VK = {
    0: "a", 1: "s", 2: "d", 3: "f", 4: "h", 5: "g", 6: "z", 7: "x", 8: "c", 9: "v", 11: "b", 12: "q",
    13: "w", 14: "e", 15: "r", 16: "y", 17: "t", 18: "1", 19: "2", 20: "3", 21: "4", 22: "6", 23: "5",
    24: "=", 25: "9", 26: "7", 27: "-", 28: "8", 29: "0", 30: "]", 31: "o", 32: "u", 33: "[", 34: "i",
    35: "p", 37: "l", 38: "j", 39: "'", 40: "k", 41: ";", 42: "\\", 43: ",", 44: "/", 45: "n", 46: "m",
    47: ".", 50: "`",
}


def _cfstring(ref) -> str:
    if not ref:
        return ""
    buf = ctypes.create_string_buffer(512)
    if _cf.CFStringGetCString(ref, buf, 512, _UTF8):
        return buf.value.decode("utf-8")
    return ""


def _source_id(source) -> str:
    return _cfstring(_carbon.TISGetInputSourceProperty(source, _kTISPropertyInputSourceID))


def classify(source_id: str) -> tuple[str | None, str | None]:
    """Input source id → (language, Russian variant)."""
    if "keylayout" not in source_id:
        return None, None
    name = source_id.rsplit(".", 1)[-1]
    if name.startswith("Russian"):
        if "Phonetic" in name:
            return None, None
        return RU, "pc" if name == "RussianWin" else "mac"
    if name.startswith(("US", "ABC", "British", "Australian", "Canadian", "Irish")) and "QWERTZ" not in name:
        return EN, None
    return None, None


class MacBackend(BaseBackend):
    VK_CODES = _VK
    COPY_SHORTCUT = ("cmd", "c")
    PASTE_SHORTCUT = ("cmd", "v")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._layout: str | None = None
        self._variant: str | None = None
        self._app = ""
        self._requests: queue.Queue[str] = queue.Queue()
        self._workspace = None
        try:
            from AppKit import NSWorkspace  # pyobjc, installed with pynput

            self._workspace = NSWorkspace.sharedWorkspace()
        except ImportError:
            log.warning("AppKit is unavailable: per-app learning is off")
        self._refresh()

    def _refresh(self) -> None:
        source = _carbon.TISCopyCurrentKeyboardInputSource()
        if source:
            lang, variant = classify(_source_id(source))
            _cf.CFRelease(source)
            self._layout = lang
            if variant:
                self._variant = variant
        if self._workspace is not None:
            app = self._workspace.frontmostApplication()
            self._app = str(app.localizedName()) if app is not None else ""

    def _select(self, lang: str) -> bool:
        sources = _carbon.TISCreateInputSourceList(None, False)
        if not sources:
            return False
        try:
            for i in range(_cf.CFArrayGetCount(sources)):
                source = _cf.CFArrayGetValueAtIndex(sources, i)
                if classify(_source_id(source))[0] == lang:
                    return _carbon.TISSelectInputSource(source) == 0
        finally:
            _cf.CFRelease(sources)
        return False

    def main_loop(self, stop: threading.Event) -> None:
        run_loop = None
        try:
            from Foundation import NSDate, NSRunLoop

            run_loop = NSRunLoop.currentRunLoop()
        except ImportError:
            pass
        while not stop.is_set():
            try:
                while True:
                    lang = self._requests.get_nowait()
                    if not self._select(lang):
                        self.notify(f"Раскладка {lang.upper()} не включена в настройках macOS")
            except queue.Empty:
                pass
            self._refresh()
            if run_loop is not None:
                run_loop.runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.04))
            else:
                time.sleep(0.04)

    def current_layout(self) -> str | None:
        return self._layout

    def set_layout(self, lang: str) -> bool:
        self._layout = lang
        self._requests.put(lang)
        return True

    def active_app(self) -> str:
        return self._app

    def detect_ru_variant(self) -> str | None:
        if self._variant:
            return self._variant
        sources = _carbon.TISCreateInputSourceList(None, False)
        if not sources:
            return None
        try:
            for i in range(_cf.CFArrayGetCount(sources)):
                lang, variant = classify(_source_id(_cf.CFArrayGetValueAtIndex(sources, i)))
                if lang == RU:
                    return variant
        finally:
            _cf.CFRelease(sources)
        return None

    def caps_lock_on(self) -> bool | None:
        try:
            import Quartz

            flags = Quartz.CGEventSourceFlagsState(Quartz.kCGEventSourceStateCombinedSessionState)
            return bool(flags & Quartz.kCGEventFlagMaskAlphaShift)
        except Exception:
            return None

    def notify(self, message: str) -> None:
        super().notify(message)
        if self.notifier is None:
            text = message.replace("\\", "\\\\").replace('"', '\\"')
            script = f'display notification "{text}" with title "Switcher"'
            subprocess.Popen(["osascript", "-e", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def permissions_hint(self) -> str:
        return ("Разрешите терминалу (или Python) доступ в «Системные настройки → Конфиденциальность и "
                "безопасность → Универсальный доступ» и «Мониторинг ввода».")
