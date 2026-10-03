"""Windows: notice "Save As" dialogs as they open, so that the file name is typed in English.

A dialog is a "Save As" one when it is a standard dialog (class #32770) with a file browser in it
(the shell view) and a title about saving or exporting.  "Save changes?" message boxes have no file
browser, so they are left alone.
"""

from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes
from typing import Callable

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WINEVENTPROC = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND, wintypes.LONG,
                                  wintypes.LONG, wintypes.DWORD, wintypes.DWORD)
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_DIALOGSTART = 0x0003, 0x0010
WINEVENT_OUTOFCONTEXT, WINEVENT_SKIPOWNPROCESS = 0x0000, 0x0002
OBJID_WINDOW = 0
WM_QUIT = 0x0012

user32.SetWinEventHook.argtypes = (wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE, WINEVENTPROC, wintypes.DWORD,
                                   wintypes.DWORD, wintypes.DWORD)
user32.SetWinEventHook.restype = wintypes.HANDLE
user32.UnhookWinEvent.argtypes = (wintypes.HANDLE,)
user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.EnumChildWindows.argtypes = (wintypes.HWND, WNDENUMPROC, wintypes.LPARAM)
user32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
user32.GetMessageW.restype = wintypes.BOOL
user32.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

SAVE_WORDS = ("сохран", "save", "экспорт", "export", "зберег")
SHELL_VIEWS = {"DirectUIHWND", "SHELLDLL_DefView", "DUIViewWndClassName"}


def _class(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(128)
    user32.GetClassNameW(hwnd, buf, 128)
    return buf.value


def _title(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, 256)
    return buf.value


def looks_like_save(hwnd) -> bool:
    """A standard dialog titled about saving (its file browser may not be there yet)."""
    return _class(hwnd) == "#32770" and any(word in _title(hwnd).lower() for word in SAVE_WORDS)


def has_file_browser(hwnd) -> bool:
    found = []

    def visit(child, _):
        if _class(child) in SHELL_VIEWS:
            found.append(child)
            return False
        return True

    user32.EnumChildWindows(hwnd, WNDENUMPROC(visit), 0)
    return bool(found)


class SaveDialogWatcher:
    def __init__(self, on_save_dialog: Callable[[int], None]):
        self._on_save_dialog = on_save_dialog
        self._proc = WINEVENTPROC(self._event)  # keep a reference: Windows calls it
        self._tid = 0
        self._seen: list[int] = []
        self.running = False

    def start(self) -> None:
        threading.Thread(target=self._run, name="switcher-save-dialogs", daemon=True).start()

    def stop(self) -> None:
        if self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)

    def _run(self) -> None:
        hooks = []
        try:
            self._tid = kernel32.GetCurrentThreadId()
            for event in (EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_DIALOGSTART):
                hook = user32.SetWinEventHook(event, event, None, self._proc, 0, 0,
                                              WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS)
                if hook:
                    hooks.append(hook)
            if not hooks:
                log.warning("save dialogs: SetWinEventHook failed (%s)", ctypes.get_last_error())
                return
            self.running = True
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                pass  # out-of-context events are delivered while this thread waits for messages
        except Exception:
            log.exception("save dialog watcher failed")
        finally:
            self.running = False
            for hook in hooks:
                user32.UnhookWinEvent(hook)

    def _event(self, hook, event, hwnd, id_object, id_child, thread, time_ms) -> None:
        if id_object != OBJID_WINDOW or not hwnd:
            return
        try:
            self._check(int(hwnd), retry=True)
        except Exception:
            log.exception("save dialog check failed")

    def _check(self, hwnd: int, retry: bool) -> None:
        if hwnd in self._seen or not looks_like_save(hwnd):
            return
        if has_file_browser(hwnd):
            self._seen = (self._seen + [hwnd])[-20:]  # once per dialog: the user may switch back
            log.info("a save dialog opened: %r", _title(hwnd))
            self._on_save_dialog(hwnd)
        elif retry:  # the file browser comes a moment after the dialog itself
            threading.Timer(0.4, self._check, args=(hwnd, False)).start()
