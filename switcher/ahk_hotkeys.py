"""The hotkeys and hotstrings of the user's AutoHotkey scripts: listed, reassigned and added from the settings.

A hotkey is reassigned by rewriting its label on its line ("^!d::" → "#d::"), keeping what makes it
behave the way it does ("~", "$", "*", " Up").  New hotkeys made without code (type a text, open a
program or a site, or one line of AutoHotkey) go to a script of Switcher's own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .ahk_conflicts import _HOTKEY_CALL, _HOTSTRING, _LABEL, ScriptHotkey, script_hotkeys, script_hotstrings
from .controller import parse_hotkey
from .hotkeys import build_spec, format_hotkey

MY_SCRIPT = "Switcher — клавиши.ahk"
HEADERS = {
    2: "#Requires AutoHotkey v2.0\n#SingleInstance Force\n; Горячие клавиши, добавленные в Switcher. Их можно править "
       "и здесь, и в «Настройки» → «Клавиши AutoHotkey».\n",
    1: "#NoEnv\n#SingleInstance Force\nSendMode Input\n; Горячие клавиши, добавленные в Switcher. Их можно править "
       "и здесь, и в «Настройки» → «Клавиши AutoHotkey».\n",
}
ACTIONS = (("text", "Напечатать текст"), ("run", "Открыть программу, папку или сайт"), ("code", "Команда AutoHotkey"))
# Switcher's key names → AutoHotkey's
AHK_KEYS = {
    "space": "Space", "pause": "Pause", "scroll_lock": "ScrollLock", "insert": "Insert", "print_screen": "PrintScreen",
    "menu": "AppsKey", "enter": "Enter", "tab": "Tab", "backspace": "Backspace", "delete": "Delete", "home": "Home",
    "end": "End", "page_up": "PgUp", "page_down": "PgDn", "left": "Left", "right": "Right", "up": "Up",
    "down": "Down", "esc": "Escape",
}
AHK_MODS = (("ctrl", "^"), ("alt", "!"), ("shift", "+"), ("cmd", "#"))


@dataclass(frozen=True)
class Binding:
    path: str
    line: int
    kind: str          # "hotkey" | "hotstring"
    written: str       # as in the script: "^!d", "btw"
    keys: str          # for people: "Ctrl + Alt + D", "набрать «btw»"
    what: str          # what it does: the comment on it, else its code
    spec: str | None   # a hotkey in Switcher's terms ("<ctrl>+<alt>+d"), None for hotstrings
    one_line: bool     # the whole action is on this line (it can be removed without touching others)


def spec_of(hotkey: ScriptHotkey) -> str:
    return build_spec(set(hotkey.mods), hotkey.key)


def ahk_label(spec: str) -> str | None:
    """"<ctrl>+<alt>+d" → "^!d"; None for what AutoHotkey labels cannot say (a double tap)."""
    parsed = parse_hotkey(spec)
    if parsed is None or isinstance(parsed, str):
        return None
    mods, key = parsed
    name = AHK_KEYS.get(key) or (key.upper() if re.fullmatch(r"f\d{1,2}", key) else key)
    if name in (";", "`"):
        name = "`" + name
    return "".join(symbol for mod, symbol in AHK_MODS if mod in mods) + name


def _what(lines: list[str], index: int, after: str) -> tuple[str, bool]:
    """A hotkey's description: its comment, else its own code; and whether it is all on its line."""
    code = re.sub(r"(^|\s);.*$", "", after).strip()
    inline = re.search(r"(?:^|\s);\s*(.+)$", after)
    above = lines[index - 1].strip() if index > 0 else ""
    comment = inline.group(1).strip() if inline else (above[1:].strip() if above.startswith(";") else "")
    one_line = bool(code) and code != "{"
    return (comment or code or "несколько строк")[:120], one_line


def list_bindings(path: str, text: str) -> list[Binding]:
    lines = text.splitlines()
    found = []
    for hotkey in script_hotkeys(text):
        line = lines[hotkey.line - 1]
        label = _LABEL.match(line)
        after = line[label.end():] if label else ""
        what, one_line = _what(lines, hotkey.line - 1, after) if label else ("Hotkey(…) в коде", False)
        spec = spec_of(hotkey)
        keys = format_hotkey(spec) + (" (и дальше в программу)" if hotkey.passthrough else "")
        found.append(Binding(path, hotkey.line, "hotkey", hotkey.written, keys, what, spec, one_line and bool(label)))
    for hotstring in script_hotstrings(text):
        line = lines[hotstring.line - 1]
        match = _HOTSTRING.match(line)
        after = line[match.end():] if match else ""
        what, one_line = _what(lines, hotstring.line - 1, after)
        found.append(Binding(path, hotstring.line, "hotstring", hotstring.abbr, f"набрать «{hotstring.abbr}»", what,
                             None, one_line and match is not None))
    return sorted(found, key=lambda b: b.line)


def relabel(text: str, line: int, written: str, new_label: str) -> str:
    """The script with the hotkey on ``line`` moved to ``new_label``, keeping "~", "$", "*" and " Up"."""
    lines = text.split("\n")
    old = lines[line - 1]
    prefix = re.match(r"[~$*]*", written).group(0)
    up = re.search(r"\s+up$", written, re.I)
    label = prefix + new_label + (up.group(0) if up else "")
    match = _LABEL.match(old)
    if match and match.group("label") == written:
        lines[line - 1] = old[:match.start("label")] + label + old[match.end("label"):]
    elif _HOTKEY_CALL.match(old) and written in old:
        lines[line - 1] = old.replace(written, label, 1)
    else:
        raise ValueError(f"строка {line} изменилась: в ней больше нет «{written}»")
    return "\n".join(lines)


def _v2_string(value: str) -> str:
    return '"' + value.replace("`", "``").replace('"', '`"').replace("\n", "`n") + '"'


def _v1_text(value: str) -> str:
    return re.sub(r"([`%,;])", r"`\1", value).replace("\n", "`n")


def action_code(kind: str, value: str, major: int) -> str:
    """One line of AutoHotkey for an action chosen in the settings."""
    value = value.strip("\r\n")
    if kind == "text":
        return f"SendText {_v2_string(value)}" if major >= 2 else f"SendInput {{Text}}{_v1_text(value)}"
    if kind == "run":
        return f"Run {_v2_string(value)}" if major >= 2 else f"Run, {_v1_text(value)}"
    if kind == "code":
        return value.strip()
    raise ValueError(kind)


def add_binding(text: str, label: str, kind: str, value: str, major: int, description: str = "") -> str:
    """The script with a new one-line hotkey at its end."""
    body = text if text.endswith("\n") or not text else text + "\n"
    comment = f"\n; {description.strip()}" if description.strip() else "\n"
    return f"{body}{comment}\n{label}::{action_code(kind, value, major)}\n"


def remove_line(text: str, line: int) -> str:
    """The script without a one-line hotkey (and the comment right above it)."""
    lines = text.split("\n")
    start = line - 1
    if start > 0 and lines[start - 1].strip().startswith(";"):
        start -= 1
    del lines[start:line]
    return "\n".join(lines)


def my_script(folder: Path, major: int) -> Path:
    """Switcher's own script for hotkeys made in the settings, created when first needed."""
    path = folder / MY_SCRIPT
    if not path.exists():
        folder.mkdir(parents=True, exist_ok=True)
        path.write_text(HEADERS[major], encoding="utf-8-sig")
    return path
