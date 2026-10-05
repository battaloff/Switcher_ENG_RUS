"""Listing, reassigning and adding the hotkeys of AutoHotkey scripts."""

import pytest

from switcher.hotkeys import format_hotkey

from switcher.ahk_hotkeys import (action_code, add_binding, ahk_label, list_bindings, my_script, relabel,
                                  remove_line)

SCRIPT = """#Requires AutoHotkey v2.0
; сегодняшняя дата
^!d::SendText FormatTime(, "dd.MM.yyyy")
#n::Run "notepad.exe"   ; Блокнот
~*F7 Up::
{
    MsgBox "F7"
}
::btw::by the way
Hotkey("^!q", Quit)
"""


def test_every_hotkey_and_hotstring_is_listed_with_what_it_does():
    found = {(b.line, b.kind): b for b in list_bindings("my.ahk", SCRIPT)}
    date = found[(3, "hotkey")]
    assert (date.keys, date.what, date.spec, date.one_line) == ("Ctrl + Alt + D", "сегодняшняя дата",
                                                                "<ctrl>+<alt>+d", True)
    assert found[(4, "hotkey")].what == "Блокнот" and found[(4, "hotkey")].keys == format_hotkey("<cmd>+n")
    f7 = found[(5, "hotkey")]
    assert f7.keys == "F7 (и дальше в программу)" and not f7.one_line and f7.what == "несколько строк"
    assert found[(9, "hotstring")].keys == "набрать «btw»" and found[(9, "hotstring")].what == "by the way"
    assert found[(10, "hotkey")].what == "Hotkey(…) в коде" and found[(10, "hotkey")].spec == "<ctrl>+<alt>+q"


def test_switchers_keys_in_autohotkeys_words():
    assert ahk_label("<ctrl>+<alt>+d") == "^!d"
    assert ahk_label("<cmd>+<shift>+<space>") == "+#Space"
    assert ahk_label("<ctrl>+<f5>") == "^F5"
    assert ahk_label("<pause>") == "Pause"
    assert ahk_label("<ctrl>+;") == "^`;"
    assert ahk_label("double_shift") is None


def test_a_hotkey_is_moved_keeping_how_it_behaves():
    moved = relabel(SCRIPT, 3, "^!d", "#d")
    assert moved.splitlines()[2] == '#d::SendText FormatTime(, "dd.MM.yyyy")'
    moved = relabel(SCRIPT, 5, "~*F7 Up", "^F8")
    assert moved.splitlines()[4] == "~*^F8 Up::"
    moved = relabel(SCRIPT, 10, "^!q", "^!w")
    assert moved.splitlines()[9] == 'Hotkey("^!w", Quit)'
    with pytest.raises(ValueError):
        relabel(SCRIPT, 4, "^!d", "#d")  # the line is not that hotkey any more


def test_new_hotkeys_without_code():
    assert action_code("text", 'Мой адрес: "дом", 5%', 2) == 'SendText "Мой адрес: `"дом`", 5%"'
    assert action_code("text", "100%, ок; да", 1) == "SendInput {Text}100`%`, ок`; да"
    assert action_code("run", "https://claude.ai", 2) == 'Run "https://claude.ai"'
    assert action_code("run", r"C:\Program Files\App\app.exe", 1) == r"Run, C:\Program Files\App\app.exe"
    assert action_code("code", "  Send \"{Volume_Up}\"  ", 2) == 'Send "{Volume_Up}"'
    text = add_binding("#Requires AutoHotkey v2.0", "^!m", "text", "me@example.com", 2, "почта")
    assert text == "#Requires AutoHotkey v2.0\n\n; почта\n^!m::SendText \"me@example.com\"\n"
    found = list_bindings("x.ahk", text)
    assert [(b.keys, b.what, b.one_line) for b in found] == [("Ctrl + Alt + M", "почта", True)]
    assert remove_line(text, found[0].line) == "#Requires AutoHotkey v2.0\n\n"


def test_switchers_own_script(tmp_path):
    path = my_script(tmp_path / "AutoHotkey", 2)
    assert path.name == "Switcher — клавиши.ahk" and path.read_bytes().startswith(b"\xef\xbb\xbf#Requires")
    path.write_text("changed", encoding="utf-8")
    assert my_script(tmp_path / "AutoHotkey", 2).read_text(encoding="utf-8") == "changed"  # never overwritten
