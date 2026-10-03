"""The AutoHotkey manager: which scripts there are, starting and stopping them, checking and saving."""

import os
import sys
import threading
import time

import pytest

from switcher import ahk
from switcher.config import Config


class FakeSystem(ahk.System):
    supported = True

    def __init__(self, tmp_path):
        self.windows: dict[str, int] = {}
        self.commands: list[tuple[int, int]] = []
        self.launched: list[str] = []
        self.refuse = False
        self.startup: list[str] = []
        self.checks: list[list[str]] = []
        self.check_result = (0, "")
        self.root = str(tmp_path / "AutoHotkey")

    def running(self):
        return dict(self.windows)

    def command(self, window, command):
        if self.refuse:
            return False
        self.commands.append((window, command))
        if command == ahk.ID_EXIT:
            self.windows = {p: w for p, w in self.windows.items() if w != window}
        return True

    def launch(self, path):
        self.launched.append(path)
        self.windows[path] = 100 + len(self.launched)

    def install_roots(self):
        return [self.root]

    def run(self, args, timeout):
        self.checks.append(args)
        return self.check_result

    def startup_scripts(self):
        return list(self.startup)


def script(tmp_path, name="keys.ahk", text="#Requires AutoHotkey v2.0\n::btw::by the way\n"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def manager(tmp_path, scripts=None):
    config = Config()
    config.ahk_scripts = scripts or {}
    system = FakeSystem(tmp_path)
    return ahk.AhkManager(config, system=system), system


def test_a_running_script_is_known_by_its_window_title():
    assert ahk.title_path(r"C:\Users\me\Мои скрипты\keys.ahk - AutoHotkey v2.0.18") == r"C:\Users\me\Мои скрипты\keys.ahk"
    assert ahk.title_path(r"D:\tools\hotkeys.exe") == r"D:\tools\hotkeys.exe"  # a compiled script
    assert ahk.title_path(r"C:\x\a.ahk - AutoHotkey v1.1.37.02") == r"C:\x\a.ahk"
    assert ahk.title_path("Untitled - Notepad") is None
    assert ahk.title_path("") is None


def test_the_version_a_script_is_written_for():
    assert ahk.script_version("#Requires AutoHotkey v2.0\n") == 2
    assert ahk.script_version("#Requires AutoHotkey >=2.0-a\n") == 2
    assert ahk.script_version("#Requires AutoHotkey v1.1.33+\n") == 1
    assert ahk.script_version("#NoEnv\nSendMode Input\n^j::\nSend, hello\nreturn\n") == 1
    assert ahk.script_version('^j::SendText("hello")\nMsgBox("done")\n') == 2
    assert ahk.script_version("::btw::by the way\n") is None  # the same in both


def test_autohotkey_is_found_where_its_installer_puts_it(tmp_path):
    files = {r"C:\AHK\v2\AutoHotkey64.exe", r"C:\AHK\v1.1.37.02\AutoHotkeyU64.exe", r"C:\Old\AutoHotkey.exe"}
    listing = {r"C:\AHK": ["v2", "v1.1.37.02", "UX"], r"C:\Old": []}
    found = ahk.find_interpreters(
        [r"C:\AHK", r"C:\Old", ""], exists=lambda p: p.replace("/", "\\") in files,
        listdir=lambda p: listing.get(p, []))
    assert [(os.path.basename(i.path), i.major) for i in found] == [
        ("AutoHotkey64.exe", 2), ("AutoHotkeyU64.exe", 1), ("AutoHotkey.exe", 1)]
    assert found[0].check_args("s.ahk")[1:3] == ["/ErrorStdOut=UTF-8", "/validate"]
    assert found[1].check_args("s.ahk")[1:4] == ["/ErrorStdOut", "/iLib", "NUL"]


def test_the_list_is_the_settings_plus_whatever_runs(tmp_path):
    mine, other = script(tmp_path, "b-mine.ahk"), script(tmp_path, "a-other.ahk")
    m, system = manager(tmp_path, {mine: True})
    system.windows = {other: 7}
    assert m.listed() == [other, mine]
    assert m.is_running(other) and not m.is_running(mine)


def test_a_short_8_3_path_is_the_same_script(tmp_path):
    if sys.platform != "win32":
        pytest.skip("8.3 short names are a Windows thing")
    import ctypes

    folder = tmp_path / "a rather long folder name"
    folder.mkdir()
    path = script(folder)
    buf = ctypes.create_unicode_buffer(1024)
    if not ctypes.windll.kernel32.GetShortPathNameW(path, buf, 1024) or buf.value == path:
        pytest.skip("no 8.3 names on this drive")
    assert ahk.norm(buf.value) == ahk.norm(path)


def test_the_same_script_by_another_spelling_of_its_path(tmp_path):
    real = tmp_path / "long folder name"
    real.mkdir()
    path = script(real)
    alias = tmp_path / "LONGFO~1"  # like Windows' 8.3 short names: another path to the same file
    try:
        alias.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("no symlinks here")
    m, system = manager(tmp_path, {str(alias / "keys.ahk"): True})
    system.windows = {path: 5}  # AutoHotkey's title shows the full long path
    assert m.is_running(str(alias / "keys.ahk"))
    assert m.listed() == [str(alias / "keys.ahk")]  # one script, not two


def test_autohotkeys_own_launcher_is_not_one_of_the_users_scripts(tmp_path):
    m, system = manager(tmp_path)
    launcher = tmp_path / "AutoHotkey" / "UX" / "launcher.ahk"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("", encoding="utf-8")
    path = script(tmp_path)
    system.windows = {str(launcher): 1, path: 2}
    assert m.running(fresh=True) == [path]


def test_start_stop_and_reload(tmp_path):
    path = script(tmp_path)
    m, system = manager(tmp_path, {path: False})
    changes = []
    m.listeners.append(lambda: changes.append(1))
    assert m.toggle(path) is None and system.launched == [path] and m.is_running(path)
    assert m.start(path) is None and system.launched == [path]  # already running: not twice
    assert m.reload(path) is None and system.commands[-1][1] == ahk.ID_RELOAD
    assert m.toggle(path) is None and system.commands[-1][1] == ahk.ID_EXIT and not m.is_running(path)
    assert changes
    assert "Нет файла" in m.start(str(tmp_path / "gone.ahk"))


def test_a_script_that_will_not_listen_is_reported(tmp_path):
    path = script(tmp_path)
    m, system = manager(tmp_path)
    system.windows = {path: 5}
    system.refuse = True  # an administrator's script: Windows drops our messages
    assert "администратора" in m.stop(path)


def test_scripts_marked_so_start_with_switcher(tmp_path):
    on, off, running = script(tmp_path, "on.ahk"), script(tmp_path, "off.ahk"), script(tmp_path, "run.ahk")
    m, system = manager(tmp_path, {on: True, off: False, running: True, str(tmp_path / "gone.ahk"): True})
    system.windows = {running: 3}
    m.start_with_switcher()
    assert system.launched == [on]


def test_scripts_already_in_use_are_offered(tmp_path):
    startup, running, listed = script(tmp_path, "s.ahk"), script(tmp_path, "r.ahk"), script(tmp_path, "l.ahk")
    m, system = manager(tmp_path, {listed: True})
    system.startup, system.windows = [startup], {running: 1, listed: 2}
    assert sorted(m.discover()) == sorted([startup, running])


def test_the_syntax_check(tmp_path):
    path = script(tmp_path)
    m, system = manager(tmp_path)
    (tmp_path / "AutoHotkey" / "v2").mkdir(parents=True)
    (tmp_path / "AutoHotkey" / "v2" / "AutoHotkey64.exe").write_bytes(b"")
    assert m.check(path)[0] is True
    assert system.checks[-1][-1] == path and "/validate" in system.checks[-1]
    system.check_result = (2, f"{path} (2) : ==> This line does not contain a recognized action.\n")
    ok, message, line = m.check(path)
    assert ok is False and line == 2 and message.startswith("Строка 2: This line")
    old = script(tmp_path, "old.ahk", "#NoEnv\nMsgBox, hi\n")
    assert m.check(old)[:2] == (None, "Проверить нельзя: скрипт написан для AutoHotkey v1, а он не установлен")


def test_no_autohotkey_no_check(tmp_path):
    m, _ = manager(tmp_path)
    assert m.check(script(tmp_path))[0] is None
    assert "не найден" in m.describe_install()


def test_a_new_script_and_saving_keep_what_autohotkey_can_read(tmp_path):
    m, _ = manager(tmp_path)
    path = m.new_script(tmp_path / "scripts", 'Мои: "клавиши"', major=1)
    assert path.name == "Мои_ _клавиши_.ahk"
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")  # v1 needs the BOM for Russian text
    text, encoding, newline = ahk.read_script(path)
    assert "#NoEnv" in text and encoding == "utf-8-sig"
    old = tmp_path / "old.ahk"
    old.write_bytes("::пр::привет\r\n".encode("cp1251"))
    text, encoding, newline = ahk.read_script(old)
    assert text == "::пр::привет\n" and encoding == "cp1251" and newline == "\r\n"
    assert ahk.write_script(old, text + "::ok::окей\n", encoding, newline) == "cp1251"
    assert old.read_bytes() == "::пр::привет\r\n::ok::окей\r\n".encode("cp1251")
    assert ahk.write_script(old, "::e::😀\n", encoding, newline) == "utf-8-sig"  # cp1251 has no emoji
    plain = tmp_path / "plain.ahk"
    assert ahk.write_script(plain, "::д::да\n", "utf-8", "\n") == "utf-8-sig"


def test_the_watch_tells_when_a_script_starts_or_stops(tmp_path):
    path = script(tmp_path)
    m, system = manager(tmp_path)
    m.WATCH_EVERY = 0.01
    seen = threading.Event()
    m.listeners.append(seen.set)
    stop = threading.Event()
    watcher = threading.Thread(target=m.watch, args=(stop,))
    watcher.start()
    system.windows = {path: 9}  # started from Explorer, not from Switcher
    assert seen.wait(2)
    stop.set()
    watcher.join(2)


def test_unsupported_systems_say_so(tmp_path):
    m = ahk.AhkManager(Config(), system=ahk.System())
    assert not m.supported and m.listed() == [] and "Windows" in m.describe_install()
    assert "Windows" in m.start(script(tmp_path))


def test_a_script_taking_switchers_keys_is_told_about_once(tmp_path):
    clash = script(tmp_path, "clash.ahk", "#Requires AutoHotkey v2.0\n^!s::MsgBox 1\n~Shift::return\n")
    calm = script(tmp_path, "calm.ahk", "#Requires AutoHotkey v2.0\n^!d::MsgBox 1\n")
    m, system = manager(tmp_path, {clash: False, calm: False})
    notes = []
    m.notify = notes.append
    m.WATCH_EVERY = 0.01
    stop = threading.Event()
    watcher = threading.Thread(target=m.watch, args=(stop,))
    watcher.start()
    system.windows = {calm: 1}
    time.sleep(0.1)
    assert notes == []
    system.windows = {calm: 1, clash: 2}  # the script starts
    deadline = time.time() + 2
    while not notes and time.time() < deadline:
        time.sleep(0.01)
    system.windows = {calm: 1}
    time.sleep(0.05)
    system.windows = {calm: 1, clash: 3}  # and again: nothing new to say
    time.sleep(0.1)
    stop.set()
    watcher.join(2)
    assert len(notes) == 1
    assert notes[0].startswith("Скрипт «clash», строка 2: ^!s — это Ctrl + Alt + S") and "(и ещё 1)" in notes[0]
    assert m.all_conflicts().keys() == {clash}
    assert m.scripts_using("<ctrl>+<alt>+s") == [(clash, 2, "^!s")]
    assert m.scripts_using("<ctrl>+<alt>+q") == []
