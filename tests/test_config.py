import json

from switcher.config import load_config


def write(path, hotkeys, **extra):
    path.write_text(json.dumps({"hotkeys": hotkeys, **extra}), encoding="utf-8")


def test_old_selection_hotkey_moves_to_shift_pause_once(tmp_path):
    path = tmp_path / "config.json"
    write(path, {"convert_selection": "<ctrl>+<alt>+c"})
    config = load_config(path)
    assert config.hotkeys.convert_selection == "<shift>+<pause>"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["config_version"] == 1 and saved["hotkeys"]["convert_selection"] == "<shift>+<pause>"
    # chosen again by hand later: stays
    saved["hotkeys"]["convert_selection"] = "<ctrl>+<alt>+c"
    path.write_text(json.dumps(saved), encoding="utf-8")
    assert load_config(path).hotkeys.convert_selection == "<ctrl>+<alt>+c"


def test_the_users_own_keys_are_kept(tmp_path):
    path = tmp_path / "config.json"
    write(path, {"convert_selection": "<ctrl>+<alt>+x"})
    assert load_config(path).hotkeys.convert_selection == "<ctrl>+<alt>+x"
    write(path, {"convert_selection": "<ctrl>+<alt>+c", "ai_fix": "<shift>+<pause>"})
    config = load_config(path)
    assert config.hotkeys.convert_selection == "<ctrl>+<alt>+c" and config.hotkeys.ai_fix == "<shift>+<pause>"


def test_a_new_config_starts_with_shift_pause(tmp_path):
    config = load_config(tmp_path / "config.json")
    assert config.hotkeys.convert_selection == "<shift>+<pause>" and config.config_version == 1
