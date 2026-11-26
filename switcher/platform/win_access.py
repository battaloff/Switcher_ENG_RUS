"""Windows: the right to change files in a folder that is closed to ordinary programs.

``Program Files`` can be written only by administrators, and Switcher does not run as one.  To edit
a CorelDRAW docker that lives there, Switcher asks once: ``icacls`` is started as administrator
(Windows shows its «Разрешить этому приложению вносить изменения?» question) and gives the current
user the right to modify that one folder.  After that Switcher writes there like anywhere else.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
ole32 = ctypes.WinDLL("ole32")

TOKEN_QUERY = 0x0008
TOKEN_USER = 1  # TokenUser: the account the program runs as
SEE_MASK_NOCLOSEPROCESS, SEE_MASK_NOASYNC, SEE_MASK_FLAG_NO_UI = 0x00000040, 0x00000100, 0x00000400
SW_HIDE = 0
COINIT_APARTMENTTHREADED, COINIT_DISABLE_OLE1DDE = 0x2, 0x4
ERROR_CANCELLED = 1223  # the user answered «Нет» to Windows' question
WAIT_OBJECT_0 = 0


class SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG), ("hwnd", wintypes.HWND),
                ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]


kernel32.GetCurrentProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
kernel32.LocalFree.argtypes = (ctypes.c_void_p,)
kernel32.LocalFree.restype = ctypes.c_void_p
kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
advapi32.OpenProcessToken.argtypes = (wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE))
advapi32.GetTokenInformation.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                         ctypes.POINTER(wintypes.DWORD))
advapi32.ConvertSidToStringSidW.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
shell32.ShellExecuteExW.argtypes = (ctypes.POINTER(SHELLEXECUTEINFOW),)
shell32.ShellExecuteExW.restype = wintypes.BOOL
ole32.CoInitializeEx.argtypes = (ctypes.c_void_p, wintypes.DWORD)
ole32.CoInitializeEx.restype = ctypes.c_long


class Refused(OSError):
    """The user said no to Windows' question."""


def user_sid() -> str:
    """The account Switcher runs as, the way Windows names it: "S-1-5-21-…" (works for any user name)."""
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        size = wintypes.DWORD()
        advapi32.GetTokenInformation(token, TOKEN_USER, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(token, TOKEN_USER, buffer, size, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]  # TOKEN_USER begins with the SID's address
        text = ctypes.c_void_p()
        if not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return ctypes.wstring_at(text.value)
        finally:
            kernel32.LocalFree(text)
    finally:
        kernel32.CloseHandle(token)


def run_as_administrator(program: str, parameters: str, timeout: float = 120.0, owner: int = 0) -> int:
    """Run a program as administrator, hidden, and wait for it: its exit code.  Windows asks the user
    first (over the window ``owner``); ``Refused`` if they say no."""
    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC | SEE_MASK_FLAG_NO_UI
    info.hwnd = owner or None
    info.lpVerb = "runas"
    info.lpFile = program
    info.lpParameters = parameters
    info.nShow = SW_HIDE
    com = ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED | COINIT_DISABLE_OLE1DDE)  # ShellExecuteEx asks for it
    try:
        started = shell32.ShellExecuteExW(ctypes.byref(info))
        error = ctypes.get_last_error()
    finally:
        if com >= 0:  # S_OK or S_FALSE: ours to undo
            ole32.CoUninitialize()
    if not started:
        if error == ERROR_CANCELLED:
            raise Refused(error, "the user did not allow it")
        raise ctypes.WinError(error)
    if not info.hProcess:
        raise OSError("Windows did not start the program")
    try:
        if kernel32.WaitForSingleObject(info.hProcess, int(timeout * 1000)) != WAIT_OBJECT_0:
            raise TimeoutError(f"{os.path.basename(program)} did not finish in {timeout:.0f} s")
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return code.value
    finally:
        kernel32.CloseHandle(info.hProcess)


def grant_modify(folder: str, timeout: float = 120.0, owner: int = 0) -> None:
    """Let the current user change the files in ``folder`` and everything in it (nothing else)."""
    folder = os.path.abspath(folder).rstrip("\\/")
    if not os.path.isdir(folder):
        raise FileNotFoundError(folder)
    icacls = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "icacls.exe")
    # (OI)(CI): the files and folders inside get the right too; M: modify
    code = run_as_administrator(icacls, f'"{folder}" /grant "*{user_sid()}:(OI)(CI)M"', timeout, owner)
    if code:
        raise OSError(f"icacls ended with the code {code}")
