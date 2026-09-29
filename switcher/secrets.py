"""Keeps the Claude API key out of plain text in config.json.

On Windows the key is encrypted with DPAPI, so only the same Windows user on
the same machine can decrypt it.  Elsewhere it is merely encoded.
"""

from __future__ import annotations

import base64
import sys

_DPAPI = "dpapi:"
_PLAIN = "plain:"


def protect(secret: str) -> str:
    if not secret:
        return ""
    if sys.platform == "win32":
        try:
            return _DPAPI + base64.b64encode(_dpapi(secret.encode("utf-8"), encrypt=True)).decode("ascii")
        except OSError:
            pass
    return _PLAIN + base64.b64encode(secret.encode("utf-8")).decode("ascii")


def reveal(stored: str) -> str:
    if not stored:
        return ""
    try:
        if stored.startswith(_DPAPI):
            return _dpapi(base64.b64decode(stored[len(_DPAPI):]), encrypt=False).decode("utf-8")
        if stored.startswith(_PLAIN):
            return base64.b64decode(stored[len(_PLAIN):]).decode("utf-8")
    except (OSError, ValueError):
        return ""
    return stored  # typed into config.json by hand


def _dpapi(data: bytes, encrypt: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = (ctypes.c_void_p,)
    blob_ptr = ctypes.POINTER(DATA_BLOB)
    if encrypt:
        fn = crypt32.CryptProtectData
        fn.argtypes = (blob_ptr, wintypes.LPCWSTR, blob_ptr, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, blob_ptr)
    else:
        fn = crypt32.CryptUnprotectData
        fn.argtypes = (blob_ptr, ctypes.c_void_p, blob_ptr, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, blob_ptr)
    fn.restype = wintypes.BOOL

    buffer = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    CRYPTPROTECT_UI_FORBIDDEN = 0x01
    description = "Switcher" if encrypt else None
    if not fn(ctypes.byref(blob_in), description, None, None, None, CRYPTPROTECT_UI_FORBIDDEN,
              ctypes.byref(blob_out)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))
