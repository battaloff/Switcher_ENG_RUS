"""End-to-end check of the Windows backend in Notepad.

    python tools/windows_smoke.py

Types into Notepad the way a user does (physical keys, the active layout
decides the letters) while the real App runs: hooks, WinAPI layout switching,
text injection.  Then reads Notepad's text back and compares.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import tempfile
import threading
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["SWITCHER_ACCEPT_INJECTED"] = "1"  # our synthetic "user" input is flagged as injected
os.environ.setdefault("SWITCHER_HOME", tempfile.mkdtemp(prefix="switcher-winsmoke-"))

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = (WNDENUMPROC, wintypes.LPARAM)
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.FindWindowExW.argtypes = (wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR)
user32.FindWindowExW.restype = wintypes.HWND
user32.SendMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, ctypes.c_void_p)
user32.SendMessageW.restype = ctypes.c_ssize_t
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
user32.BringWindowToTop.argtypes = (wintypes.HWND,)
user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
user32.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT)
user32.AttachThreadInput.argtypes = (wintypes.DWORD, wintypes.DWORD, wintypes.BOOL)
user32.LoadKeyboardLayoutW.argtypes = (wintypes.LPCWSTR, wintypes.UINT)
user32.LoadKeyboardLayoutW.restype = ctypes.c_void_p
WM_SETTEXT, WM_GETTEXT, EM_SETSEL = 0x000C, 0x000D, 0x00B1
ON_CI = os.environ.get("GITHUB_ACTIONS") == "true"


def _escape(text: str, limit: int = 4000) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")[:limit]


class _Annotated:
    """On CI every "FAIL" line also becomes an error annotation: readable without the full log."""

    def __init__(self, out):
        self.out, self.pending = out, ""

    def write(self, text):
        self.out.write(text)
        self.pending += text
        while "\n" in self.pending:
            line, self.pending = self.pending.split("\n", 1)
            if line.startswith("FAIL"):
                self.out.write(f"::error title=Notepad E2E::{_escape(line)}\n")
        return len(text)

    def flush(self):
        self.out.flush()


if ON_CI:
    sys.stdout = _Annotated(sys.stdout)


def note(title: str, text: str) -> None:
    """Details worth reading even when everything passed (an annotation on CI)."""
    print(f"   {title}: {text}")
    if ON_CI:
        print(f"::notice title={title}::{_escape(text)}")


def class_name(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def find_window(pid: int, timeout: float = 20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        found = []

        def callback(hwnd, _):
            owner = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == pid and class_name(hwnd) == "Notepad":
                found.append(hwnd)
            return True

        user32.EnumWindows(WNDENUMPROC(callback), 0)
        if found:
            return found[0]
        time.sleep(0.3)
    return None


def find_edit(hwnd, timeout: float = 20.0):
    """Notepad's text field: it is created a moment after the window itself."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        edit = user32.FindWindowExW(hwnd, None, "Edit", None) or user32.FindWindowExW(hwnd, None, "RichEditD2DPT", None)
        if edit:
            return edit
        time.sleep(0.3)
    return None


def bring_to_front(hwnd) -> bool:
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    if user32.SetForegroundWindow(hwnd) and user32.GetForegroundWindow() == hwnd:
        return True
    foreground = user32.GetForegroundWindow()
    theirs = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
    ours = kernel32.GetCurrentThreadId()
    if theirs:
        user32.AttachThreadInput(ours, theirs, True)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    if theirs:
        user32.AttachThreadInput(ours, theirs, False)
    return user32.GetForegroundWindow() == hwnd


def check_save_bar(app, dialog, user, Key, KeyCode, results):
    """The «Сохранить как» panel on a real Save As dialog: shown inside it without taking the focus,
    Ctrl+Shift+1 copies the order's name and opens its "PDF" folder, the typed file name stays."""
    import tkinter as tk

    import pyperclip

    from switcher.platform import win_savebar
    from switcher.savebar_ui import SaveBarPanel

    Parts, get_text, color_at = win_savebar.Parts, win_savebar.get_text, win_savebar.color_at
    dialogs = win_savebar.WindowsSaveDialogs()
    # the long name of the folder: on the runner TEMP is a short 8.3 one (RUNNER~1), the dialog shows the long one
    order = Path(os.path.realpath(os.environ["SWITCHER_HOME"])) / "заказы" / "ayk для s.raximov   (id_57306)"
    order.mkdir(parents=True, exist_ok=True)
    pdf = order / "PDF"

    def same(a, b) -> bool:
        return os.path.normcase(os.path.realpath(str(a))) == os.path.normcase(os.path.realpath(str(b)))

    def file_name() -> str | None:
        edit = Parts(dialog).file_edit
        return get_text(edit) if edit else None

    typed = file_name()
    note("Save bar", f"address {get_text(Parts(dialog).address) if Parts(dialog).address else None!r}; "
                     f"file name {typed!r}")
    moved = dialogs.go_into(dialog, str(order))
    note("Save bar", f"the dialog opened the folder by the {dialogs.last_route}")
    folder = dialogs.folder(dialog)
    kept = file_name()
    ok = moved and folder is not None and same(folder[0], order) and kept == typed and bool(typed)
    print(f"{'OK  ' if ok else 'FAIL'} the save dialog opens a folder and keeps the file name: moved {moved}, "
          f"folder {folder!r}, name {typed!r} → {kept!r}")
    results.append(ok)

    box: dict = {}

    def run_panel():
        root = tk.Tk()
        root.withdraw()
        box["panel"] = SaveBarPanel(root, app)
        box["panel"].start()
        root.mainloop()

    threading.Thread(target=run_panel, name="save-bar-check", daemon=True).start()
    deadline = time.time() + 10
    while time.time() < deadline and not (box.get("panel") and box["panel"].dialog == dialog
                                          and box["panel"].position):
        time.sleep(0.1)
    panel = box.get("panel")
    geometry = dialogs.geometry(dialog, measure=False)  # the panel may be over the row now
    shown = panel is not None and panel.dialog == dialog and dialogs.visible(panel.hwnd)
    front = user32.GetForegroundWindow()
    note("Save bar", f"dialog {geometry.dialog if geometry else None}, row {geometry.row if geometry else None}, "
                     f"after {geometry.after_x if geometry else None}, scale {geometry.scale if geometry else None}, "
                     f"panel at {panel.position if panel else None}, "
                     f"size {panel.size if panel else None}, {panel.where if panel else '-'}")
    ok = shown and front == dialog
    print(f"{'OK  ' if ok else 'FAIL'} the panel shows on the save dialog ({panel.where if panel else '-'}) and the "
          f"dialog keeps the focus (in front: {class_name(front) if front else None!r})")
    results.append(ok)
    if shown:
        time.sleep(0.5)
        x, y = panel.position[0] + 10, panel.position[1] + panel.size[1] // 2
        color = color_at(x, y)
        yellow = bool(color) and int(color[1:3], 16) > 200 and int(color[3:5], 16) > 150 and int(color[5:7], 16) < 120
        print(f"{'OK  ' if yellow else 'FAIL'} the panel is drawn: {color} at its main (yellow) button")
        results.append(yellow)
        fits = geometry is not None and geometry.row is not None and \
            geometry.after_x + panel.size[0] <= geometry.row[2] - round(110 * geometry.scale)
        placed = panel.where == ("inside" if fits else "above")
        print(f"{'OK  ' if placed else 'FAIL'} the panel is {panel.where} (fits in the row: {fits})")
        results.append(placed)
        # a wide dialog: the panel moves into the row of «Новая папка», right after it
        left, top = geometry.dialog[0], geometry.dialog[1]
        user32.SetWindowPos(dialog, None, left, top, 1250, 720, 0x0004 | 0x0010)  # NOZORDER | NOACTIVATE
        deadline = time.time() + 4
        while time.time() < deadline and panel.where != "inside":
            time.sleep(0.1)
        time.sleep(0.5)
        geometry = dialogs.geometry(dialog, measure=False)
        ink = panel.system._ink.get(dialog)  # measured by the panel before it showed
        inside = panel.where == "inside" and geometry.row is not None and panel.position is not None and \
            geometry.row[1] <= panel.position[1] and panel.position[1] + panel.size[1] <= geometry.row[3]
        gap = (14 + app.config.save_bar.gap) * geometry.scale  # the «Новая папка» button's padding + the gap
        after = ink is None or gap - 3 <= panel.position[0] - (geometry.row[0] + ink) <= gap + 3
        note("Save bar", f"wide dialog {geometry.dialog}, row {geometry.row}; «Новая папка» ends at "
                         f"{geometry.row[0] + ink if ink is not None else None}; panel at {panel.position}")
        print(f"{'OK  ' if inside and after else 'FAIL'} in a wide dialog the panel sits in the row right after "
              f"«Новая папка»: {panel.where} at {panel.position}, the text ends at "
              f"{geometry.row[0] + ink if ink is not None else 'not seen'}")
        results.append(inside and after and front == user32.GetForegroundWindow())
        if inside and panel.gap_points:
            y = panel.position[1] + panel.size[1] // 2
            gap = color_at(panel.position[0] + panel.gap_points[0], y)
            row = color_at(panel.position[0] + panel.size[0] + round(30 * geometry.scale), y)

            def differ(a, b) -> int:
                return sum(abs(int(a[i:i + 2], 16) - int(b[i:i + 2], 16)) for i in (1, 3, 5))

            key, alpha, flags = wintypes.DWORD(), ctypes.c_ubyte(), wintypes.DWORD()
            layered = bool(user32.GetLayeredWindowAttributes(panel.hwnd, ctypes.byref(key), ctypes.byref(alpha),
                                                             ctypes.byref(flags))) and bool(flags.value & 1)
            clear = layered and bool(gap and row) and differ(gap, row) < 30
            print(f"{'OK  ' if clear else 'FAIL'} between the buttons the dialog shows through: see-through colour "
                  f"set {layered} (key {key.value:06x}), gap {gap} at {panel.gap_points[0]}, the row beside the "
                  f"panel {row}")
            results.append(clear)

    def hotkey(digit: str) -> None:
        user.press(Key.ctrl)
        user.press(Key.shift)
        user.press(KeyCode.from_vk(0x30 + int(digit)))
        user.release(KeyCode.from_vk(0x30 + int(digit)))
        user.release(Key.shift)
        user.release(Key.ctrl)

    pyperclip.copy("")
    hotkey("1")
    deadline = time.time() + 6
    while time.time() < deadline and not ((now := dialogs.folder(dialog)) and same(now[0], pdf)):
        time.sleep(0.1)
    time.sleep(0.3)
    copied, now, kept = pyperclip.paste(), dialogs.folder(dialog), file_name()
    ok = copied == order.name and pdf.is_dir() and now is not None and same(now[0], pdf) and kept == typed
    print(f"{'OK  ' if ok else 'FAIL'} Ctrl+Shift+1 copies the folder's name and opens «PDF»: copied {copied!r}, "
          f"PDF made {pdf.is_dir()}, dialog in {now!r}, file name {kept!r}, by the {panel.system.last_route}")
    results.append(ok)

    from switcher.platform.windows import GUITHREADINFO

    def caret():
        edit = Parts(dialog).file_edit
        found = win_savebar.send_message(edit, 0x00B0, 0, 0) if edit else -1  # EM_GETSEL
        info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
        user32.GetGUIThreadInfo(user32.GetWindowThreadProcessId(dialog, None), ctypes.byref(info))
        return (found & 0xFFFF, (found >> 16) & 0xFFFF), bool(edit) and info.hwndFocus == edit

    time.sleep(0.5)
    where, focused = caret()
    ok = where == (0, 0) and focused
    print(f"{'OK  ' if ok else 'FAIL'} after the button the caret is at the start of the file name: "
          f"selection {where}, keyboard in the file name box {focused}")
    results.append(ok)
    pyperclip.copy("")
    hotkey("2")
    deadline = time.time() + 3
    while time.time() < deadline and pyperclip.paste() == "":
        time.sleep(0.1)
    copied = pyperclip.paste()
    ok = copied == order.name + " PANCH" and not (pdf / "PDF").exists()
    print(f"{'OK  ' if ok else 'FAIL'} Ctrl+Shift+2 inside «PDF»: copied {copied!r}, no PDF inside PDF: "
          f"{not (pdf / 'PDF').exists()}")
    results.append(ok)

    # a button edited while the dialog is open, the way the settings page does it: shown at once
    import copy

    edited = copy.deepcopy(app.config)
    edited.save_bar.buttons[1].update(label="PANCH_", suffix=" PANCH_")
    app.update_config(edited, save=False)
    deadline = time.time() + 6
    while time.time() < deadline and not (len(panel.buttons) > 1 and panel.buttons[1].label == "PANCH_"
                                          and panel.position and dialogs.visible(panel.hwnd)):
        time.sleep(0.1)
    time.sleep(0.5)
    shown = len(panel.buttons) > 1 and panel.buttons[1].label == "PANCH_" and bool(panel.position) and \
        dialogs.visible(panel.hwnd) and panel.dialog == dialog
    color = color_at(panel.position[0] + 10, panel.position[1] + panel.size[1] // 2) if panel.position else None
    yellow = bool(color) and int(color[1:3], 16) > 200 and int(color[3:5], 16) > 150 and int(color[5:7], 16) < 120
    print(f"{'OK  ' if shown and yellow else 'FAIL'} an edited button shows at once: buttons "
          f"{[b.label for b in panel.buttons]}, panel at {panel.position}, visible {dialogs.visible(panel.hwnd)}, "
          f"colour {color}")
    results.append(shown and yellow)
    pyperclip.copy("")
    hotkey("2")
    deadline = time.time() + 3
    while time.time() < deadline and pyperclip.paste() == "":
        time.sleep(0.1)
    copied = pyperclip.paste()
    ok = copied == order.name + " PANCH_"
    print(f"{'OK  ' if ok else 'FAIL'} the edited button copies its new text: {copied!r}")
    results.append(ok)

    # the panel's own «Новая папка»: the dialog makes a folder, ready for its name to be typed
    from switcher.savebar import NEW_FOLDER, new_folder_button

    edited = copy.deepcopy(app.config)
    edited.save_bar.buttons.append(new_folder_button().to_dict())  # Ctrl+Shift+5
    app.update_config(edited, save=False)
    deadline = time.time() + 6
    while time.time() < deadline and not (any(b.action == NEW_FOLDER for b in panel.buttons) and panel.position):
        time.sleep(0.1)
    time.sleep(0.5)
    before = {p.name for p in pdf.iterdir() if p.is_dir()}
    hotkey("5")
    made: set = set()
    deadline = time.time() + 6
    while time.time() < deadline and not made:
        time.sleep(0.2)
        made = {p.name for p in pdf.iterdir() if p.is_dir()} - before
    print(f"{'OK  ' if made else 'FAIL'} the panel's «Новая папка» makes a folder in the dialog: {sorted(made)}")
    results.append(bool(made))
    time.sleep(0.5)
    user.press(Key.esc)  # keep the name it got
    user.release(Key.esc)
    time.sleep(0.5)

    # «Подтвердить сохранение в виде» over the dialog: the panel goes away while it is open, and the
    # question is not taken for a save dialog (its title says «сохранение»)
    existing = pdf / "exists.txt"
    existing.write_text("x", encoding="utf-8")
    dialogs.go_into(dialog, str(order))  # in and out again: nothing stays selected in the list
    dialogs.go_into(dialog, str(pdf))
    time.sleep(0.5)
    name_box = Parts(dialog).file_edit
    in_box = dialogs.give_keyboard(dialog, name_box)  # the keyboard in the file name box, as a click would
    win_savebar.send_message(name_box, 0x00B1, 0, -1)  # select the whole name: typing replaces it
    dialogs._type(existing.name)
    note("Save bar", f"before saving over a file: the file name box has the keyboard {in_box}, "
                     f"the dialog in {dialogs.folder(dialog)!r} ({dialogs.last_route})")
    user.press(Key.enter)  # «Сохранить»
    user.release(Key.enter)
    deadline = time.time() + 5
    question = 0
    while time.time() < deadline:
        front = int(user32.GetForegroundWindow() or 0)
        if front and front != dialog and class_name(front) == "#32770":
            question = front
            break
        time.sleep(0.1)
    time.sleep(1.0)
    hidden = bool(question) and panel.dialog == 0 and not dialogs.visible(panel.hwnd)
    taken = bool(question) and dialogs.is_save_dialog(question)
    title = ctypes.create_unicode_buffer(256)
    if question:
        user32.GetWindowTextW(question, title, 256)
    print(f"{'OK  ' if hidden and not taken else 'FAIL'} a question over the dialog ({title.value!r}): the panel "
          f"is hidden {hidden}, the question taken for a save dialog {taken}")
    results.append(hidden and not taken)
    if question:
        user.press(Key.esc)  # «Нет»: nothing is replaced
        user.release(Key.esc)
    deadline = time.time() + 4
    while time.time() < deadline and not (panel.dialog == dialog and dialogs.visible(panel.hwnd)):
        time.sleep(0.1)
    back = panel.dialog == dialog and dialogs.visible(panel.hwnd)
    print(f"{'OK  ' if back else 'FAIL'} the panel comes back when the question is answered")
    results.append(back)

    # a folder selected in the list of files: the button still opens «PDF», not that folder
    (order / "другая папка").mkdir(exist_ok=True)
    dialogs.go_into(dialog, str(order))
    items = Parts(dialog).items
    in_list = bool(items) and dialogs.give_keyboard(dialog, items)  # the keyboard in the list
    if in_list:
        user.press(Key.end)  # selects the last item: «другая папка»
        user.release(Key.end)
        time.sleep(0.4)
    note("Save bar", f"a folder selected in the list: the list has the keyboard {in_list}")
    hotkey("1")
    deadline = time.time() + 6
    while time.time() < deadline and not ((now := dialogs.folder(dialog)) and same(now[0], pdf)):
        time.sleep(0.1)
    now = dialogs.folder(dialog)
    ok = bool(items) and now is not None and same(now[0], pdf)
    print(f"{'OK  ' if ok else 'FAIL'} with a folder selected in the list the button still opens «PDF»: "
          f"dialog in {now!r}, by the {panel.system.last_route}")
    results.append(ok)
    return panel


HANG_AFTER = 6 * 60  # seconds: the whole check takes two or three minutes; ci_notepad_check.ps1 stops it at 8


def report_hang() -> None:
    """Where every thread stands, as an annotation, then stop: a hung step leaves nothing to read."""
    import traceback

    names = {thread.ident: thread.name for thread in threading.enumerate()}
    stacks = [f"thread {names.get(ident, ident)}:\n" + "".join(traceback.format_stack(frame)[-10:])
              for ident, frame in sys._current_frames().items()]
    print(f"FAIL the check hung for {HANG_AFTER // 60} minutes; where the threads stand is in the next annotation")
    sys.stdout.write(f"::error title=Notepad E2E hung::{_escape(chr(10).join(stacks), 30000)}\n")
    sys.stdout.flush()
    os._exit(1)


def main() -> int:
    watchdog = threading.Timer(HANG_AFTER, report_hang)
    watchdog.daemon = True
    watchdog.start()
    for klid in ("00000409", "00000419"):  # make sure both layouts are loaded
        user32.LoadKeyboardLayoutW(klid, 0)
    notepad = subprocess.Popen(["notepad.exe"])
    hwnd = find_window(notepad.pid)
    if not hwnd:
        print("FAIL: Notepad window not found")
        return 1
    edit = find_edit(hwnd)
    print(f"Notepad hwnd={hwnd} edit={edit} ({class_name(edit) if edit else '-'})")
    if not edit:
        print("FAIL: Notepad's text field not found")
        return 1
    if not bring_to_front(hwnd):
        fg = user32.GetForegroundWindow()
        print(f"WARN: could not focus Notepad; foreground is {class_name(fg) if fg else None!r}")

    from pynput.keyboard import Controller, Key, KeyCode

    from switcher.app import App
    from switcher.config import Config
    from switcher.layouts import EN, RU
    from switcher.platform.windows import _VK

    config = Config()
    config.ai.enabled = False
    config.snippets = {"015": "015-510-400_4_", "525": "525-459_4_"}
    import logging

    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="   log: %(name)s: %(message)s")
    app = App(config)
    runner = threading.Thread(target=app.run, kwargs={"tray": False}, daemon=True)
    runner.start()
    time.sleep(2.0)
    user = Controller()
    vk_of = {code: vk for vk, code in _VK.items()}
    print("installed layouts:", sorted(app.backend._hkls))

    def screen() -> str:
        buf = ctypes.create_unicode_buffer(8192)
        user32.SendMessageW(edit, WM_GETTEXT, 8192, ctypes.addressof(buf))
        return buf.value

    def clear() -> None:
        empty = ctypes.create_unicode_buffer("")
        user32.SendMessageW(edit, WM_SETTEXT, 0, ctypes.addressof(empty))
        app.post(lambda: app.controller.reset("test"))
        time.sleep(0.3)

    def tap(key) -> None:
        user.press(key)
        user.release(key)

    def type_keys(physical: str) -> None:
        for ch in physical:
            if ch == " ":
                tap(Key.space)
            elif ch == "\b":
                tap(Key.backspace)
            else:
                tap(KeyCode.from_vk(vk_of[ch]))
            time.sleep(0.12)
        time.sleep(1.2)

    def layout(lang: str) -> None:
        app.backend.set_layout(lang)
        time.sleep(0.6)
        print(f"   layout requested {lang}, Notepad now {app.backend.current_layout()}")

    results = []

    def check(label: str, expected: str) -> None:
        got = screen()
        ok = got == expected
        results.append(ok)
        c = app.controller
        tracked = "" if ok else (f"; tracked {[(t.text, t.lang, t.change) for t in c.history]}, "
                                 f"cur {c.cur.typed_text if c.cur else None!r}")
        fg = user32.GetForegroundWindow()
        print(f"{'OK  ' if ok else 'FAIL'} {label}: {got!r} (expected {expected!r}); "
              f"layout {app.backend.current_layout()}, app {app.backend.active_app()!r}, "
              f"in front {class_name(fg) if fg else None!r}{tracked}")

    layout(EN)
    type_keys("ghbdtn ")
    check("Russian word typed on the English layout", "привет ")
    type_keys("rfr ltkf ")
    check("the rest is typed in Russian already", "привет как дела ")

    clear()
    layout(RU)
    type_keys("hello ")
    check("English word typed on the Russian layout", "hello ")

    clear()
    layout(EN)
    type_keys("e vtyz ")
    check("short word fixed together with the next one", "у меня ")

    clear()
    layout(EN)
    type_keys("ghbd")
    check("switched after the first letters, like Punto", "прив")
    results.append(app.backend.current_layout() == RU)
    type_keys("tn ")
    check("the rest of the word came out in Russian", "привет ")

    clear()
    layout(RU)
    type_keys("ghtdtn ")  # "превет" on the Russian layout
    check("typo fixed when the word ends", "привет ")

    clear()
    layout(RU)
    user.press(Key.shift)  # the English quote key, Shift+': "Э" on the Russian layout
    tap(KeyCode.from_vk(vk_of["'"]))
    user.release(Key.shift)
    time.sleep(0.12)
    type_keys(";len ")
    check("a quote typed with the English key stays a quote", '"ждут ')

    # (early on: later the runner's console tends to take the focus)
    def focus_notepad() -> None:
        ok = bring_to_front(hwnd)
        fg = user32.GetForegroundWindow()
        print(f"   Notepad in front: {ok} (foreground {class_name(fg) if fg else None!r})")
        time.sleep(0.5)

    # snippets: by default only file names are completed — "015" typed in the text stays "015"
    focus_notepad()
    clear()
    layout(EN)
    type_keys("015 ")
    check("no snippet outside save and export dialogs", "015 ")

    # completing everywhere (the user's choice): "015" is completed at once
    app.config.snippets_only_in_save_dialogs = False
    clear()
    type_keys("015")
    check("a snippet is completed as it is typed", "015-510-400_4_")

    # the same on the numeric keypad, on the Russian layout
    focus_notepad()
    clear()
    layout(RU)
    for vk in (0x65, 0x62, 0x65):  # NumPad 5, 2, 5
        tap(KeyCode.from_vk(vk))
        time.sleep(0.12)
    time.sleep(1.2)
    check("a snippet typed on the numeric keypad", "525-459_4_")
    app.config.snippets_only_in_save_dialogs = True  # back to the default: Save As below completes the name

    # AutoHotkey: scripts run from Switcher's manager, and what they type is not Switcher's to fix
    ahk_script = Path(os.environ["SWITCHER_HOME"]) / "smoke.ahk"
    ahk_found = app.ahk.interpreters()
    if ahk_found:
        note("AutoHotkey", f"{app.ahk.describe_install()}; {ahk_found}")
        ahk_script.write_text("#Requires AutoHotkey v2.0\n#SingleInstance Force\n::zzq::готово\n", encoding="utf-8-sig")
        error = app.ahk.start(str(ahk_script))
        deadline = time.time() + 10
        while not app.ahk.is_running(str(ahk_script)) and time.time() < deadline:
            time.sleep(0.3)
        started = error is None and app.ahk.is_running(str(ahk_script))
        print(f"{'OK  ' if started else 'FAIL'} a script started from Switcher runs: error {error!r}; "
              f"running {app.ahk.running(fresh=True)}")
        results.append(started)
        time.sleep(1)
        focus_notepad()
        clear()
        layout(EN)
        type_keys("zzq ")
        check("an AutoHotkey hotstring types its text", "готово ")
        type_keys("ghbdtn ")
        check("Switcher goes on right after AutoHotkey typed", "готово привет ")
        note("AutoHotkey keys", f"dropped injected {app.backend.injected_dropped}, "
                                f"accept injected {app.backend.accept_injected}")
        # a hotkey moved to other keys, as the "Клавиши AHK" settings page does it
        from switcher.ahk import read_script, write_script
        from switcher.ahk_hotkeys import ahk_label, list_bindings, relabel

        keyed = Path(os.environ["SWITCHER_HOME"]) / "keys.ahk"
        keyed.write_text('#Requires AutoHotkey v2.0\n#SingleInstance Force\n^!j::SendText "клавиша"\n',
                         encoding="utf-8-sig")
        app.ahk.start(str(keyed))
        deadline = time.time() + 10
        while not app.ahk.is_running(str(keyed)) and time.time() < deadline:
            time.sleep(0.3)
        text, encoding, newline = read_script(keyed)
        binding = list_bindings(str(keyed), text)[0]
        # letters: F10 would open the program's menu when it is let go
        write_script(keyed, relabel(text, binding.line, binding.written, ahk_label("<ctrl>+<alt>+k")),
                     encoding, newline)
        moved = app.ahk.reload(str(keyed))
        time.sleep(2.5)
        focus_notepad()
        clear()
        user.press(Key.ctrl)
        user.press(Key.alt)
        tap(KeyCode.from_vk(vk_of["k"]))
        # held until the script has typed: AutoHotkey lets Ctrl and Alt go while it types and puts them
        # back down after, so letting go of them earlier would leave them stuck
        time.sleep(1.5)
        user.release(Key.alt)
        user.release(Key.ctrl)
        time.sleep(0.5)
        held = [name for name, vk in (("Ctrl", 0x11), ("Alt", 0x12), ("Shift", 0x10))
                if user32.GetAsyncKeyState(vk) & 0x8000]
        note("AutoHotkey keys", f"moved: {moved!r}; script now {read_script(keyed)[0].splitlines()[2]!r}; "
                                f"held after: {held}")
        check("a hotkey moved to Ctrl+Alt+K from Switcher works there", "клавиша")
        app.ahk.stop(str(keyed))
        for key in (Key.alt, Key.ctrl, Key.shift):  # nothing may stay down for the checks after this one
            user.release(key)
        time.sleep(0.3)
        reloaded = app.ahk.reload(str(ahk_script))
        time.sleep(2)
        running_after_reload = app.ahk.is_running(str(ahk_script))
        stopped = app.ahk.stop(str(ahk_script))
        deadline = time.time() + 10
        while app.ahk.is_running(str(ahk_script)) and time.time() < deadline:
            time.sleep(0.3)
        ok = reloaded is None and running_after_reload and stopped is None and not app.ahk.is_running(str(ahk_script))
        print(f"{'OK  ' if ok else 'FAIL'} reload and stop from Switcher: reload {reloaded!r}, running after "
              f"{running_after_reload}, stop {stopped!r}, running now {app.ahk.running(fresh=True)}")
        results.append(ok)
    elif os.environ.get("SWITCHER_EXPECT_AHK") == "1":
        print("FAIL AutoHotkey was installed but Switcher did not find it: "
              f"roots {app.ahk.system.install_roots()}")
        results.append(False)
    else:
        print("SKIP AutoHotkey is not installed")

    # "Save As" opens on the Russian layout: the file name is typed in English
    focus_notepad()
    clear()
    layout(RU)
    user.press(Key.ctrl)
    tap(KeyCode.from_vk(vk_of["s"]))
    user.release(Key.ctrl)
    asked = time.time()
    time.sleep(1)
    while time.time() - asked < 8 and app.backend.current_layout() != EN:  # a slow runner: give it time
        time.sleep(0.2)
    waited = time.time() - asked
    fg = user32.GetForegroundWindow()
    lang = app.backend.current_layout()
    saved = class_name(fg) == "#32770" and lang == EN
    from switcher.platform.dialogs import is_save_dialog
    from switcher.platform.win_events import button_texts

    buttons = button_texts(fg) if fg else []
    by_button = is_save_dialog("", buttons)
    note("Save As", f"buttons {buttons[:6]}; known by its button alone: {by_button}")
    results.append(by_button)
    print(f"{'OK  ' if saved else 'FAIL'} Save As switched to English: window {class_name(fg)!r}, "
          f"layout {lang} after {waited:.1f} s; Switcher thinks "
          f"{app.controller.layout}, save dialog in front: {app.backend.in_save_dialog()}")
    results.append(saved)
    # the file name typed there with a snippet, as it is meant to be used
    from switcher.platform.windows import GUITHREADINFO

    type_keys("015")
    time.sleep(1)
    info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
    user32.GetGUIThreadInfo(user32.GetWindowThreadProcessId(fg, None), ctypes.byref(info))
    buf = ctypes.create_unicode_buffer(512)
    user32.SendMessageW(info.hwndFocus, WM_GETTEXT, 512, ctypes.addressof(buf))
    named = buf.value == "015-510-400_4_"
    print(f"{'OK  ' if named else 'FAIL'} a snippet in the file name field: {buf.value!r}")
    results.append(named)
    panel = None
    try:
        panel = check_save_bar(app, fg, user, Key, KeyCode, results)
    except Exception as exc:  # noqa: BLE001  (any failure here is a failed check, not the end of the run)
        import traceback

        print(f"FAIL the «Сохранить как» panel: {exc!r}\n{traceback.format_exc()}")
        results.append(False)
    tap(Key.esc)
    time.sleep(1)
    if panel is not None:
        deadline = time.time() + 3
        while time.time() < deadline and (panel.dialog or panel.system.visible(panel.hwnd)):
            time.sleep(0.1)
        gone = not panel.dialog and not panel.system.visible(panel.hwnd)
        print(f"{'OK  ' if gone else 'FAIL'} the panel goes away with the dialog")
        results.append(gone)


    clear()
    layout(EN)
    type_keys("ghbdtn ")
    for _ in range(2):
        tap(Key.shift)
        time.sleep(0.06)
    time.sleep(1.2)
    check("double Shift undoes the switch", "ghbdtn ")
    rule = app.profile.layout_rule("ghbdtn", app.backend.active_app())
    print(f"{'OK  ' if rule else 'FAIL'} rule learned from the undo: {rule}")
    results.append(bool(rule))

    def select(text: str) -> None:
        clear()
        buf = ctypes.create_unicode_buffer(text)
        user32.SendMessageW(edit, WM_SETTEXT, 0, ctypes.addressof(buf))
        user32.SendMessageW(edit, EM_SETSEL, 0, len(text))
        time.sleep(0.3)

    def shift_pause() -> None:
        user.press(Key.shift)  # Switcher must wait for Shift to be let go before its own Ctrl+C
        tap(Key.pause)
        time.sleep(0.15)
        user.release(Key.shift)
        time.sleep(2.0)

    layout(EN)
    select("vfibyf? rfr ltkf")
    shift_pause()
    check("Shift+Pause swaps the layout of the selected text", "машина, как дела")
    shift_pause()  # the result is still selected, as in Punto: pressed again, it goes back
    check("a second Shift+Pause brings the text back", "vfibyf? rfr ltkf")
    select("ыршае=зфгыу")
    shift_pause()
    check("Shift+Pause swaps text with \"=\" in it", "shift=pause")
    layout(EN)
    select("shift+pause")
    shift_pause()
    check("Shift+Pause swaps right text as well, no questions asked", "ыршае+зфгыу")

    # Windows silently removes a hook that once answers too slowly; do the same and expect a recovery
    from pynput._util.win32 import SystemHook

    watchdog = app.backend._watchdog
    print(f"{'OK  ' if watchdog and watchdog.running else 'FAIL'} hook watchdog is running")
    results.append(bool(watchdog and watchdog.running))
    hook = SystemHook._HOOKS[app.backend._listeners[0].ident]
    user32.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)
    removed = user32.UnhookWindowsHookEx(hook._hook)
    print(f"   keyboard hook removed behind Switcher's back: {bool(removed)}")
    clear()
    layout(EN)
    type_keys("asdfasdf")  # only Raw Input hears these now (a second of silence, then 8 raw events)
    time.sleep(1.0)
    print(f"{'OK  ' if watchdog.restarts else 'FAIL'} watchdog noticed and reinstalled the hook: "
          f"{watchdog.restarts} time(s)")
    results.append(watchdog.restarts >= 1)
    clear()
    layout(EN)
    type_keys("rfr ltkf ")  # not "ghbdtn": the undo above taught to keep that one as typed
    check("keys are heard again after Windows dropped the hook", "как дела ")

    # the keyboard listener thread dies: the health check starts a new one
    app.backend._listeners[0].stop()
    time.sleep(7)
    clear()
    layout(EN)
    type_keys("rfr ltkf ")
    check("keys are heard again after the listener died", "как дела ")
    print(f"   health recoveries so far: {app.recoveries}")

    # the engine thread hangs on one item: a new one takes over
    import switcher.app as app_module

    app_module.STUCK_AFTER = 3.0
    release = threading.Event()
    app.post(release.wait)
    time.sleep(10)
    clear()
    layout(EN)
    type_keys("rfr ltkf ")
    check("keys are handled again after the engine thread hung", "как дела ")
    release.set()
    results.append(app.recoveries >= 2)
    print(f"{'OK  ' if app.recoveries >= 2 else 'FAIL'} health recoveries: {app.recoveries}")

    # back at the computer after a break (night, sleep): the hook is put in afresh
    app.backend._away = True
    time.sleep(7)
    back = any("after a break" in what for _, what in app.recovery_log)
    print(f"{'OK  ' if back else 'FAIL'} hook reinstalled after a break: {app.recovery_log[-1:]}")
    results.append(back)
    clear()
    layout(EN)
    type_keys("rfr ltkf ")
    check("keys are handled after the break", "как дела ")

    # Win+L: Switcher saw Win go down, the lock screen got its release; Windows holds nothing now
    from switcher.controller import KeyEvent

    app.post(lambda: app.controller.handle(KeyEvent("press", "cmd", app=app.backend.active_app(),
                                                    time=time.monotonic())))
    time.sleep(0.3)
    clear_keep_mods = app.controller.mods.copy()
    empty = ctypes.create_unicode_buffer("")
    user32.SendMessageW(edit, WM_SETTEXT, 0, ctypes.addressof(empty))
    layout(EN)
    type_keys("rfr ltkf ")
    check("keys are handled after Win+L and unlocking", "как дела ")
    print(f"   Switcher thought held before: {sorted(clear_keep_mods)}, now: {sorted(app.controller.mods)}")

    from switcher.platform.windows import process_elevated

    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(edit, ctypes.byref(pid))
    own, other = process_elevated(), process_elevated(pid.value)
    app.backend._foreground_changed(edit)  # must not fail
    told = own is not None and other is not None
    print(f"{'OK  ' if told else 'FAIL'} tells programs run as administrator (Switcher: {own}, Notepad: {other})")
    results.append(told)

    report = app.diagnostics()
    print("self-check:\n" + report)
    healthy = "Обработка клавиш: работает" in report and "Перехват клавиатуры: работает" in report
    print(f"{'OK  ' if healthy else 'FAIL'} the self-check says all works")
    results.append(healthy)

    app.stop_event.set()
    runner.join(timeout=5)
    notepad.kill()
    if ahk_found:  # last: a check that goes wrong may leave a window that takes the focus
        broken = Path(os.environ["SWITCHER_HOME"]) / "broken.ahk"
        broken.write_text("#Requires AutoHotkey v2.0\nx := 1\nif (x {\n", encoding="utf-8-sig")
        exe = ahk_found[0].path
        for args in (["/ErrorStdOut=UTF-8", "/validate"], ["/ErrorStdOut=UTF-8", "/iLib", "NUL"]):
            for path in (ahk_script, broken):
                try:
                    code, out = app.ahk.system.run([exe, *args, str(path)], timeout=10)
                except Exception as exc:
                    code, out = None, repr(exc)
                note("AutoHotkey check", f"{' '.join(args)} {path.name}: code {code}, output {out.strip()[:300]!r}")
        good, bad = app.ahk.check(str(ahk_script)), app.ahk.check(str(broken))
        checked = good[0] is True and bad[0] is False and bad[2] is not None
        print(f"{'OK  ' if checked else 'FAIL'} AutoHotkey syntax check: good {good}, broken {bad}")
        results.append(checked)

    print("PASSED" if all(results) else "FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
