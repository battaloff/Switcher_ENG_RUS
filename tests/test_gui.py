import threading

import pytest

tk = pytest.importorskip("tkinter")
# One Tk interpreter for the whole module: creating many in one process is
# flaky on Windows ("Can't find a usable init.tcl").
try:
    ROOT = tk.Tk()
    ROOT.withdraw()
except tk.TclError:
    pytest.skip("no display for Tk", allow_module_level=True)

from switcher import autostart, gui  # noqa: E402
from switcher.config import Config  # noqa: E402
from switcher.secrets import reveal  # noqa: E402


class FakeApp:
    def __init__(self, profile, keyboard):
        self.config = Config()
        self.profile = profile
        self.keyboard = keyboard
        self.stop_event = threading.Event()
        self.saved = []

    def update_config(self, new, save=True):
        self.saved.append(new)

    def ai_ready(self):
        return False


@pytest.fixture
def window(profile, keyboard, monkeypatch):
    monkeypatch.setattr(autostart, "is_enabled", lambda: False)
    monkeypatch.setattr(autostart, "enable", lambda: "on")
    errors = []
    monkeypatch.setattr(gui.messagebox, "showerror", lambda *a, **k: errors.append(a))
    app = FakeApp(profile, keyboard)
    ui = gui.Ui(app, root=ROOT)
    ui.open_settings(welcome=True)
    ui.root.update()
    ui.window.errors = errors
    yield ui.window
    ui.window.destroy()
    ROOT.update()


def test_settings_are_saved(window):
    window.var_threshold.set(3.0)
    window.var_look_back.set(False)
    window.var_key.set("sk-ant-test-key")
    window.var_hotkeys["toggle"].set("<ctrl>+<alt>+p")
    window.var_excluded.set("keepass, Bitwarden ,")
    window.save()
    new = window.app.saved[-1]
    assert new.threshold == 3.0
    assert new.look_back is False
    assert new.hotkeys.toggle == "<ctrl>+<alt>+p"
    assert new.excluded_apps == ["keepass", "Bitwarden"]
    assert reveal(new.ai.api_key) == "sk-ant-test-key"
    assert "sk-ant-test-key" not in new.ai.api_key
    # the key stays hidden and is kept when the field is not touched
    assert window.var_key.get() == gui.KEY_PLACEHOLDER
    window.save()
    assert reveal(window.app.saved[-1].ai.api_key) == "sk-ant-test-key"


def test_bad_hotkey_is_rejected(window):
    window.var_hotkeys["toggle"].set("<ctrl>+<alt>")
    window.save()
    assert window.errors and not window.app.saved


def test_rules_and_stats_tabs(window, profile):
    profile.add_rule("layout", "ghbdtn", "en", source="user")
    window.refresh_rules()
    items = window.tree.get_children()
    assert len(items) == 1
    assert window.tree.item(items[0])["values"][0] == "ghbdtn"
    window.tree.selection_set(items[0])
    window.remove_rules()
    assert profile.rules() == []
    window.show("stats")
    assert "Правил: 0" in window.stats.get("1.0", "end")


def test_paste_button_takes_the_key_from_the_clipboard(window):
    window.clipboard_clear()
    window.clipboard_append("  sk-ant-from-clipboard\n")
    window.paste_key()
    assert window.var_key.get() == "sk-ant-from-clipboard"


@pytest.mark.skipif(not gui._SHORTCUT_KEYCODES, reason="no keycode table for this platform")
def test_ctrl_v_works_with_the_russian_layout(window):
    from types import SimpleNamespace

    window.clipboard_clear()
    window.clipboard_append("sk-ant-ru-layout")
    window.var_key.set("")
    paste_code = next(code for code, action in gui._SHORTCUT_KEYCODES.items() if action == "<<Paste>>")
    event = SimpleNamespace(keysym="Cyrillic_em", keycode=paste_code, widget=window.key_entry)
    assert gui.ctrl_shortcut(event) == "break"
    window.update()
    assert window.var_key.get() == "sk-ant-ru-layout"
    # with the Latin layout Tk's own binding does the job
    assert gui.ctrl_shortcut(SimpleNamespace(keysym="v", keycode=paste_code, widget=window.key_entry)) is None
