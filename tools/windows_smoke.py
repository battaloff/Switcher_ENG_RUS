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
user32.AttachThreadInput.argtypes = (wintypes.DWORD, wintypes.DWORD, wintypes.BOOL)
user32.LoadKeyboardLayoutW.argtypes = (wintypes.LPCWSTR, wintypes.UINT)
user32.LoadKeyboardLayoutW.restype = ctypes.c_void_p
WM_SETTEXT, WM_GETTEXT, EM_SETSEL = 0x000C, 0x000D, 0x00B1
ON_CI = os.environ.get("GITHUB_ACTIONS") == "true"


def _escape(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")[:4000]


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


def main() -> int:
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
    tap(Key.esc)
    time.sleep(1)


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
    select("vfibyf? rfr ltkf")  # not "ghbdtn": the undo above taught to keep that one as typed
    shift_pause()
    check("Shift+Pause fixes the selected text", "машина, как дела")
    select("ыршае=зфгыу")
    shift_pause()
    check("words joined by \"=\" are fixed one by one", "shift=pause")
    layout(EN)
    select("shift+pause")
    shift_pause()
    check("Shift+Pause leaves right text alone", "shift+pause")
    shift_pause()
    check("a second Shift+Pause swaps its layout anyway", "ыршае+зфгыу")

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
