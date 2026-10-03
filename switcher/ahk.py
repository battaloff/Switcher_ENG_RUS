"""AutoHotkey scripts: one place to start, stop, reload, check and edit them (settings page and tray menu).

Scripts run exactly as on a double click (the ``.ahk`` file association), so the AutoHotkey version
installed decides how.  A running script is found by its hidden main window: class ``AutoHotkey``,
titled "C:\\path\\script.ahk - AutoHotkey v2.0.18".  Stopping and reloading send it the commands of
its own tray menu, so the script exits the way it would from there (OnExit handlers run).
"""

from __future__ import annotations

import logging
import ntpath
import os
import re
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

log = logging.getLogger(__name__)

SCRIPT_SUFFIXES = (".ahk", ".ah2", ".ahk2")
DOWNLOAD_URL = "https://www.autohotkey.com/"
# commands of a script's tray menu (WM_COMMAND ids, the same in AutoHotkey v1 and v2)
ID_RELOAD, ID_SUSPEND, ID_PAUSE, ID_EXIT = 65303, 65305, 65306, 65307

TEMPLATES = {
    2: """#Requires AutoHotkey v2.0
#SingleInstance Force

; Ctrl+Alt+D — сегодняшняя дата
^!d::SendText FormatTime(, "dd.MM.yyyy")

; набираете "@@" и пробел — получаете адрес
::@@::me@example.com
""",
    1: """#NoEnv
#SingleInstance Force
SendMode Input

; Ctrl+Alt+D — сегодняшняя дата
^!d::
FormatTime, today,, dd.MM.yyyy
SendInput {Text}%today%
return

; набираете "@@" и пробел — получаете адрес
::@@::me@example.com
""",
}

_TITLE = re.compile(r"^(?P<path>.+?)(?: - AutoHotkey(?: v[\w.\-+]+)?)?$")
_REQUIRES = re.compile(r"^\s*#Requires\s+AutoHotkey\s+(?:[<>=]*\s*)?v?(?P<major>[12])", re.I | re.M)
_V1_SIGNS = re.compile(
    r"^\s*(?:#NoEnv\b|SetWorkingDir,?\s+%|(?:MsgBox|Send|SendInput|Sleep|Run|FormatTime|StringReplace|"
    r"SetTitleMatchMode|WinActivate|IfWinActive|IfWinExist)\s*,)",
    re.I | re.M)
_V2_SIGNS = re.compile(r"\b(?:MsgBox|Send|SendInput|SendText|Run|Sleep|WinActive|WinActivate|FormatTime)\(|=>|"
                       r"^\s*(?:SendText|WinActivate)\s+[\"\w]", re.I | re.M)
_ERROR = re.compile(r"\((?P<line>\d+)\)\s*:\s*==>\s*(?P<text>.+)")
_LINE = re.compile(r"(?:^|\n)\s*(?:Line|Строка)\D{0,3}(?P<line>\d+)", re.I)


def norm(path: str | os.PathLike) -> str:
    """A path as a key: absolute, long (not "C:\\Users\\RUNNER~1\\…"), with Windows' case and separators.

    AutoHotkey shows a script's full long path in its window title, whatever path it was started with.
    """
    path = os.fspath(path)
    try:
        path = os.path.realpath(path)
    except (OSError, ValueError):
        path = os.path.abspath(path)
    return os.path.normcase(path)


def is_script(path: str | os.PathLike) -> bool:
    return os.fspath(path).lower().endswith(SCRIPT_SUFFIXES + (".exe",))


def name_of(path: str) -> str:
    return ntpath.splitext(ntpath.basename(path))[0]  # Windows paths, whatever the OS running the tests


def title_path(title: str) -> str | None:
    """The script path in the title of an AutoHotkey main window, or None."""
    found = _TITLE.match(title.strip())
    path = found["path"].strip() if found else ""
    return path if path and is_script(path) and (":" in path or path.startswith("\\\\")) else None


def script_version(text: str) -> int | None:
    """1 or 2: the AutoHotkey a script is written for (its #Requires line, else the look of its code)."""
    found = _REQUIRES.search(text)
    if found:
        return int(found["major"])
    v1, v2 = len(_V1_SIGNS.findall(text)), len(_V2_SIGNS.findall(text))
    if v1 == v2:
        return None
    return 1 if v1 > v2 else 2


def parse_error(output: str) -> tuple[int | None, str]:
    """(line, message) from AutoHotkey's /ErrorStdOut text."""
    output = output.strip()
    found = _ERROR.search(output)
    if found:
        return int(found["line"]), found["text"].strip()
    line = _LINE.search(output)
    return (int(line["line"]) if line else None), output.splitlines()[0] if output else ""


def read_script(path: str | os.PathLike) -> tuple[str, str, str]:
    """(text with \\n line ends, encoding, line end) — to write it back the way it was."""
    raw = Path(path).read_bytes()
    newline = "\r\n" if b"\r\n" in raw else "\n"
    if raw.startswith(b"\xef\xbb\xbf"):
        text, encoding = raw[3:].decode("utf-8", errors="replace"), "utf-8-sig"
    elif raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        text, encoding = raw.decode("utf-16"), "utf-16"
    else:
        try:
            text, encoding = raw.decode("utf-8"), "utf-8"
        except UnicodeDecodeError:  # AutoHotkey v1 scripts saved by Notepad in the system code page
            text, encoding = raw.decode("cp1251", errors="replace"), "cp1251"
    return text.replace("\r\n", "\n"), encoding, newline


def write_script(path: str | os.PathLike, text: str, encoding: str = "utf-8-sig", newline: str = "\r\n") -> str:
    """Save keeping the file's encoding; one that cannot hold the text becomes UTF-8 with BOM.  Returns it."""
    data = text.replace("\r\n", "\n").replace("\n", newline)
    try:
        raw = data.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        encoding, raw = "utf-8-sig", data.encode("utf-8-sig")  # both AutoHotkey versions read it
    if encoding == "utf-8" and any(ord(ch) > 127 for ch in data):
        # AutoHotkey v1 reads UTF-8 without a BOM as the system code page: "привет" would come out garbled
        encoding, raw = "utf-8-sig", data.encode("utf-8-sig")
    Path(path).write_bytes(raw)
    return encoding


@dataclass(frozen=True)
class Interpreter:
    path: str
    major: int  # 1 or 2

    def check_args(self, script: str) -> list[str]:
        """Load the script, report syntax errors to stdout and exit without running it."""
        if self.major >= 2:
            return [self.path, "/ErrorStdOut=UTF-8", "/validate", script]
        return [self.path, "/ErrorStdOut", "/iLib", "NUL", script]


_INTERPRETER_NAMES = {
    # name: major version when the file version cannot be read
    "AutoHotkey64.exe": 2, "AutoHotkey32.exe": 2,
    "AutoHotkeyU64.exe": 1, "AutoHotkeyU32.exe": 1, "AutoHotkeyA32.exe": 1, "AutoHotkey.exe": 1,
}


def find_interpreters(roots: Iterable[str], major_of: Callable[[str], int | None] = lambda path: None,
                      exists: Callable[[str], bool] = os.path.isfile,
                      listdir: Callable[[str], list[str]] = lambda path: os.listdir(path)) -> list[Interpreter]:
    """AutoHotkey executables under the install folders, newest major version first."""
    found: dict[str, Interpreter] = {}
    for root in roots:
        if not root:
            continue
        folders = [root, os.path.join(root, "v2")]
        try:
            folders += [os.path.join(root, sub) for sub in listdir(root) if sub.lower().startswith("v1")]
        except OSError:
            pass
        for folder in folders:
            for name, guess in _INTERPRETER_NAMES.items():
                path = os.path.join(folder, name)
                key = norm(path)
                if key in found or not exists(path):
                    continue
                major = major_of(path) or (2 if os.path.basename(folder).lower() == "v2" else guess)
                found[key] = Interpreter(path, major)
    order = list(_INTERPRETER_NAMES)
    return sorted(found.values(), key=lambda i: (-i.major, order.index(os.path.basename(i.path))))


class System:
    """What the manager needs from the OS; the Windows one is in ``platform.win_ahk``."""

    supported = False

    def running(self) -> dict[str, int]:
        """Script path → its main window, for every AutoHotkey script running."""
        return {}

    def command(self, window: int, command: int) -> bool:
        return False

    def launch(self, path: str) -> None:
        raise OSError("AutoHotkey работает только в Windows")

    def edit_elsewhere(self, path: str) -> None:
        raise OSError("AutoHotkey работает только в Windows")

    def install_roots(self) -> list[str]:
        return []

    def major_of(self, path: str) -> int | None:
        return None

    def run(self, args: list[str], timeout: float) -> tuple[int, str]:
        raise OSError("AutoHotkey работает только в Windows")

    def startup_scripts(self) -> list[str]:
        """Scripts Windows itself starts at sign-in (the Startup folder)."""
        return []


def default_system() -> System:
    if sys.platform == "win32":
        try:
            from .platform.win_ahk import WindowsAhk

            return WindowsAhk()
        except Exception:
            log.exception("AutoHotkey support is unavailable")
    return System()


class AhkManager:
    """The user's scripts: the ones in the settings plus any AutoHotkey script running right now."""

    WATCH_EVERY = 2.0  # seconds between looks at which scripts run (tray menu, settings page)
    START_WAIT = 5.0   # a script started this long ago and still without a window has failed

    def __init__(self, config, system: System | None = None, notify: Callable[[str], None] | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.config = config  # reads config.ahk_scripts: {path: start with Switcher}
        self.system = system or default_system()
        self.notify = notify or (lambda message: None)
        self.clock = clock
        self.listeners: list[Callable[[], None]] = []
        self._running: dict[str, tuple[str, int]] = {}  # norm(path) → (path, main window)
        self._looked_at = float("-inf")
        self._interpreters: list[Interpreter] | None = None
        self._own_folders: list[str] | None = None  # AutoHotkey's install folders: its own scripts live there
        self._lock = threading.Lock()

    # -- what there is ---------------------------------------------------------

    @property
    def supported(self) -> bool:
        return self.system.supported

    def configured(self) -> dict[str, bool]:
        return dict(getattr(self.config, "ahk_scripts", {}) or {})

    def _ahk_own(self, key: str) -> bool:
        """AutoHotkey's own helper scripts (its launcher, its Dash), not the user's."""
        if self._own_folders is None:
            try:
                self._own_folders = [norm(root) + os.sep for root in self.system.install_roots()]
            except Exception:
                self._own_folders = []
        return any(key.startswith(folder) for folder in self._own_folders)

    def _look(self, fresh: bool = False) -> dict[str, tuple[str, int]]:
        with self._lock:
            if fresh or self.clock() - self._looked_at > 0.5:
                try:
                    found = ((norm(path), path, window) for path, window in self.system.running().items())
                    self._running = {key: (path, window) for key, path, window in found if not self._ahk_own(key)}
                except Exception:
                    log.exception("could not list the running AutoHotkey scripts")
                    self._running = {}
                self._looked_at = self.clock()
            return dict(self._running)

    def running(self, fresh: bool = False) -> list[str]:
        """Paths of the AutoHotkey scripts running now."""
        return [path for path, _ in self._look(fresh).values()]

    def is_running(self, path: str) -> bool:
        return norm(path) in self._look()

    def listed(self) -> list[str]:
        """Scripts in the settings and the ones running now, by name."""
        paths = {norm(p): p for p in self.configured()}
        for key, (path, _) in self._look().items():
            paths.setdefault(key, path)
        return sorted(paths.values(), key=lambda p: (name_of(p).lower(), p.lower()))

    def interpreters(self) -> list[Interpreter]:
        if self._interpreters is None:
            try:
                self._interpreters = find_interpreters(self.system.install_roots(), self.system.major_of)
            except Exception:
                log.exception("could not look for AutoHotkey")
                self._interpreters = []
        return self._interpreters

    def interpreter_for(self, path: str) -> tuple[Interpreter | None, int | None]:
        """The AutoHotkey to check a script with, and the version the script is written for."""
        try:
            major = script_version(Path(path).read_text(encoding="utf-8-sig", errors="replace"))
        except OSError:
            major = None
        found = self.interpreters()
        if major:
            return next((i for i in found if i.major == major), None), major
        return (found[0] if found else None), None

    def describe_install(self) -> str:
        if not self.supported:
            return "AutoHotkey работает только в Windows."
        found = self.interpreters()
        if not found:
            return "AutoHotkey не найден на этом компьютере: скрипты запускаются, только если он установлен."
        versions = ", ".join(sorted({f"v{i.major}" for i in found}, reverse=True))
        return f"AutoHotkey установлен ({versions}): {ntpath.dirname(found[0].path)}"

    # -- doing -----------------------------------------------------------------

    def start(self, path: str) -> str | None:
        """Run a script like a double click does; an error message, or None."""
        if not self.supported:
            return "AutoHotkey работает только в Windows"
        if not os.path.isfile(path):
            return f"Нет файла {path}"
        if self.is_running(path):
            return None
        try:
            self.system.launch(path)
        except OSError as exc:
            log.warning("could not start %s: %s", path, exc)
            if not self.interpreters():
                return "AutoHotkey не установлен — скачайте его с autohotkey.com"
            return f"Не удалось запустить {name_of(path)}: {exc}"
        log.info("AutoHotkey script started: %s", path)
        self._changed()
        return None

    def _send(self, path: str, command: int) -> bool:
        window = self._look(fresh=True).get(norm(path), (path, 0))[1]
        if not window:
            return False
        if not self.system.command(window, command):
            log.warning("AutoHotkey script %s did not take command %d (runs as administrator?)", path, command)
            return False
        return True

    def stop(self, path: str) -> str | None:
        if not self.is_running(path):
            return None
        if not self._send(path, ID_EXIT):
            return (f"{name_of(path)} не отвечает Switcher — возможно, он запущен от имени администратора. "
                    "Закройте его через его значок в трее.")
        log.info("AutoHotkey script stopped: %s", path)
        self._changed()
        return None

    def reload(self, path: str) -> str | None:
        if not self.is_running(path):
            return self.start(path)
        if not self._send(path, ID_RELOAD):
            return f"{name_of(path)} не отвечает Switcher — перезапустите его через его значок в трее."
        log.info("AutoHotkey script reloaded: %s", path)
        self._changed()
        return None

    def toggle(self, path: str) -> str | None:
        return self.stop(path) if self.is_running(path) else self.start(path)

    def reload_running(self) -> list[str]:
        return [error for path in self.running(fresh=True) if (error := self.reload(path))]

    def stop_all(self) -> list[str]:
        return [error for path in self.running(fresh=True) if (error := self.stop(path))]

    def start_with_switcher(self) -> None:
        """At Switcher's start: run the scripts marked so that are not running yet."""
        for path, wanted in self.configured().items():
            if wanted and os.path.isfile(path) and not self.is_running(path):
                error = self.start(path)
                if error:
                    self.notify(error)

    def check(self, path: str) -> tuple[bool | None, str, int | None]:
        """Syntax check without running: (ok — None when AutoHotkey is missing, message, error line)."""
        interpreter, major = self.interpreter_for(path)
        if interpreter is None:
            if major and self.interpreters():
                return None, f"Проверить нельзя: скрипт написан для AutoHotkey v{major}, а он не установлен", None
            return None, "Проверить нельзя: AutoHotkey не найден", None
        try:
            code, output = self.system.run(interpreter.check_args(path), timeout=15)
        except Exception as exc:  # a timeout too: the script may have started running
            log.warning("AutoHotkey check of %s failed: %s", path, exc)
            return None, f"Проверка не удалась: {exc}", None
        line, message = parse_error(output)
        if code == 0 and "==>" not in output:
            warning = f"; предупреждение: {message}" if message else ""
            return True, f"Ошибок нет (AutoHotkey v{interpreter.major}){warning}", None
        where = f"Строка {line}: " if line else ""
        return False, where + (message or f"AutoHotkey завершился с кодом {code}"), line

    def new_script(self, folder: Path, name: str, major: int = 2) -> Path:
        name = re.sub(r'[\\/:*?"<>|]+', "_", name.strip()) or "Новый скрипт"
        path = folder / (name if name.lower().endswith(SCRIPT_SUFFIXES) else name + ".ahk")
        if path.exists():
            raise FileExistsError(f"Файл {path.name} уже есть")
        folder.mkdir(parents=True, exist_ok=True)
        path.write_text(TEMPLATES[major], encoding="utf-8-sig")  # AutoHotkey v1 reads UTF-8 only with the BOM
        return path

    def preferred_major(self) -> int:
        found = self.interpreters()
        return found[0].major if found else 2

    def discover(self) -> list[str]:
        """Scripts to offer when the list is empty: those running now and those in the Startup folder."""
        found = {norm(p): p for p in self.system.startup_scripts()}
        for path in self.running(fresh=True):
            found.setdefault(norm(path), path)
        configured = {norm(p) for p in self.configured()}
        return [p for key, p in found.items() if key not in configured]

    # -- watching ----------------------------------------------------------------

    def _changed(self) -> None:
        self.running(fresh=True)
        for listener in list(self.listeners):
            try:
                listener()
            except Exception:
                log.exception("AutoHotkey listener failed")

    def watch(self, stop: threading.Event) -> None:
        """Tell the listeners whenever a script starts or stops, whoever started or stopped it."""
        before = set(self.running(fresh=True))
        while not stop.wait(self.WATCH_EVERY):
            now = set(self.running(fresh=True))
            if now != before:
                before = now
                self._changed()
