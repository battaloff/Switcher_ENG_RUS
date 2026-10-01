"""Keeps the keyboard hook alive on Windows.

Windows silently removes a low-level keyboard hook whose callback once answers
too slowly (the LowLevelHooksTimeout); the program is never told and simply
stops seeing keys.  Raw Input reports keystrokes independently of hooks, so both
are counted: when keys keep arriving through Raw Input while the hook sees
none, the hook is gone and gets reinstalled.  The hook is also reinstalled
after the computer wakes up and after the screen is unlocked, when Windows
is most likely to have dropped it.
"""

from __future__ import annotations

import ctypes
import logging
import threading
import time
from ctypes import wintypes
from typing import Callable

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
WM_DESTROY, WM_CLOSE, WM_INPUT = 0x0002, 0x0010, 0x00FF
WM_POWERBROADCAST, WM_WTSSESSION_CHANGE = 0x0218, 0x02B1
PBT_RESUMED = {0x0007, 0x0012}  # PBT_APMRESUMESUSPEND, PBT_APMRESUMEAUTOMATIC
WTS_BACK = {0x1, 0x3, 0x8}  # console / remote connect, session unlock
RIDEV_INPUTSINK = 0x00000100  # receive input even when another window has the focus
HWND_MESSAGE = wintypes.HWND(-3)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [("usUsagePage", wintypes.USHORT), ("usUsage", wintypes.USHORT), ("dwFlags", wintypes.DWORD),
                ("hwndTarget", wintypes.HWND)]


user32.DefWindowProcW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.DefWindowProcW.restype = LRESULT
user32.RegisterClassW.argtypes = (ctypes.POINTER(WNDCLASSW),)
user32.RegisterClassW.restype = wintypes.ATOM
user32.CreateWindowExW.argtypes = (wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                   wintypes.HINSTANCE, wintypes.LPVOID)
user32.CreateWindowExW.restype = wintypes.HWND
user32.RegisterRawInputDevices.argtypes = (ctypes.POINTER(RAWINPUTDEVICE), wintypes.UINT, wintypes.UINT)
user32.RegisterRawInputDevices.restype = wintypes.BOOL
user32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
user32.GetMessageW.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = (ctypes.POINTER(wintypes.MSG),)
user32.DispatchMessageW.argtypes = (ctypes.POINTER(wintypes.MSG),)
user32.DispatchMessageW.restype = LRESULT
user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.DestroyWindow.argtypes = (wintypes.HWND,)
user32.PostQuitMessage.argtypes = (ctypes.c_int,)
kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
kernel32.GetModuleHandleW.restype = wintypes.HMODULE


def _notify_on_wake(hwnd) -> None:
    """Ask for WM_POWERBROADCAST on resume and WM_WTSSESSION_CHANGE on unlock (best effort)."""
    try:
        user32.RegisterSuspendResumeNotification.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        user32.RegisterSuspendResumeNotification.restype = wintypes.HANDLE
        if not user32.RegisterSuspendResumeNotification(hwnd, 0):  # DEVICE_NOTIFY_WINDOW_HANDLE
            log.info("hook watchdog: no resume notifications (%s)", ctypes.get_last_error())
    except (AttributeError, OSError):
        log.info("hook watchdog: no resume notifications on this Windows")
    try:
        wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)
        wtsapi32.WTSRegisterSessionNotification.argtypes = (wintypes.HWND, wintypes.DWORD)
        wtsapi32.WTSRegisterSessionNotification.restype = wintypes.BOOL
        if not wtsapi32.WTSRegisterSessionNotification(hwnd, 0):  # NOTIFY_FOR_THIS_SESSION
            log.info("hook watchdog: no unlock notifications (%s)", ctypes.get_last_error())
    except (AttributeError, OSError):
        log.info("hook watchdog: no unlock notifications on this Windows")


class HookWatchdog:
    #: raw key events in a row the hook did not see (a key down and up each: about four keystrokes)
    MISSING = 8

    def __init__(self, restart: Callable[[], None]):
        self._restart = restart
        self._missed = 0
        self._hwnd = None
        self._proc = WNDPROC(self._window_proc)  # keep a reference: Windows calls it
        self.restarts = 0
        self.wakeups = 0
        self._last_wake = 0.0
        self.running = False

    def hook_saw_event(self) -> None:
        """Called from the hook for every key it sees."""
        self._missed = 0

    def start(self) -> None:
        threading.Thread(target=self._run, name="switcher-hook-watchdog", daemon=True).start()

    def stop(self) -> None:
        if self._hwnd:
            user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)

    def _run(self) -> None:
        try:
            instance = kernel32.GetModuleHandleW(None)
            name = f"SwitcherHookWatchdog{id(self)}"
            wc = WNDCLASSW(lpfnWndProc=self._proc, hInstance=instance, lpszClassName=name)
            if not user32.RegisterClassW(ctypes.byref(wc)):
                log.warning("hook watchdog: RegisterClass failed (%s)", ctypes.get_last_error())
                return
            hwnd = user32.CreateWindowExW(0, name, "", 0, 0, 0, 0, 0, HWND_MESSAGE, None, instance, None)
            if not hwnd:
                log.warning("hook watchdog: CreateWindow failed (%s)", ctypes.get_last_error())
                return
            self._hwnd = hwnd
            device = RAWINPUTDEVICE(0x01, 0x06, RIDEV_INPUTSINK, hwnd)  # generic desktop / keyboard
            if not user32.RegisterRawInputDevices(ctypes.byref(device), 1, ctypes.sizeof(device)):
                log.warning("hook watchdog: RegisterRawInputDevices failed (%s)", ctypes.get_last_error())
                return
            _notify_on_wake(hwnd)
            self.running = True
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception:
            log.exception("hook watchdog failed")
        finally:
            self.running = False

    def _window_proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_INPUT:
            self._missed += 1
            if self._missed >= self.MISSING:
                self._missed = 0
                self.restarts += 1
                log.warning("keys arrive but the keyboard hook sees none (Windows removed it?): reinstalling")
                threading.Thread(target=self._restart, name="switcher-hook-restart", daemon=True).start()
        elif (msg == WM_POWERBROADCAST and wparam in PBT_RESUMED) or (msg == WM_WTSSESSION_CHANGE
                                                                       and wparam in WTS_BACK):
            now = time.monotonic()
            if now - self._last_wake > 5:  # a wake-up comes as two messages
                self._last_wake = now
                self.wakeups += 1
                self._missed = 0
                log.info("woke up or unlocked: reinstalling the keyboard hook")
                threading.Thread(target=self._restart, name="switcher-hook-restart", daemon=True).start()
        elif msg == WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0
        elif msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
