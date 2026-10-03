import threading

import pytest

tk = pytest.importorskip("tkinter")
pytest.importorskip("customtkinter")

from switcher import autostart, gui  # noqa: E402

# One Tk interpreter for the whole module: creating many in one process is
# flaky on Windows ("Can't find a usable init.tcl").
try:
    ROOT = gui.make_root()
except tk.TclError:
    pytest.skip("no display for Tk", allow_module_level=True)

from switcher.config import Config  # noqa: E402
from switcher.secrets import reveal  # noqa: E402


class FakeApp:
    def __init__(self, profile, keyboard):
        self.config = Config()
        self.profile = profile
        self.keyboard = keyboard
        self.stop_event = threading.Event()
        self.saved = []
        self.releases = []  # nothing to fetch: the updates page shows this list
        self.release_listeners = []
        self.installed = []

    def check_updates(self, force=False):
        return self.releases

    def install_update(self, release, setup):
        self.installed.append((release.version, setup))

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
    window.hotkey_specs["toggle"] = "<ctrl>+<alt>+p"
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
    window.hotkey_specs["toggle"] = "<ctrl>+<alt>"
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
    field = window.key_entry._entry  # the Tk entry inside CustomTkinter's widget gets the keys
    event = SimpleNamespace(keysym="Cyrillic_em", keycode=paste_code, widget=field)
    assert gui.ctrl_shortcut(event) == "break"
    window.update()
    assert window.var_key.get() == "sk-ant-ru-layout"
    # with the Latin layout Tk's own binding does the job
    assert gui.ctrl_shortcut(SimpleNamespace(keysym="v", keycode=paste_code, widget=field)) is None


def key_event(window, keysym, keycode=0, char=""):
    from types import SimpleNamespace

    return SimpleNamespace(keysym=keysym, keycode=keycode, char=char, widget=window.hotkey_labels["toggle"])


def test_record_a_combination(window):
    window.start_recording("toggle")
    window._record_press(key_event(window, "Control_L"))
    window._record_press(key_event(window, "Alt_L"))
    # the K key with the Russian layout active: still Ctrl+Alt+K
    window._record_press(key_event(window, "Cyrillic_el", 75, "л"))
    assert window.hotkey_specs["toggle"] == "<ctrl>+<alt>+k"
    assert window.hotkey_labels["toggle"].cget("text") == "Ctrl + Alt + K"


def test_record_double_ctrl_and_pause(window):
    window.start_recording("toggle")
    for _ in range(2):
        window._record_press(key_event(window, "Control_L"))
        window._record_release(key_event(window, "Control_L"))
    assert window.hotkey_specs["toggle"] == "double_ctrl"
    window.start_recording("convert_last")
    window._record_press(key_event(window, "Pause", 19))
    assert window.hotkey_specs["convert_last"] == "<pause>"


def test_recording_refuses_typing_keys_and_duplicates(window):
    window.start_recording("toggle")
    window._record_press(key_event(window, "k", 75, "k"))
    assert window.hotkey_specs["toggle"] == "<ctrl>+<alt>+s"
    assert "помешает печатать" in window.hotkey_hint.cget("text")
    window._record_press(key_event(window, "Shift_L"))
    window._record_press(key_event(window, "Pause", 19))  # taken by "fix the selection"
    assert window.hotkey_specs["toggle"] == "<ctrl>+<alt>+s"
    assert "уже назначено" in window.hotkey_hint.cget("text")


def test_recording_goes_through_the_window_bindings(window):
    window.show("keys")
    window.start_recording("toggle")
    window.update()
    if window.focus_get() is not window:
        pytest.skip("the test window did not get the keyboard focus")
    window.event_generate("<KeyPress>", keysym="F7", when="now")
    window.update()
    assert window.hotkey_specs["toggle"] == "<f7>"
    assert window.hotkey_labels["toggle"].cget("text") == "F7"


def fake_release(version, relation, notes=("Новое: что-то",)):
    from types import SimpleNamespace

    return SimpleNamespace(version=version, relation=relation, notes=list(notes), date="2026-09-29",
                           prerelease=False, size=31_000_000, url="https://example.invalid", asset_name="x.exe")


def test_updates_page_lists_versions_with_the_right_buttons(window):
    releases = [fake_release("0.3.0", "newer", ["Новое: тёмная тема", "Исправлено: вставка ключа"]),
                fake_release("0.2.0", "current"), fake_release("0.1.9", "older")]
    window.show_releases(releases)
    assert [b.cget("text") for b in window.release_buttons] == ["Обновить", "Откатить"]
    assert window.nav["updates"][1].cget("text") == "Обновления  ●"
    texts = [w.cget("text") for w in _labels(window.release_box)]
    assert "•  Новое: тёмная тема\n•  Исправлено: вставка ключа" in texts
    assert "Установлена" in texts


def _labels(widget):
    import customtkinter as ctk

    for child in widget.winfo_children():
        if isinstance(child, ctk.CTkLabel):
            yield child
        yield from _labels(child)


def test_update_downloads_confirms_and_installs(window, monkeypatch, tmp_path):
    from switcher import updater

    release = fake_release("0.3.0", "newer")
    monkeypatch.setattr(updater, "can_install", lambda: True)
    asked = []
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda title, text, **k: asked.append(text) or True)

    def download(rel, progress=None):
        progress(15, 30)
        return tmp_path / "SwitcherSetup-0.3.0.exe"

    monkeypatch.setattr(updater, "download", download)
    window.show_releases([release])
    window.choose_release(release)
    assert asked and asked[0].startswith("Обновиться до версии 0.3.0?")
    for _ in range(50):  # the download runs in a thread and reports through ui.call
        while not window.ui._calls.empty():
            window.ui._calls.get_nowait()()
        if window.app.installed:
            break
        window.after(20)
        window.update()
    assert window.app.installed == [("0.3.0", tmp_path / "SwitcherSetup-0.3.0.exe")]
    assert "перезапустится" in window.update_status.cget("text")


def test_rollback_is_offered_but_can_be_declined(window, monkeypatch):
    from switcher import updater

    release = fake_release("0.1.9", "older")
    monkeypatch.setattr(updater, "can_install", lambda: True)
    asked = []
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda title, text, **k: asked.append(text) or False)
    window.show_releases([release])
    window.choose_release(release)
    assert asked[0].startswith("Откатиться на версию 0.1.9?")
    assert window.app.installed == []
    assert all(b.cget("state") == "normal" for b in window.release_buttons)


def test_auto_update_setting_is_saved(window):
    window.var_auto_update.set(False)
    window.save()
    assert window.app.saved[-1].updates.check_automatically is False


def test_early_switch_and_autocorrect_can_be_turned_off(window):
    assert window.var_early.get() is True and window.var_autocorrect.get() is True
    window.var_early.set(False)
    window.var_autocorrect.set(False)
    window.save()
    saved = window.app.saved[-1]
    assert saved.early_switch is False and saved.autocorrect is False


def test_latest_version_is_said_plainly(window):
    window.show_releases([fake_release("0.2.0", "current"), fake_release("0.1.9", "older")])
    assert window.update_status.cget("text").startswith("У вас последняя версия ✓")
    assert "Подробнее на GitHub →" in [w.cget("text") for w in _labels(window.release_box)]
    window.show_releases([fake_release("0.3.0", "newer"), fake_release("0.2.0", "current")])
    assert not window.update_status.cget("text").startswith("У вас последняя версия")


def test_version_list_can_be_refreshed(window):
    releases = [fake_release("0.3.0", "newer"), fake_release("0.2.0", "current"), fake_release("0.1.9", "older")]
    window.show_releases(releases)
    window.show_releases(releases)  # "Проверить" again: the list is rebuilt, not broken halfway
    assert [b.cget("text") for b in window.release_buttons] == ["Обновить", "Откатить"]


def test_snippets_take_effect_at_once(window, monkeypatch):
    answers = iter(["015", "015-510-400_4_"])
    monkeypatch.setattr(window, "_ask", lambda title, text: next(answers))
    window.add_snippet()
    assert window.app.saved[-1].snippets == {"015": "015-510-400_4_"}
    assert window.snippet_tree.get_children() == ("015",)
    window.snippet_tree.selection_set("015")
    window.remove_snippets()
    assert window.app.saved[-1].snippets == {} and window.snippet_tree.get_children() == ()
    assert window.collect().snippets == {}
