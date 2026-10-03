"""Where an AutoHotkey script and Switcher want the same keys.

Read from the script's text: its hotkeys ("^!s::", "~Shift::", Hotkey("^!s", …)) against Switcher's
hotkeys, and its hotstrings ("::015::…") against Switcher's snippets.  A hotkey a script takes
without "~" never reaches Switcher (AutoHotkey's hook keeps it); with "~" both act on it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .controller import parse_hotkey
from .hotkeys import ACTIONS, format_hotkey
from .layouts import DEFAULT_KEYBOARD

AHK_MODIFIERS = {"^": "ctrl", "!": "alt", "+": "shift", "#": "cmd"}
MODIFIER_KEYS = {
    "shift": "shift", "lshift": "shift", "rshift": "shift",
    "ctrl": "ctrl", "control": "ctrl", "lctrl": "ctrl", "rctrl": "ctrl", "lcontrol": "ctrl", "rcontrol": "ctrl",
    "alt": "alt", "lalt": "alt", "ralt": "alt", "lwin": "cmd", "rwin": "cmd",
}
KEY_NAMES = {
    "space": "space", "pause": "pause", "scrolllock": "scroll_lock", "insert": "insert", "ins": "insert",
    "printscreen": "print_screen", "appskey": "menu", "enter": "enter", "return": "enter", "tab": "tab",
    "backspace": "backspace", "bs": "backspace", "delete": "delete", "del": "delete", "home": "home", "end": "end",
    "pgup": "page_up", "pgdn": "page_down", "left": "left", "right": "right", "up": "up", "down": "down",
    "escape": "esc", "esc": "esc",
}

_LABEL = re.compile(r"^\s*(?P<label>[^\s:;\"'(][^\s:]*(?:\s+&\s+[^\s:]+)?(?:\s+up)?)::", re.I)
_HOTKEY_CALL = re.compile(r"^\s*Hotkey\s*(?:\(\s*|,\s*|\s+)[\"']?(?P<label>[^\"',\s)%]+)", re.I)
_HOTSTRING = re.compile(r"^\s*:(?P<options>[^:\s]*):(?P<abbr>.+?)::")
_HOTSTRING_CALL = re.compile(r"\bHotstring\s*\(\s*[\"']:(?P<options>[^:\"']*):(?P<abbr>[^\"']+)[\"']", re.I)


@dataclass(frozen=True)
class ScriptHotkey:
    line: int
    written: str              # as in the script: "^!s"
    mods: frozenset[str]
    key: str                  # in Switcher's names: "s", "space", "pause", "shift"
    wildcard: bool            # "*": fires whatever other modifiers are held
    passthrough: bool         # "~": the key still reaches other programs


@dataclass(frozen=True)
class ScriptHotstring:
    line: int
    abbr: str
    case_sensitive: bool


@dataclass(frozen=True)
class Conflict:
    line: int
    message: str


def _code_lines(text: str):
    """(line number, line) outside comments and continuation sections."""
    in_comment = in_section = False
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if in_comment:
            if stripped.startswith("*/") or stripped.endswith("*/"):
                in_comment = False
            continue
        if stripped.startswith("/*"):
            in_comment = not stripped.endswith("*/") or stripped == "/*"
            continue
        if in_section:
            if stripped.startswith(")"):
                in_section = False
            continue
        if stripped.startswith("(") and not stripped.startswith("()") and ")" not in stripped:
            in_section = True  # a continuation section: text, not code
            continue
        line = re.sub(r"(^|\s);.*$", "", line)
        if line.strip():
            yield number, line


def _key(name: str) -> str | None:
    """An AutoHotkey key name in Switcher's names."""
    low = name.lower()
    if low in MODIFIER_KEYS:
        return MODIFIER_KEYS[low]
    if low in KEY_NAMES:
        return KEY_NAMES[low]
    if re.fullmatch(r"f([1-9]|1[0-9]|2[0-4])", low):
        return low
    if len(name) == 1:
        found = DEFAULT_KEYBOARD.stroke_for_char(name, None)  # "ы" is the S key, as Switcher names keys
        return found[0].code if found else low
    return None  # mouse buttons, vk/sc codes and the like


def parse_label(label: str, line: int = 0) -> ScriptHotkey | None:
    """'~^!s', '*Pause', 'LCtrl & s', '^!s Up' → a hotkey, or None if it is not a keyboard one."""
    written = label.strip()
    body = re.sub(r"\s+up$", "", written, flags=re.I)
    mods: set[str] = set()
    if "&" in body:  # a custom combination: only "Ctrl & s" style ones are like Switcher's
        first, _, second = (part.strip() for part in body.partition("&"))
        prefix = MODIFIER_KEYS.get(first.lstrip("~*$").lower())
        if prefix is None:
            return None
        mods.add(prefix)
        body = second
    wildcard = passthrough = False
    i = 0
    while i < len(body) - 1 and body[i] in "~*$<>^!+#":
        ch = body[i]
        wildcard |= ch == "*"
        passthrough |= ch == "~"
        if ch in AHK_MODIFIERS:
            mods.add(AHK_MODIFIERS[ch])
        i += 1
    key = _key(body[i:])
    if key is None:
        return None
    return ScriptHotkey(line, written, frozenset(mods), key, wildcard, passthrough or written.startswith("~"))


def script_hotkeys(text: str) -> list[ScriptHotkey]:
    found = []
    for number, line in _code_lines(text):
        if _HOTSTRING.match(line):
            continue
        match = _LABEL.match(line) or _HOTKEY_CALL.match(line)
        if match:
            hotkey = parse_label(match["label"], number)
            if hotkey is not None:
                found.append(hotkey)
    return found


def script_hotstrings(text: str) -> list[ScriptHotstring]:
    found = []
    for number, line in _code_lines(text):
        for match in (_HOTSTRING.match(line), _HOTSTRING_CALL.search(line)):
            if match:
                # C: case sensitive; C0 and C1 are not
                case = re.search(r"c(?![01])", match["options"], re.I) is not None
                found.append(ScriptHotstring(number, match["abbr"], case))
                break
    return found


def takes(hotkey: ScriptHotkey, spec: str) -> bool:
    """Whether the script's hotkey fires on Switcher's hotkey ``spec``."""
    parsed = parse_hotkey(spec)
    if parsed is None:
        return False
    if isinstance(parsed, str):  # "double_shift": a hotkey on Shift itself gets the taps
        return hotkey.key == parsed.split("_", 1)[1] and (not hotkey.mods or hotkey.wildcard)
    mods, key = parsed
    return hotkey.key == key and (hotkey.mods == mods or (hotkey.wildcard and hotkey.mods <= mods))


def find_conflicts(text: str, config) -> list[Conflict]:
    """Every place where the script and Switcher's settings want the same keys, by line."""
    conflicts = []
    titles = dict(ACTIONS)
    for hotkey in script_hotkeys(text):
        for name, spec in vars(config.hotkeys).items():
            if spec and takes(hotkey, spec):
                outcome = ("сработают и скрипт, и Switcher" if hotkey.passthrough
                           else "сработает скрипт, а Switcher — нет")
                conflicts.append(Conflict(hotkey.line, f"{hotkey.written} — это {format_hotkey(spec)}, в Switcher "
                                                       f"«{titles.get(name, name)}»: {outcome}"))
    starts = list(config.snippets) if config.snippets_enabled else []
    where = " в окнах сохранения" if config.snippets_only_in_save_dialogs else ""
    for hotstring in script_hotstrings(text):
        abbr = hotstring.abbr if hotstring.case_sensitive else hotstring.abbr.lower()
        for start in starts:
            mine = start if hotstring.case_sensitive else start.lower()
            if abbr == mine:
                conflicts.append(Conflict(hotstring.line, f"замена «{hotstring.abbr}» — это и шаблон дописывания "
                                                          f"Switcher «{start}»: сработают оба{where}"))
            elif abbr.startswith(mine):
                conflicts.append(Conflict(hotstring.line, f"замена «{hotstring.abbr}» начинается с шаблона "
                                                          f"Switcher «{start}»: Switcher допишет раньше{where}"))
    return sorted(set(conflicts), key=lambda c: (c.line, c.message))
