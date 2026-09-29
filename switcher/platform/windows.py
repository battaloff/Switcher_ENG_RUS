"""Windows: layouts via WinAPI (GetKeyboardLayout / WM_INPUTLANGCHANGEREQUEST)."""

from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes

from ..layouts import EN, RU
from .base import BaseBackend

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

HKL = ctypes.c_void_p
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetKeyboardLayout.argtypes = (wintypes.DWORD,)
user32.GetKeyboardLayout.restype = HKL
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
        return _lang_of(user32.GetKeyboardLayout(tid))

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
        return bool(user32.PostMessageW(hwnd, WM_INPUTLANGCHANGEREQUEST, 0, ctypes.c_ssize_t(hkl).value))

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

    def type_text(self, text: str) -> None:
        """Type as Unicode characters, never as virtual keys.

        pynput sends plain Latin letters as virtual keys, which the target app
        translates with *its* layout — and our layout switch may not have
        reached it yet, turning "hello" into "руддщ".  Unicode input does not
        depend on the layout.
        """
        from pynput._util.win32 import INPUT, INPUT_union, KEYBDINPUT, SendInput

        self._busy()
        try:
            for ch in text:
                if ch in "\n\t":
                    key = self._pk.Key.enter if ch == "\n" else self._pk.Key.tab
                    self._out.press(key)
                    self._out.release(key)
                else:
                    encoded = ch.encode("utf-16-le")
                    units = [int.from_bytes(encoded[i:i + 2], "little") for i in range(0, len(encoded), 2)]
                    events = [
                        INPUT(type=INPUT.KEYBOARD, value=INPUT_union(ki=KEYBDINPUT(
                            wVk=0, wScan=unit, dwFlags=KEYBDINPUT.UNICODE | flags)))
                        for flags in (0, KEYBDINPUT.KEYUP) for unit in units
                    ]
                    SendInput(len(events), ctypes.byref((INPUT * len(events))(*events)), ctypes.sizeof(INPUT))
                if self.delay:
                    time.sleep(self.delay)
        finally:
            self._settle()

    def caps_lock_on(self) -> bool | None:
        return bool(user32.GetKeyState(VK_CAPITAL) & 1)

    def detect_ru_variant(self) -> str | None:
        return "pc"
