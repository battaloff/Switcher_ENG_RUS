"""Windows side of the «Сохранить как» panel: what a file-save dialog shows, where its rows are, and
moving it into a folder while keeping the file name the user has typed.

The address bar of a file dialog is a toolbar whose text is "Адрес: <folder>".  The row of
«Упорядочить» and «Новая папка» has no window of its own: it is the strip between the address bar
and the folder tree / file list, and the two buttons' width is measured from their captions.
"""

from __future__ import annotations

import ctypes
import functools
import logging
import os
import time
import uuid
from ctypes import wintypes

from ..savebar import Geometry, Rect, folder_from_address, ink_end
from .win_events import has_file_browser, looks_like_save

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
ole32 = ctypes.WinDLL("ole32")

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumChildWindows.argtypes = (wintypes.HWND, WNDENUMPROC, wintypes.LPARAM)
user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetParent.argtypes = (wintypes.HWND,)
user32.GetParent.restype = wintypes.HWND
user32.GetDlgCtrlID.argtypes = (wintypes.HWND,)
user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
user32.IsWindow.argtypes = (wintypes.HWND,)
user32.IsWindowVisible.argtypes = (wintypes.HWND,)
user32.IsWindowEnabled.argtypes = (wintypes.HWND,)
user32.GetForegroundWindow.restype = wintypes.HWND
user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.SendMessageTimeoutW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM, wintypes.UINT,
                                       wintypes.UINT, ctypes.POINTER(ctypes.c_size_t))
user32.SendMessageTimeoutW.restype = ctypes.c_ssize_t
user32.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
user32.SetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_long)
user32.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                wintypes.UINT)
user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
user32.GetDC.argtypes = (wintypes.HWND,)
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
gdi32.GetPixel.argtypes = (wintypes.HDC, ctypes.c_int, ctypes.c_int)
gdi32.GetPixel.restype = wintypes.DWORD
gdi32.CreateFontW.argtypes = (ctypes.c_int,) * 5 + (wintypes.DWORD,) * 8 + (wintypes.LPCWSTR,)
gdi32.CreateFontW.restype = wintypes.HFONT
gdi32.SelectObject.argtypes = (wintypes.HDC, wintypes.HGDIOBJ)
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
gdi32.GetTextExtentPoint32W.argtypes = (wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(wintypes.SIZE))
gdi32.GetDeviceCaps.argtypes = (wintypes.HDC, ctypes.c_int)
gdi32.CreateCompatibleDC.argtypes = (wintypes.HDC,)
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleBitmap.argtypes = (wintypes.HDC, ctypes.c_int, ctypes.c_int)
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.BitBlt.argtypes = (wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HDC,
                         ctypes.c_int, ctypes.c_int, wintypes.DWORD)
gdi32.GetDIBits.argtypes = (wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                            ctypes.c_void_p, wintypes.UINT)
gdi32.DeleteDC.argtypes = (wintypes.HDC,)
ole32.CoTaskMemFree.argtypes = (ctypes.c_void_p,)

WM_GETTEXT, WM_SETTEXT, WM_COMMAND, WM_NEXTDLGCTL = 0x000D, 0x000C, 0x0111, 0x0028
EM_GETSEL, EM_SETSEL = 0x00B0, 0x00B1
SMTO_ABORTIFHUNG = 0x0002
IDOK = 1
FILE_NAME_BOX = 1148  # cmb13: the file name box of a file dialog
GWL_EXSTYLE = -20
WS_EX_TOPMOST, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x00000008, 0x00000080, 0x08000000
HWND_TOPMOST = wintypes.HWND(-1)
SWP_NOSIZE, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x0001, 0x0010, 0x0040
SW_HIDE = 0
LOGPIXELSX = 88
CLR_INVALID = 0xFFFFFFFF
SRCCOPY = 0x00CC0020

# the captions of «Упорядочить» and «Новая папка» by the language of Windows
COMMAND_CAPTIONS = {0x19: ("Упорядочить", "Новая папка"), 0x22: ("Упорядкувати", "Нова папка"),
                    0x09: ("Organize", "New folder")}
# what the two buttons take beside their captions, in pixels at 100 %: the margin before
# «Упорядочить», its arrow and padding, the padding of «Новая папка», the gap before the panel
COMMAND_EXTRA = 12 + 34 + 28

KNOWN_FOLDERS = {
    "desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}", "documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
    "downloads": "{374DE290-123F-4565-9164-39C4925E467B}", "pictures": "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
    "music": "{4BD8D571-6D19-48D3-BE97-422220080E43}", "videos": "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}",
}


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class GUID(ctypes.Structure):
    _fields_ = [("data", ctypes.c_ubyte * 16)]


class SHFILEINFOW(ctypes.Structure):
    _fields_ = [("hIcon", wintypes.HANDLE), ("iIcon", ctypes.c_int), ("dwAttributes", wintypes.DWORD),
                ("szDisplayName", wintypes.WCHAR * 260), ("szTypeName", wintypes.WCHAR * 80)]


shell32.SHGetKnownFolderPath.argtypes = (ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE,
                                         ctypes.POINTER(ctypes.c_void_p))
shell32.SHGetFileInfoW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(SHFILEINFOW), wintypes.UINT,
                                   wintypes.UINT)
shell32.SHGetFileInfoW.restype = ctypes.c_void_p
SHGFI_DISPLAYNAME = 0x000000200


def _class(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(128)
    user32.GetClassNameW(hwnd, buf, 128)
    return buf.value


def _rect(hwnd) -> Rect:
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def get_text(hwnd, size: int = 2048) -> str:
    """A control's text, also in another program; never waits on a program that hangs."""
    buf = ctypes.create_unicode_buffer(size)
    result = ctypes.c_size_t()
    if not user32.SendMessageTimeoutW(hwnd, WM_GETTEXT, size, ctypes.addressof(buf), SMTO_ABORTIFHUNG, 500,
                                      ctypes.byref(result)):
        return ""
    return buf.value


def set_text(hwnd, text: str) -> bool:
    buf = ctypes.create_unicode_buffer(text)
    result = ctypes.c_size_t()
    return bool(user32.SendMessageTimeoutW(hwnd, WM_SETTEXT, 0, ctypes.addressof(buf), SMTO_ABORTIFHUNG, 500,
                                           ctypes.byref(result)))


def _children(hwnd) -> list[int]:
    found: list[int] = []

    def visit(child, _):
        found.append(int(child))
        return len(found) < 2000

    user32.EnumChildWindows(hwnd, WNDENUMPROC(visit), 0)
    return found


def _ancestors(hwnd, top) -> list[int]:
    chain = []
    parent = user32.GetParent(hwnd)
    while parent and int(parent) != int(top) and len(chain) < 12:
        chain.append(int(parent))
        parent = user32.GetParent(parent)
    return chain


class Parts:
    """The windows of a file dialog that the panel needs."""

    def __init__(self, dialog: int):
        self.dialog = dialog
        self.address = self.band = self.view = self.file_edit = self.save_button = None
        self.pane_tops: list[int] = []
        best_edit = 0
        for child in _children(dialog):
            cls = _class(child)
            if cls == "ToolbarWindow32" and self.address is None and _class(user32.GetParent(child)) == "Breadcrumb Parent":
                self.address = child
            elif cls == "Address Band Root" and self.band is None and user32.IsWindowVisible(child):
                self.band = _rect(child)
            elif cls in ("NamespaceTreeControl", "SHELLDLL_DefView") and user32.IsWindowVisible(child):
                self.pane_tops.append(_rect(child)[1])
            elif cls == "DUIViewWndClassName" and self.view is None:
                self.view = _rect(child)
            elif cls == "Button" and user32.GetDlgCtrlID(child) == IDOK and self.save_button is None:
                self.save_button = child
            elif cls == "Edit":
                score = self._file_name_score(child)
                if score > best_edit:
                    best_edit, self.file_edit = score, child

    def _file_name_score(self, edit) -> int:
        """2: the edit of the file name box; 1: an edit in some combo box; 0: another edit."""
        chain = _ancestors(edit, self.dialog)
        classes = [_class(a) for a in chain]
        if any(c in ("Address Band Root", "UniversalSearchBand", "SearchEditBoxWrapperClass", "ReBarWindow32")
               for c in classes):
            return 0  # the address bar or the search box
        for ancestor, cls in zip(chain, classes):
            if cls in ("ComboBox", "ComboBoxEx32"):
                return 2 if user32.GetDlgCtrlID(ancestor) == FILE_NAME_BOX else 1
        return 0

    def row(self) -> Rect | None:
        """The strip of «Упорядочить» and «Новая папка»: between the address bar and the panes."""
        if self.band is None or self.view is None:
            return None
        top = self.band[3]
        below = [t for t in self.pane_tops if t > top]
        if not below:
            return None
        return self.view[0], top, self.view[2], min(below)


def dialog_scale(hwnd) -> float:
    try:
        dpi = user32.GetDpiForWindow(hwnd)
    except AttributeError:  # before Windows 10
        dpi = 0
    if not dpi:
        hdc = user32.GetDC(None)
        dpi = gdi32.GetDeviceCaps(hdc, LOGPIXELSX)
        user32.ReleaseDC(None, hdc)
    return (dpi or 96) / 96


def text_width(text: str, scale: float) -> int:
    hdc = user32.GetDC(None)
    font = gdi32.CreateFontW(-round(12 * scale), 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 5, 0, "Segoe UI")
    old = gdi32.SelectObject(hdc, font)
    size = wintypes.SIZE()
    gdi32.GetTextExtentPoint32W(hdc, text, len(text), ctypes.byref(size))
    gdi32.SelectObject(hdc, old)
    gdi32.DeleteObject(font)
    user32.ReleaseDC(None, hdc)
    return size.cx


@functools.cache
def _command_width(scale: float) -> int:
    language = kernel32.GetUserDefaultUILanguage() & 0x3FF
    captions = COMMAND_CAPTIONS.get(language)
    if captions is None:  # another language: the longer captions of the ones we know, to be safe
        widths = [sum(text_width(c, scale) for c in pair) for pair in COMMAND_CAPTIONS.values()]
        return max(widths) + round(COMMAND_EXTRA * scale)
    return sum(text_width(c, scale) for c in captions) + round(COMMAND_EXTRA * scale)


def grab(left: int, top: int, width: int, height: int) -> list[int]:
    """What the screen shows there, as 0xRRGGBB pixels row by row."""
    screen = user32.GetDC(None)
    memory = gdi32.CreateCompatibleDC(screen)
    bitmap = gdi32.CreateCompatibleBitmap(screen, width, height)
    old = gdi32.SelectObject(memory, bitmap)
    try:
        gdi32.BitBlt(memory, 0, 0, width, height, screen, left, top, SRCCOPY)
        header = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=width, biHeight=-height,
                                  biPlanes=1, biBitCount=32, biCompression=0)
        pixels = (ctypes.c_uint32 * (width * height))()
        info = (ctypes.c_byte * (ctypes.sizeof(BITMAPINFOHEADER) + 16))()
        ctypes.memmove(info, ctypes.byref(header), ctypes.sizeof(header))
        if not gdi32.GetDIBits(memory, bitmap, 0, height, pixels, info, 0):
            return []
        return [p & 0xFFFFFF for p in pixels]
    finally:
        gdi32.SelectObject(memory, old)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory)
        user32.ReleaseDC(None, screen)


def color_at(x: int, y: int) -> str | None:
    hdc = user32.GetDC(None)
    color = gdi32.GetPixel(hdc, x, y)
    user32.ReleaseDC(None, hdc)
    if color == CLR_INVALID:
        return None
    return f"#{color & 0xFF:02x}{(color >> 8) & 0xFF:02x}{(color >> 16) & 0xFF:02x}"


def _guid(text: str) -> GUID:
    guid = GUID()
    ctypes.memmove(guid.data, uuid.UUID(text).bytes_le, 16)
    return guid


def display_name(path: str) -> str:
    info = SHFILEINFOW()
    if not shell32.SHGetFileInfoW(path, 0, ctypes.byref(info), ctypes.sizeof(info), SHGFI_DISPLAYNAME):
        return ""
    return info.szDisplayName


@functools.lru_cache(maxsize=1)
def known_folders() -> dict[str, str]:
    """Desktop, Documents, Downloads…: their names as a file dialog may show them → the folder."""
    found: dict[str, str] = {}
    for english, folder_id in KNOWN_FOLDERS.items():
        out = ctypes.c_void_p()
        if shell32.SHGetKnownFolderPath(ctypes.byref(_guid(folder_id)), 0, None, ctypes.byref(out)) != 0:
            continue
        try:
            path = ctypes.wstring_at(out.value)
        finally:
            ole32.CoTaskMemFree(out)
        for name in (english, os.path.basename(path), display_name(path)):
            if name:
                found.setdefault(name.lower(), path)
    return found


def _long(path: str) -> str:
    """A path Windows takes even when it is longer than 260 characters."""
    if path.startswith("\\\\?\\"):
        return path
    if path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + path[2:]
    return "\\\\?\\" + path


def _same(a: str, b: str) -> bool:
    """The same folder, also written another way (a short 8.3 name like RUNNER~1)."""
    def norm(path: str) -> str:
        try:
            path = os.path.realpath(path)
        except (OSError, ValueError):
            pass
        return os.path.normcase(os.path.normpath(path))

    return norm(a) == norm(b)


class WindowsSaveDialogs:
    """What SaveBarPanel needs from Windows."""

    def foreground(self) -> int:
        return int(user32.GetForegroundWindow() or 0)

    def is_save_dialog(self, hwnd: int) -> bool:
        return _class(hwnd) == "#32770" and looks_like_save(hwnd) and has_file_browser(hwnd)

    def alive(self, hwnd: int) -> bool:
        return bool(user32.IsWindow(hwnd))

    def dialog_rect(self, hwnd: int) -> Rect:
        return _rect(hwnd)

    def __init__(self):
        self._ink: dict[int, int] = {}  # dialog → where its «Новая папка» ends, from the row's left

    def geometry(self, hwnd: int, measure: bool = True) -> Geometry | None:
        if not user32.IsWindowVisible(hwnd) or not user32.IsWindowEnabled(hwnd):
            return None  # minimized away, or a message box is open over it
        dialog = _rect(hwnd)
        scale = dialog_scale(hwnd)
        parts = Parts(hwnd)
        row = parts.row()
        if row is None or not 16 * scale <= row[3] - row[1] <= 90 * scale:
            return Geometry(dialog, None, None, scale)
        after_x = row[0] + _command_width(scale)
        color = None
        if measure:  # the panel is not over the row: see where «Новая папка» really ends
            color = color_at(row[2] - round(140 * scale), (row[1] + row[3]) // 2)
            if hwnd not in self._ink:
                width = min(row[2] - row[0], round(700 * scale))
                height = max(4, round(12 * scale))
                found = ink_end(grab(row[0], (row[1] + row[3] - height) // 2, width, height), width, scale)
                if found is not None:
                    self._ink[hwnd] = found
                    log.info("save bar: «Новая папка» ends %d px into the row (from the captions: %d)", found,
                             after_x - row[0] - round(14 * scale))
        if hwnd in self._ink:
            after_x = row[0] + self._ink[hwnd] + round(14 * scale)
        return Geometry(dialog, row, after_x, scale, color)

    def folder(self, hwnd: int) -> tuple[str, str] | None:
        """The folder the dialog shows: (path, name)."""
        address = Parts(hwnd).address
        if address is None:
            return None
        return folder_from_address(get_text(address), known_folders())

    def make_folder(self, path: str) -> None:
        try:
            os.mkdir(_long(path))
        except FileExistsError:
            if not os.path.isdir(_long(path)):
                raise

    def go_into(self, hwnd: int, folder: str, timeout: float = 4.0) -> bool:
        """Open ``folder`` in the dialog the way a person would: type it as the file name and press
        «Сохранить» (a folder is opened, nothing is saved), then put back the name they had typed."""
        parts = Parts(hwnd)
        if parts.file_edit is None or parts.save_button is None:
            log.info("save bar: no file name box or Save button in the dialog")
            return False
        typed = get_text(parts.file_edit)
        if not set_text(parts.file_edit, folder.rstrip("\\") + "\\"):
            return False
        user32.PostMessageW(hwnd, WM_COMMAND, IDOK, parts.save_button)
        arrived = False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and user32.IsWindow(hwnd):
            time.sleep(0.05)
            now = self.folder(hwnd)
            if now and _same(now[0], folder):
                arrived = True
                break
        if user32.IsWindow(hwnd):
            edit = Parts(hwnd).file_edit or parts.file_edit
            set_text(edit, typed)
        if not arrived:
            log.warning("save bar: the dialog did not open %s", folder)
        return arrived

    def caret_to_start(self, hwnd: int) -> None:
        """The keyboard in the file name box, the caret before the name (nothing selected)."""
        edit = Parts(hwnd).file_edit
        if edit is None:
            return
        user32.PostMessageW(hwnd, WM_NEXTDLGCTL, edit, 1)  # the dialog moves its focus there (and selects all)
        user32.PostMessageW(edit, EM_SETSEL, 0, 0)
        time.sleep(0.25)
        # a dialog that has just opened a folder may select the whole name once more a moment later; but
        # if the user already types (the caret moved, nothing selected), leave it be
        selection = ctypes.c_size_t()
        if user32.SendMessageTimeoutW(edit, EM_GETSEL, 0, 0, SMTO_ABORTIFHUNG, 300, ctypes.byref(selection)):
            start, end = selection.value & 0xFFFF, (selection.value >> 16) & 0xFFFF
            if start != end:
                user32.PostMessageW(edit, EM_SETSEL, 0, 0)

    def copy(self, text: str) -> None:
        import pyperclip

        pyperclip.copy(text)

    # -- the panel's own window ----------------------------------------------------------------

    def prepare(self, window) -> int:
        """Make the panel a window that never takes the focus from the dialog; returns its handle."""
        window.update_idletasks()
        hwnd = user32.GetParent(window.winfo_id()) or window.winfo_id()
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST)
        return int(hwnd)

    def show(self, hwnd: int, x: int, y: int) -> None:
        user32.SetWindowPos(hwnd, HWND_TOPMOST, x, y, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)

    def hide(self, hwnd: int) -> None:
        user32.ShowWindow(hwnd, SW_HIDE)

    def visible(self, hwnd: int) -> bool:
        return bool(user32.IsWindowVisible(hwnd))

    def focus(self, hwnd: int) -> None:
        user32.SetForegroundWindow(hwnd)
