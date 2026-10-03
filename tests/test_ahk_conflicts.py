"""Telling where an AutoHotkey script takes keys that Switcher uses."""

from switcher.ahk_conflicts import find_conflicts, parse_label, script_hotkeys, script_hotstrings
from switcher.config import Config

SCRIPT = """#Requires AutoHotkey v2.0
^!s::MsgBox "мой Ctrl+Alt+S"   ; занято и у Switcher
^!d::SendText "дата"
/*
^!Space::это комментарий
*/
::015::другое
(
+Pause::это текст, а не клавиша
)
"""


def config(**hotkeys):
    c = Config()
    c.snippets = {"015": "015-510-400_4_"}
    for name, spec in hotkeys.items():
        setattr(c.hotkeys, name, spec)
    return c


def test_hotkeys_are_read_the_way_autohotkey_writes_them():
    assert parse_label("^!s").mods == {"ctrl", "alt"} and parse_label("^!s").key == "s"
    assert parse_label("+Pause").mods == {"shift"} and parse_label("+Pause").key == "pause"
    assert parse_label("^!ы").key == "s"  # the same physical key, typed on the Russian layout
    assert parse_label("#n").mods == {"cmd"}
    assert parse_label("<^>!a").mods == {"ctrl", "alt"}  # AltGr
    assert parse_label("~LShift").key == "shift" and parse_label("~LShift").passthrough
    assert parse_label("*s").wildcard
    assert parse_label("^!s Up").key == "s"
    assert parse_label("LCtrl & s").mods == {"ctrl"}
    assert parse_label("CapsLock & s") is None  # a custom prefix key: nothing Switcher uses
    assert parse_label("LButton") is None
    assert parse_label("+").key == "=" and not parse_label("+").mods  # the "+" key itself: Shift+= on US keys


def test_comments_and_text_sections_are_not_hotkeys():
    found = script_hotkeys(SCRIPT)
    assert [(h.line, h.written) for h in found] == [(2, "^!s"), (3, "^!d")]
    assert [(h.line, h.abbr) for h in script_hotstrings(SCRIPT)] == [(7, "015")]
    assert [h.written for h in script_hotkeys('Hotkey("^!Space", Fix)\nHotkey, ^!x, Label\n')] == ["^!Space", "^!x"]


def test_a_script_taking_switchers_keys_is_found():
    found = find_conflicts(SCRIPT, config())
    assert [c.line for c in found] == [2, 7]
    assert "Ctrl + Alt + S" in found[0].message and "«Пауза»" in found[0].message
    assert "сработает скрипт, а Switcher — нет" in found[0].message
    assert "шаблон дописывания Switcher «015»" in found[1].message


def test_what_does_and_does_not_collide():
    c = config()
    assert find_conflicts("~^!s::Beep\n", c)[0].message.endswith("сработают и скрипт, и Switcher")
    assert find_conflicts("*s::Beep\n", c)  # any modifiers: Ctrl+Alt+S too
    assert not find_conflicts("^!+s::Beep\n", c)  # with Shift as well: another hotkey
    assert not find_conflicts("^s::Beep\n", c)
    assert find_conflicts("~Shift::Beep\n", c)  # sees every Shift: the double Shift too
    assert not find_conflicts("^Shift::Beep\n", c)
    assert find_conflicts("::0155::x\n", c)  # Switcher completes "015" before "0155" is typed
    assert not find_conflicts(":C:Abc::x\n", config()) and not find_conflicts("::01::x\n", c)
    c.snippets_enabled = False
    assert not find_conflicts("::015::x\n", c)
    assert not find_conflicts("^!s::x\n", config(toggle=""))  # the hotkey is off in Switcher


def test_switchers_own_hotkeys_when_changed():
    c = config(ai_fix="<ctrl>+<alt>+d")
    found = find_conflicts(SCRIPT, c)
    assert [c.line for c in found] == [2, 3, 7]
    assert "Исправить фразу с Claude" in found[1].message
