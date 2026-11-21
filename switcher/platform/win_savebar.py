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

from ..savebar import Geometry, Rect, folder_from_address, ink_end, settled_ink
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
user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.WindowFromPoint.argtypes = (wintypes.POINT,)
user32.WindowFromPoint.restype = wintypes.HWND
user32.ScreenToClient.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.POINT))
kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.VirtualAllocEx.argtypes = (wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD)
kernel32.VirtualAllocEx.restype = ctypes.c_void_p
kernel32.VirtualFreeEx.argtypes = (wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD)
kernel32.ReadProcessMemory.argtypes = (wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                       ctypes.POINTER(ctypes.c_size_t))
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
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
VK_RETURN, VK_ESCAPE, VK_MENU, VK_CONTROL, VK_D, VK_L = 0x0D, 0x1B, 0x12, 0x11, 0x44, 0x4C
MODIFIER_VKS = (0x10, 0x11, 0x12, 0x5B, 0x5C)  # Shift, Ctrl, Alt, both Win keys
CB_SHOWDROPDOWN = 0x014F
TB_BUTTONCOUNT, TB_GETITEMRECT = 0x0418, 0x041D
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP, MK_LBUTTON = 0x0200, 0x0201, 0x0202, 0x0001
PROCESS_VM_OPERATION, PROCESS_VM_READ, PROCESS_QUERY_LIMITED_INFORMATION = 0x0008, 0x0010, 0x1000
MEM_COMMIT, MEM_RESERVE, MEM_RELEASE, PAGE_READWRITE = 0x1000, 0x2000, 0x8000, 0x04
# how the panel opens the address bar for typing, in this order.  Never F4: it also drops the list of
# recent folders down over the dialog (0.7.5, 0.7.6).  Alt+D is the English letter of «Address»: on
# the user's Russian Windows it does nothing (their log), so a click on the bar comes first.
ADDRESS_WAYS = ("click", "Ctrl+L", "Alt+D")
KEYUP, UNICODE = 0x0002, 0x0004  # SendInput: a key let go; a character rather than a key
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
# «Упорядочить», its arrow and padding, and the padding of «Новая папка» after its caption
COMMAND_EXTRA = 12 + 34 + 14
NEW_FOLDER_PAD = 14  # from the end of the «Новая папка» caption to the end of its button

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


def send_message(hwnd, message: int, wparam: int, lparam: int) -> int:
    """SendMessage to a control of another program; never waits on one that hangs."""
    result = ctypes.c_size_t()
    user32.SendMessageTimeoutW(hwnd, message, wparam & 0xFFFFFFFFFFFFFFFF, lparam, SMTO_ABORTIFHUNG, 500,
                               ctypes.byref(result))
    return result.value


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


def toolbar_buttons(toolbar: int) -> list[Rect]:
    """Where a toolbar of another program has its buttons (client pixels): TB_GETITEMRECT writes into
    memory of that program."""
    count = min(send_message(toolbar, TB_BUTTONCOUNT, 0, 0), 64)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(toolbar, ctypes.byref(pid))
    process = kernel32.OpenProcess(PROCESS_VM_OPERATION | PROCESS_VM_READ | PROCESS_QUERY_LIMITED_INFORMATION,
                                   False, pid.value)
    if not count or not process:
        if process:
            kernel32.CloseHandle(process)
        return []
    found: list[Rect] = []
    try:
        remote = kernel32.VirtualAllocEx(process, None, ctypes.sizeof(wintypes.RECT), MEM_COMMIT | MEM_RESERVE,
                                         PAGE_READWRITE)
        if not remote:
            return []
        try:
            for index in range(count):
                if not send_message(toolbar, TB_GETITEMRECT, index, remote):
                    continue
                r, done = wintypes.RECT(), ctypes.c_size_t()
                if kernel32.ReadProcessMemory(process, remote, ctypes.byref(r), ctypes.sizeof(r),
                                              ctypes.byref(done)) and r.right > r.left:
                    found.append((r.left, r.top, r.right, r.bottom))
        finally:
            kernel32.VirtualFreeEx(process, remote, 0, MEM_RELEASE)
    finally:
        kernel32.CloseHandle(process)
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
        self.address = self.band = self.view = self.file_edit = self.save_button = self.items = None
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
            elif cls == "DirectUIHWND" and self.items is None and _class(user32.GetParent(child)) == "SHELLDLL_DefView":
                self.items = child  # the list of files and folders
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
        """A dialog for saving with its file browser and file name box: not «Подтвердить сохранение
        в виде» (it says «сохранение» but is a question over the dialog)."""
        return _class(hwnd) == "#32770" and looks_like_save(hwnd) and has_file_browser(hwnd) \
            and Parts(hwnd).file_edit is not None

    def alive(self, hwnd: int) -> bool:
        return bool(user32.IsWindow(hwnd))

    def dialog_rect(self, hwnd: int) -> Rect:
        return _rect(hwnd)

    def __init__(self):
        self._ink: dict[int, int] = {}  # dialog → where its «Новая папка» ends, from the row's left
        self.last_route = ""  # how go_into opened the folder last (for the log and the Windows check)
        self.address_key = ""  # how the address bar was opened last: one of ADDRESS_WAYS ("" — it was not)

    def geometry(self, hwnd: int, measure: bool = True) -> Geometry | None:
        if not user32.IsWindowVisible(hwnd) or not user32.IsWindowEnabled(hwnd):
            return None  # minimized away, or a message box is open over it
        dialog = _rect(hwnd)
        scale = dialog_scale(hwnd)
        parts = Parts(hwnd)
        row = parts.row()
        if row is None or not 16 * scale <= row[3] - row[1] <= 90 * scale:
            return Geometry(dialog, None, None, scale)
        after_x = row[0] + _command_width(scale)  # where the «Новая папка» button ends
        if measure and hwnd not in self._ink:  # the panel is not over the row: where «Новая папка» really ends
            width = min(row[2] - row[0], round(700 * scale))
            height = max(4, round(12 * scale))
            expected = after_x - row[0] - round(NEW_FOLDER_PAD * scale)  # from the captions' widths
            looks: list[int | None] = []
            for attempt in range(5):  # a slow program draws its dialog bit by bit: look until it agrees
                if attempt:
                    time.sleep(0.15)
                looks.append(ink_end(grab(row[0], (row[1] + row[3] - height) // 2, width, height), width, scale))
                found = settled_ink(looks, expected)
                if found is not None:
                    self._ink[hwnd] = found
                    break
            log.info("save bar: «Новая папка» ends %s px into the row (looked %s; from the captions: %d)",
                     self._ink.get(hwnd, "—"), looks, expected)
        if hwnd in self._ink:
            after_x = row[0] + self._ink[hwnd] + round(NEW_FOLDER_PAD * scale)
        return Geometry(dialog, row, after_x, scale)

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

    def focused(self, hwnd: int) -> int:
        """The control that has the dialog's keyboard."""
        from . import windows

        info = windows.GUITHREADINFO(cbSize=ctypes.sizeof(windows.GUITHREADINFO))
        if not windows.user32.GetGUIThreadInfo(user32.GetWindowThreadProcessId(hwnd, None), ctypes.byref(info)):
            return 0
        return int(info.hwndFocus or 0)

    def give_keyboard(self, hwnd: int, control: int) -> bool:
        """Ask the dialog to move its keyboard to ``control`` and see whether it did.  Only by asking:
        joining another program's input (AttachThreadInput + SetFocus) can hang both programs."""
        user32.PostMessageW(hwnd, WM_NEXTDLGCTL, control, 1)
        for _ in range(12):
            time.sleep(0.05)
            if self.focused(hwnd) == control:
                return True
        log.info("save bar: the dialog did not give the keyboard to %s (it is in %s)", _class(control),
                 _class(self.focused(hwnd)) if self.focused(hwnd) else None)
        return False

    def go_into(self, hwnd: int, folder: str, timeout: float = 4.0) -> bool:
        """Open ``folder`` in the dialog: through its address bar (the file name is not touched, and a
        folder selected in the list does not matter), else put in the file name box and Enter, as 0.7.4
        did (then the name typed before is put back)."""
        parts = Parts(hwnd)
        if parts.file_edit is None:
            log.info("save bar: no file name box in the dialog")
            return False
        target = folder.rstrip("\\") + "\\"
        self.last_route = ""
        # Ctrl+Shift of the panel's own hotkey may still be held: keys sent now would be Ctrl+Shift+…
        keys_free = self._keys_released()
        log.info("save bar: opening %s (keys let go: %s)", folder, keys_free)
        if keys_free and parts.items is not None and self._through_address_bar(hwnd, parts.items, target):
            if self._arrives(hwnd, folder, timeout):
                self.last_route = f"address bar ({self.address_key})"
                return True
            self._leave_address_bar(hwnd)
        typed = get_text(parts.file_edit)
        log.info("save bar: not through the address bar: the file name box next")
        # put there, not typed: typing would drop the box's own list of suggestions down.  (With a
        # folder selected in the list of files, the dialog may open that folder instead.)
        in_box = keys_free and self.give_keyboard(hwnd, parts.file_edit)
        set_text(parts.file_edit, target)
        if in_box and self._in_front(hwnd):
            self._press([VK_RETURN])
            self.last_route = "file name box"
        elif parts.save_button is not None:
            user32.PostMessageW(hwnd, WM_COMMAND, IDOK, parts.save_button)
            self.last_route = "Save button"
        arrived = self._arrives(hwnd, folder, timeout)
        if user32.IsWindow(hwnd):
            set_text(Parts(hwnd).file_edit or parts.file_edit, typed)
        if not arrived:
            log.warning("save bar: the dialog did not open %s (%s)", folder, self.last_route)
            self.last_route += ": did not arrive"
        return arrived

    def _in_front(self, hwnd: int) -> bool:
        return int(user32.GetForegroundWindow() or 0) == hwnd

    @staticmethod
    def _keys_released(timeout: float = 1.5) -> bool:
        """Wait until Ctrl, Shift, Alt and Win are let go."""
        deadline = time.monotonic() + timeout
        while any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in MODIFIER_VKS):
            if time.monotonic() > deadline:
                return False
            time.sleep(0.03)
        return True

    def _address_edit(self, hwnd: int) -> int:
        """The address bar's edit box, when it has the keyboard (the bar is open for typing)."""
        focused = self.focused(hwnd)
        if focused and _class(focused) == "Edit" and user32.IsWindowVisible(focused) and "Address Band Root" in (
                _class(a) for a in _ancestors(focused, hwnd)):
            return focused
        return 0

    def _wait_address_edit(self, hwnd: int, timeout: float) -> int:
        deadline = time.monotonic() + timeout
        edit = 0
        while time.monotonic() < deadline and not edit:
            time.sleep(0.03)
            edit = self._address_edit(hwnd)
        return edit

    def click_address_bar(self, hwnd: int) -> bool:
        """Click the empty part of the address bar, after its last folder, as a person would with the
        mouse (the mouse itself does not move): the bar opens for typing, whatever the language of
        Windows.  False: no empty part found."""
        toolbar = Parts(hwnd).address
        if toolbar is None:
            return False
        left, top, _right, bottom = _rect(toolbar)
        bar_right = _rect(user32.GetParent(toolbar))[2]  # the end of «Breadcrumb Parent»
        buttons = toolbar_buttons(toolbar)
        if not buttons:
            return False
        end = left + max(b[2] for b in buttons)
        if bar_right - end < round(16 * dialog_scale(hwnd)):
            log.info("save bar: address bar: no empty part to click (%s..%s)", end, bar_right)
            return False
        point = wintypes.POINT((end + bar_right) // 2, (top + bottom) // 2)
        target = int(user32.WindowFromPoint(point) or 0)
        if target != toolbar and not (target in _ancestors(toolbar, hwnd)
                                      and _class(target) in ("Breadcrumb Parent", "Address Band Root")):
            log.info("save bar: address bar: something else is at %s,%s (%s)", point.x, point.y, _class(target))
            return False
        user32.ScreenToClient(target, ctypes.byref(point))
        where = (point.y & 0xFFFF) << 16 | (point.x & 0xFFFF)
        for message, flags in ((WM_MOUSEMOVE, 0), (WM_LBUTTONDOWN, MK_LBUTTON), (WM_LBUTTONUP, 0)):
            user32.PostMessageW(target, message, flags, where)
        return True

    def open_address_bar(self, hwnd: int, items: int, ways=ADDRESS_WAYS) -> int:
        """Open the address bar for typing, the first of ``ways`` that does; its edit box, or 0."""
        self.address_key = ""
        for way in ways:
            if not self._in_front(hwnd):
                return 0
            if way == "click":
                if not self.click_address_bar(hwnd):
                    continue
            else:  # from the list of files: in the file name box the keys could end up as text
                self.give_keyboard(hwnd, items)
                if not self._in_front(hwnd):
                    return 0
                self._chord(VK_CONTROL if way == "Ctrl+L" else VK_MENU, VK_L if way == "Ctrl+L" else VK_D)
            edit = self._wait_address_edit(hwnd, 0.6)
            if edit:
                self.address_key = way
                log.info("save bar: address bar: opened by %s", way)
                return edit
            log.info("save bar: address bar: %s did not open it", way)
        return 0

    def _through_address_bar(self, hwnd: int, items: int, text: str) -> bool:
        """Open the address bar for typing, put ``text`` there and press Enter."""
        edit = self.open_address_bar(hwnd, items)
        if not edit or not self._in_front(hwnd):
            log.info("save bar: address bar: it did not open for typing")
            self._leave_address_bar(hwnd)
            return False
        for ancestor in _ancestors(edit, hwnd):  # the list of recent folders, if it is down: with it
            if _class(ancestor) == "ComboBox":   # open, Enter could take a folder from it
                send_message(ancestor, CB_SHOWDROPDOWN, 0, 0)
        if not set_text(edit, text) or self._address_edit(hwnd) != edit:
            log.info("save bar: address bar: could not put the folder there")
            self._leave_address_bar(hwnd)
            return False
        log.info("save bar: address bar: the folder is there, Enter")
        self._press([VK_RETURN])
        return True

    def _leave_address_bar(self, hwnd: int) -> None:
        if self._address_edit(hwnd) and self._in_front(hwnd):
            self._press([VK_ESCAPE])  # only there: anywhere else Esc would close the dialog

    def _arrives(self, hwnd: int, folder: str, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and user32.IsWindow(hwnd):
            time.sleep(0.05)
            now = self.folder(hwnd)
            if now and _same(now[0], folder):
                return True
        return False

    @staticmethod
    def _chord(modifier: int, key: int) -> None:
        from .windows import WindowsBackend

        WindowsBackend._send([(modifier, 0, 0), (key, 0, 0), (key, 0, KEYUP), (modifier, 0, KEYUP)])

    @staticmethod
    def _press(keys: list[int]) -> None:
        from .windows import WindowsBackend

        WindowsBackend._send([event for vk in keys for event in ((vk, 0, 0), (vk, 0, KEYUP))])

    @staticmethod
    def _type(text: str) -> None:
        """Type ``text`` as characters (whatever the keyboard layout), tagged as Switcher's own keys."""
        from .windows import WindowsBackend

        units = text.encode("utf-16-le")
        codes = [int.from_bytes(units[i:i + 2], "little") for i in range(0, len(units), 2)]
        events = [event for code in codes for event in ((0, code, UNICODE), (0, code, UNICODE | KEYUP))]
        for start in range(0, len(events), 200):
            WindowsBackend._send(events[start:start + 200])

    def caret_to_start(self, hwnd: int) -> None:
        """The keyboard in the file name box, the caret before the name (nothing selected)."""
        edit = Parts(hwnd).file_edit
        if edit is None:
            return
        self.give_keyboard(hwnd, edit)  # (the dialog may select the whole name when the box gets the keyboard)
        user32.PostMessageW(edit, EM_SETSEL, 0, 0)
        time.sleep(0.25)
        # a dialog that has just opened a folder may select the whole name once more a moment later; but
        # if the user already types (the caret moved, nothing selected), leave it be
        selection = ctypes.c_size_t()
        if user32.SendMessageTimeoutW(edit, EM_GETSEL, 0, 0, SMTO_ABORTIFHUNG, 300, ctypes.byref(selection)):
            start, end = selection.value & 0xFFFF, (selection.value >> 16) & 0xFFFF
            if start != end:
                user32.PostMessageW(edit, EM_SETSEL, 0, 0)

    def new_folder(self, hwnd: int) -> bool:
        """The dialog's own «Новая папка» (Ctrl+Shift+N in its list of files): it makes the folder and
        lets the user type its name."""
        from .windows import WindowsBackend

        items = Parts(hwnd).items
        if items is None:
            return False
        self.give_keyboard(hwnd, items)  # the keyboard in the list of files
        if int(user32.GetForegroundWindow() or 0) != hwnd:
            return False
        ctrl, shift, n, up = 0x11, 0x10, 0x4E, 0x0002
        WindowsBackend._send([(ctrl, 0, 0), (shift, 0, 0), (n, 0, 0), (n, 0, up), (shift, 0, up), (ctrl, 0, up)])
        return True

    def subfolders(self, path: str) -> set[str]:
        try:
            return {entry.name for entry in os.scandir(_long(path)) if entry.is_dir()}
        except OSError:
            return set()

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
