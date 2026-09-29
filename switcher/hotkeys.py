"""Hotkeys as the settings window shows and records them.

Specs are stored in pynput style ("<ctrl>+<alt>+c", "<pause>") or as a double
tap of a modifier ("double_shift", "double_ctrl").  Keys are named by their
physical position (the US QWERTY character), so Ctrl+Alt+C recorded with the
Russian layout active is still Ctrl+Alt+C.
"""

from __future__ import annotations

import re
import sys
import unicodedata

from .controller import DOUBLE_TAPS, parse_hotkey
from .layouts import DEFAULT_KEYBOARD

MODIFIER_KEYSYMS = {
    "Shift_L": "shift", "Shift_R": "shift", "Control_L": "ctrl", "Control_R": "ctrl",
    "Alt_L": "alt", "Alt_R": "alt", "Meta_L": "alt", "Meta_R": "alt", "ISO_Level3_Shift": "alt",
    "Win_L": "cmd", "Win_R": "cmd", "Super_L": "cmd", "Super_R": "cmd",
}
MODIFIER_ORDER = ("ctrl", "alt", "shift", "cmd")

# Tk keysym → pynput Key name
SPECIAL_KEYSYMS = {
    "Pause": "pause", "Break": "pause", "Scroll_Lock": "scroll_lock", "Insert": "insert", "Print": "print_screen",
    "Menu": "menu", "App": "menu", "space": "space", "Return": "enter", "Tab": "tab", "BackSpace": "backspace",
    "Delete": "delete", "Home": "home", "End": "end", "Prior": "page_up", "Next": "page_down",
    "Left": "left", "Right": "right", "Up": "up", "Down": "down", "Escape": "esc",
}
# Keys that make sense as a hotkey on their own, without modifiers.
STANDALONE = {"pause", "scroll_lock", "insert", "print_screen", "menu"} | {f"f{i}" for i in range(1, 25)}
# Keys that must not become hotkeys without Ctrl/Alt/Win: they are needed for typing.
TYPING_KEYS = {"space", "enter", "tab", "backspace", "delete", "esc", "home", "end", "page_up", "page_down",
               "left", "right", "up", "down"}

_WINDOWS_VK = {0x30 + i: str(i) for i in range(10)}
_WINDOWS_VK.update({0x41 + i: chr(ord("a") + i) for i in range(26)})
_WINDOWS_VK.update({0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".", 0xBF: "/", 0xC0: "`",
                    0xDB: "[", 0xDC: "\\", 0xDD: "]", 0xDE: "'"})
_PUNCT_KEYSYMS = {
    "comma": ",", "period": ".", "semicolon": ";", "apostrophe": "'", "quoteright": "'", "bracketleft": "[",
    "bracketright": "]", "grave": "`", "quoteleft": "`", "minus": "-", "equal": "=", "slash": "/",
    "backslash": "\\",
}
_CYRILLIC_NAMES = {"shorti": "SHORT I", "softsign": "SOFT SIGN", "hardsign": "HARD SIGN"}

KEY_LABELS = {
    "space": "Пробел", "pause": "Pause", "scroll_lock": "Scroll Lock", "insert": "Insert",
    "print_screen": "PrtSc", "menu": "Menu", "enter": "Enter", "tab": "Tab", "backspace": "Backspace",
    "delete": "Delete", "home": "Home", "end": "End", "page_up": "PgUp", "page_down": "PgDn",
    "left": "←", "right": "→", "up": "↑", "down": "↓", "esc": "Esc",
}
MOD_LABELS = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "cmd": "Win" if sys.platform == "win32" else "Cmd"}


def _keysym_char(keysym: str) -> str | None:
    if len(keysym) == 1:
        return keysym
    if keysym in _PUNCT_KEYSYMS:
        return _PUNCT_KEYSYMS[keysym]
    if keysym.startswith("Cyrillic_"):
        name = keysym[len("Cyrillic_"):]
        case = "CAPITAL" if name[:1].isupper() else "SMALL"
        try:
            return unicodedata.lookup(f"CYRILLIC {case} LETTER {_CYRILLIC_NAMES.get(name.lower(), name.upper())}")
        except KeyError:
            return None
    return None


def key_name(keysym: str, keycode: int, char: str, platform: str = sys.platform) -> str | None:
    """Physical key of a Tk key event, in the names parse_hotkey understands."""
    if keysym in SPECIAL_KEYSYMS:
        return SPECIAL_KEYSYMS[keysym]
    if re.fullmatch(r"F([1-9]|1[0-9]|2[0-4])", keysym):
        return keysym.lower()
    if platform == "win32" and keycode in _WINDOWS_VK:
        return _WINDOWS_VK[keycode]
    for candidate in (char if char and len(char) == 1 and char.isprintable() and not char.isspace() else None,
                      _keysym_char(keysym)):
        if candidate:
            found = DEFAULT_KEYBOARD.stroke_for_char(candidate, None)
            return found[0].code if found else candidate.lower()
    return None


def build_spec(mods: set[str], key: str) -> str:
    parts = [f"<{m}>" for m in MODIFIER_ORDER if m in mods]
    parts.append(key if len(key) == 1 else f"<{key}>")
    return "+".join(parts)


def problem(spec: str) -> str | None:
    """Why ``spec`` would get in the way of typing, or None if it is fine."""
    parsed = parse_hotkey(spec)
    if parsed is None:
        return "не понял сочетание"
    if isinstance(parsed, str):
        return None
    mods, key = parsed
    if not mods and key not in STANDALONE:
        return "одна клавиша без Ctrl/Alt помешает печатать — добавьте Ctrl или Alt"
    if mods <= {"shift"} and key not in STANDALONE and (len(key) == 1 or key in TYPING_KEYS):
        return "Shift с этой клавишей нужен для набора текста — добавьте Ctrl или Alt"
    return None


def format_hotkey(spec: str) -> str:
    """Human-readable form: "Ctrl + Alt + C", "Shift дважды", "Pause"."""
    spec = (spec or "").strip().lower()
    if not spec:
        return "— не назначено —"
    if spec in DOUBLE_TAPS:
        return f"{MOD_LABELS[spec.split('_', 1)[1]]} дважды"
    parsed = parse_hotkey(spec)
    if parsed is None:
        return spec
    mods, key = parsed
    label = KEY_LABELS.get(key) or (key.upper() if len(key) == 1 or re.fullmatch(r"f\d+", key) else key.title())
    return " + ".join([MOD_LABELS[m] for m in MODIFIER_ORDER if m in mods] + [label])
