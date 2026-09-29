import pytest

from switcher.hotkeys import build_spec, format_hotkey, key_name, problem


@pytest.mark.parametrize("spec, shown", [
    ("double_shift", "Shift дважды"),
    ("double_ctrl", "Ctrl дважды"),
    ("<ctrl>+<alt>+c", "Ctrl + Alt + C"),
    ("<ctrl>+<alt>+<space>", "Ctrl + Alt + Пробел"),
    ("<pause>", "Pause"),
    ("<shift>+<f12>", "Shift + F12"),
    ("", "— не назначено —"),
])
def test_format(spec, shown):
    assert format_hotkey(spec) == shown


def test_key_names_are_physical():
    assert key_name("Cyrillic_es", 67, "с", platform="win32") == "c"
    assert key_name("Cyrillic_es", 0, "с", platform="linux") == "c"
    assert key_name("Cyrillic_zhe", 0, "", platform="linux") == ";"   # Ctrl hides the char on X11
    assert key_name("F9", 120, "", platform="win32") == "f9"
    assert key_name("Pause", 19, "", platform="win32") == "pause"
    assert build_spec({"alt", "ctrl"}, "c") == "<ctrl>+<alt>+c"
    assert build_spec(set(), "pause") == "<pause>"


def test_problems():
    assert problem("<ctrl>+<alt>+c") is None
    assert problem("double_shift") is None
    assert problem("<pause>") is None
    assert problem("<shift>+<pause>") is None
    assert "печатать" in problem("k")
    assert "Shift" in problem("<shift>+k")
    assert problem("<ctrl>+<alt>") == "не понял сочетание"
