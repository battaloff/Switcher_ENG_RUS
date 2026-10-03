"""Windows: layouts via WinAPI (GetKeyboardLayout / WM_INPUTLANGCHANGEREQUEST)."""

from __future__ import annotations

import ctypes
import logging
import os
import threading
import time
from ctypes import wintypes

from ..layouts import EN, RU, Stroke
from .base import BaseBackend

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

HKL = ctypes.c_void_p
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetKeyboardLayout.argtypes = (wintypes.DWORD,)
user32.GetKeyboardLayout.restype = HKL
class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


user32.GetLastInputInfo.argtypes = (ctypes.POINTER(LASTINPUTINFO),)
user32.GetLastInputInfo.restype = wintypes.BOOL
kernel32.GetTickCount.restype = wintypes.DWORD
user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
user32.GetAsyncKeyState.restype = ctypes.c_short
_MODIFIER_VKS = (("shift", 0x10), ("ctrl", 0x11), ("alt", 0x12), ("cmd", 0x5B), ("cmd", 0x5C))
user32.GetKeyboardLayoutList.argtypes = (ctypes.c_int, ctypes.POINTER(HKL))
user32.GetKeyboardLayoutList.restype = ctypes.c_int
user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.PostMessageW.restype = wintypes.BOOL
user32.GetKeyState.argtypes = (ctypes.c_int,)
user32.GetKeyState.restype = ctypes.c_short
kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD))
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD), ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND), ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND), ("rcCaret", wintypes.RECT),
    ]


user32.GetGUIThreadInfo.argtypes = (wintypes.DWORD, ctypes.POINTER(GUITHREADINFO))
user32.GetGUIThreadInfo.restype = wintypes.BOOL

WM_INPUTLANGCHANGEREQUEST = 0x0050
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
VK_CAPITAL = 0x14
VK_BACK, VK_TAB, VK_RETURN = 0x08, 0x09, 0x0D
# Tag in dwExtraInfo of every key we send.  The hook drops exactly these, so keys
# the user (or an automated check) types while we are typing are never lost.
OWN_INPUT = 0x53574348  # "SWCH"
_PRIMARY_LANG = {0x09: EN, 0x19: RU}

_VK = {0x30 + i: str(i) for i in range(10)}
_VK.update({0x41 + i: chr(ord("a") + i) for i in range(26)})
_VK.update({
    0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".", 0xBF: "/", 0xC0: "`",
    0xDB: "[", 0xDC: "\\", 0xDD: "]", 0xDE: "'",
})


def _lang_of(hkl: int | None) -> str | None:
    if not hkl:
        return None
    return _PRIMARY_LANG.get(hkl & 0x3FF)


class WindowsBackend(BaseBackend):
    VK_CODES = _VK

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._hkls: dict[str, int] = {}
        self._app_cache: tuple[int, float, str] = (0, 0.0, "")
        self._pending_layout: tuple[str | None, float] = (None, 0.0)
        self._watchdog = None
        self._watchdog_retry_at = 0.0
        self._away = False
        self._save_dialogs = None
        self._refresh_layouts()

    def _refresh_layouts(self) -> None:
        count = user32.GetKeyboardLayoutList(0, None)
        buf = (HKL * max(count, 1))()
        count = user32.GetKeyboardLayoutList(count, buf)
        found: dict[str, int] = {}
        for hkl in buf[:count]:
            lang = _lang_of(hkl)
            if lang and lang not in found:
                found[lang] = hkl
        self._hkls = found

    def _foreground_thread(self) -> tuple[int, int]:
        hwnd = user32.GetForegroundWindow()
        pid = wintypes.DWORD()
        tid = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return tid, pid.value

    def current_layout(self) -> str | None:
        tid, _ = self._foreground_thread()
        actual = _lang_of(user32.GetKeyboardLayout(tid))
        pending, since = self._pending_layout
        if pending and actual != pending and time.monotonic() - since < 0.5:
            # we asked for a switch a moment ago; keys typed now reach the window after it applies
            return pending
        self._pending_layout = (None, 0.0)
        return actual

    def set_layout(self, lang: str) -> bool:
        if lang not in self._hkls:
            self._refresh_layouts()
        hkl = self._hkls.get(lang)
        if not hkl:
            self.notify(f"Раскладка {lang.upper()} не установлена в Windows")
            return False
        hwnd = user32.GetForegroundWindow()
        tid, _ = self._foreground_thread()
        info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
        if user32.GetGUIThreadInfo(tid, ctypes.byref(info)) and info.hwndFocus:
            hwnd = info.hwndFocus
        posted = bool(user32.PostMessageW(hwnd, WM_INPUTLANGCHANGEREQUEST, 0, ctypes.c_ssize_t(hkl).value))
        if posted:
            self._pending_layout = (lang, time.monotonic())
        return posted

    def active_app(self) -> str:
        _, pid = self._foreground_thread()
        cached_pid, stamp, name = self._app_cache
        now = time.monotonic()
        if pid == cached_pid and now - stamp < 2.0:
            return name
        name = ""
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            try:
                size = wintypes.DWORD(1024)
                buf = ctypes.create_unicode_buffer(size.value)
                if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                    name = os.path.splitext(os.path.basename(buf.value))[0]
            finally:
                kernel32.CloseHandle(handle)
        self._app_cache = (pid, now, name)
        return name

    def _describe(self, key):
        """The char the focused window gets, from the physical key and *its* layout.

        pynput translates keys with the wrong layout (it reports "hello" while
        Notepad receives "руддщ"), so only its letter case is trusted.
        """
        if isinstance(key, self._pk.Key):
            return super()._describe(key)
        vk = getattr(key, "vk", None)
        code = self.VK_CODES.get(vk) if vk is not None else None
        lang = self.current_layout() if code else None
        if lang is None:
            return super()._describe(key)
        real = self.keyboard.layouts[lang].char(Stroke(code, self._shift))
        if real.isalpha():
            hint = key.char if key.char and len(key.char) == 1 and key.char.isalpha() else None
            upper = hint.isupper() if hint else self._shift != bool(user32.GetKeyState(VK_CAPITAL) & 1)
            real = real.upper() if upper else real.lower()
        return "char", real, code

    def _listener_options(self) -> dict:
        return {"win32_event_filter": self._hook_filter}

    def _hook_filter(self, msg, data) -> bool:
        """Runs in the hook for every key: tells the watchdog the hook is alive, drops our own keys."""
        watchdog = self._watchdog
        if watchdog is not None:
            watchdog.hook_saw_event()
        return (data.dwExtraInfo or 0) != OWN_INPUT

    def start(self, sink) -> None:
        super().start(sink)
        from .win_watchdog import HookWatchdog

        self._watchdog = HookWatchdog(self._restart_keyboard_hook)
        self._watchdog_retry_at = time.monotonic() + 30  # give it time to start
        self._watchdog.start()
        try:
            from .win_events import SaveDialogWatcher

            self._save_dialogs = SaveDialogWatcher(self._save_dialog_opened)
            self._save_dialogs.start()
        except Exception:
            log.exception("could not watch for save dialogs")

    def _save_dialog_opened(self, hwnd: int, again: bool = True) -> None:
        from ..controller import KeyEvent

        if self._sink:
            self._sink(KeyEvent("press", "save-dialog", layout=self.current_layout(), app=self.active_app(),
                                time=time.monotonic()))
        if again:  # a dialog still setting itself up may miss the first request: make sure
            threading.Timer(0.8, self._recheck_save_dialog, args=(hwnd,)).start()

    def _recheck_save_dialog(self, hwnd: int) -> None:
        if user32.GetForegroundWindow() == hwnd and self.current_layout() != EN:
            log.info("the save dialog is still not on English: asking again")
            self._save_dialog_opened(hwnd, again=False)

    def stop(self) -> None:
        if self._save_dialogs is not None:
            self._save_dialogs.stop()
            self._save_dialogs = None
        if self._watchdog is not None:
            self._watchdog.stop()
            self._watchdog = None
        super().stop()

    def _held_mods(self) -> frozenset[str]:
        return frozenset(name for name, vk in _MODIFIER_VKS if user32.GetAsyncKeyState(vk) & 0x8000)

    #: our own input is tagged (OWN_INPUT), so injected keys from elsewhere can be trusted when they
    #: are all there is: a remote desktop session or keyboard software sends the user's keys that way
    TRUST_INJECTED_AFTER = 30
    #: a break this long (night, lunch, sleep, a locked screen) and the hook is reinstalled on return
    AWAY_AFTER = 300.0

    @staticmethod
    def idle_seconds() -> float:
        """Since the last keyboard or mouse input in this session."""
        info = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        return ((kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0

    def _restart_keyboard_hook(self, missed: bool = True) -> None:
        super()._restart_keyboard_hook(missed)
        if self._watchdog is not None:
            self._watchdog.hook_restarted()

    def heal(self) -> str | None:
        fixed = super().heal()
        idle = self.idle_seconds()
        if idle >= self.AWAY_AFTER:
            self._away = True
        elif self._away and idle < 30:
            # back at the computer: whatever happened meanwhile (sleep, lock, a hook Windows dropped
            # unnoticed), a fresh hook costs nothing
            self._away = False
            log.info("back after a break: reinstalling the keyboard hook")
            self._restart_keyboard_hook(missed=False)
            fixed = fixed or "hook after a break"
        watchdog = self._watchdog
        if watchdog is not None and not watchdog.running and time.monotonic() > self._watchdog_retry_at:
            # without it a hook Windows drops is never noticed; retry now and then, not every check
            self._watchdog_retry_at = time.monotonic() + 600
            log.error("the hook watchdog is not running: starting a new one")
            from .win_watchdog import HookWatchdog

            watchdog.stop()
            self._watchdog = HookWatchdog(self._restart_keyboard_hook)
            self._watchdog.start()
            fixed = fixed or "watchdog"
        return fixed

    @staticmethod
    def _send(keys: list[tuple[int, int, int]]) -> None:
        """SendInput a batch of (vk, scan, flags), tagged as ours; a batch is never split by other input."""
        from pynput._util.win32 import INPUT, INPUT_union, KEYBDINPUT, SendInput

        events = [INPUT(type=INPUT.KEYBOARD, value=INPUT_union(ki=KEYBDINPUT(
            wVk=vk, wScan=scan, dwFlags=flags, dwExtraInfo=OWN_INPUT))) for vk, scan, flags in keys]
        SendInput(len(events), ctypes.byref((INPUT * len(events))(*events)), ctypes.sizeof(INPUT))

    def backspace(self, count: int) -> None:
        from pynput._util.win32 import KEYBDINPUT

        for _ in range(count):
            self._send([(VK_BACK, 0, 0), (VK_BACK, 0, KEYBDINPUT.KEYUP)])
            if self.delay:
                time.sleep(self.delay)

    def type_text(self, text: str) -> None:
        """Type as Unicode characters, never as virtual keys.

        pynput sends plain Latin letters as virtual keys, which the target app
        translates with *its* layout — and our layout switch may not have
        reached it yet, turning "hello" into "руддщ".  Unicode input does not
        depend on the layout.
        """
        from pynput._util.win32 import KEYBDINPUT

        for ch in text:
            if ch in "\n\t":
                vk = VK_RETURN if ch == "\n" else VK_TAB
                self._send([(vk, 0, 0), (vk, 0, KEYBDINPUT.KEYUP)])
            else:
                encoded = ch.encode("utf-16-le")
                units = [int.from_bytes(encoded[i:i + 2], "little") for i in range(0, len(encoded), 2)]
                self._send([(0, unit, KEYBDINPUT.UNICODE | flags) for flags in (0, KEYBDINPUT.KEYUP)
                            for unit in units])
            if self.delay:
                time.sleep(self.delay)

    def caps_lock_on(self) -> bool | None:
        return bool(user32.GetKeyState(VK_CAPITAL) & 1)

    def detect_ru_variant(self) -> str | None:
        return "pc"
