"""Windows side of the AutoHotkey manager: running scripts by their windows, the install, the Startup folder."""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
from ctypes import wintypes
from pathlib import Path

from ..ahk import System, is_script, title_path

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
version = ctypes.WinDLL("version", use_last_error=True)

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = (WNDENUMPROC, wintypes.LPARAM)
user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.PostMessageW.restype = wintypes.BOOL
version.GetFileVersionInfoSizeW.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD))
version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
version.GetFileVersionInfoW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p)
version.GetFileVersionInfoW.restype = wintypes.BOOL
version.VerQueryValueW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p),
                                   ctypes.POINTER(wintypes.UINT))
version.VerQueryValueW.restype = wintypes.BOOL

WM_COMMAND = 0x0111
CREATE_NO_WINDOW = 0x08000000


class VS_FIXEDFILEINFO(ctypes.Structure):
    _fields_ = [(name, wintypes.DWORD) for name in (
        "dwSignature", "dwStrucVersion", "dwFileVersionMS", "dwFileVersionLS", "dwProductVersionMS",
        "dwProductVersionLS", "dwFileFlagsMask", "dwFileFlags", "dwFileOS", "dwFileType", "dwFileSubtype",
        "dwFileDateMS", "dwFileDateLS")]


def _text(fn, hwnd, size: int = 512) -> str:
    buf = ctypes.create_unicode_buffer(size)
    fn(hwnd, buf, size)
    return buf.value


class WindowsAhk(System):
    supported = True

    def running(self) -> dict[str, int]:
        found: dict[str, int] = {}

        def visit(hwnd, _):
            if _text(user32.GetClassNameW, hwnd, 64) == "AutoHotkey":
                path = title_path(_text(user32.GetWindowTextW, hwnd))
                if path:
                    found.setdefault(path, int(hwnd))
            return True

        user32.EnumWindows(WNDENUMPROC(visit), 0)  # hidden windows too: a script's main window is hidden
        return found

    def command(self, window: int, command: int) -> bool:
        return bool(user32.PostMessageW(window, WM_COMMAND, command, 0))

    def launch(self, path: str) -> None:
        # like a double click in Explorer, which also starts a script in its own folder
        os.startfile(path, "open", "", os.path.dirname(path))  # type: ignore[attr-defined]

    def edit_elsewhere(self, path: str) -> None:
        try:
            os.startfile(path, "edit")  # type: ignore[attr-defined]  # the editor chosen for .ahk files
        except OSError:
            subprocess.Popen(["notepad.exe", path])

    def install_roots(self) -> list[str]:
        roots = []
        try:
            import winreg

            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                    try:
                        with winreg.OpenKey(hive, r"SOFTWARE\AutoHotkey", 0, winreg.KEY_READ | view) as key:
                            roots.append(winreg.QueryValueEx(key, "InstallDir")[0])
                    except OSError:
                        pass
        except ImportError:
            pass
        for variable in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
            if os.environ.get(variable):
                roots.append(os.path.join(os.environ[variable], "AutoHotkey"))
        if os.environ.get("LOCALAPPDATA"):
            roots.append(os.path.join(os.environ["LOCALAPPDATA"], "Programs", "AutoHotkey"))
        return list(dict.fromkeys(r for r in roots if r and os.path.isdir(r)))

    def major_of(self, path: str) -> int | None:
        size = version.GetFileVersionInfoSizeW(path, None)
        if not size:
            return None
        data = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(path, 0, size, data):
            return None
        info, length = ctypes.c_void_p(), wintypes.UINT()
        if not version.VerQueryValueW(data, "\\", ctypes.byref(info), ctypes.byref(length)) or not info.value:
            return None
        fixed = ctypes.cast(info, ctypes.POINTER(VS_FIXEDFILEINFO)).contents
        major = fixed.dwFileVersionMS >> 16
        return major if major in (1, 2) else None

    def run(self, args: list[str], timeout: float) -> tuple[int, str]:
        done = subprocess.run(args, capture_output=True, timeout=timeout, creationflags=CREATE_NO_WINDOW,
                              cwd=os.path.dirname(args[-1]) or None)
        raw = done.stdout + done.stderr
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("mbcs", errors="replace")  # AutoHotkey v1 writes in the system code page
        return done.returncode, text

    def startup_scripts(self) -> list[str]:
        folder = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        try:
            return [str(p) for p in folder.iterdir() if p.is_file() and is_script(p) and p.suffix.lower() != ".exe"]
        except OSError:
            return []
