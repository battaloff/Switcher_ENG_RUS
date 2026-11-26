import threading
import time

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


class FakeAhkSystem:
    supported = True

    def __init__(self):
        self.windows = {}

    def running(self):
        return dict(self.windows)

    def command(self, window, command):
        from switcher import ahk

        if command == ahk.ID_EXIT:
            self.windows = {p: w for p, w in self.windows.items() if w != window}
        return True

    def launch(self, path):
        self.windows[path] = 42

    def edit_elsewhere(self, path):
        pass

    def install_roots(self):
        return []

    def major_of(self, path):
        return None

    def run(self, args, timeout):
        return 0, ""

    def startup_scripts(self):
        return []


def test_autohotkey_scripts_page_and_editor(profile, keyboard, monkeypatch, tmp_path):
    from switcher import ahk, ahk_editor

    monkeypatch.setattr(autostart, "is_enabled", lambda: False)
    app = FakeApp(profile, keyboard)
    system = FakeAhkSystem()
    running = tmp_path / "running.ahk"
    running.write_text("#Requires AutoHotkey v2.0\n::btw::by the way\n", encoding="utf-8")
    system.windows = {str(running): 7}
    app.ahk = ahk.AhkManager(app.config, system=system)
    ui = gui.Ui(app, root=ROOT)
    ui.open_settings(tab="ahk")
    window = ui.window
    ROOT.update()
    try:
        # the first visit picks up the script already running
        assert app.saved[-1].ahk_scripts == {str(running): False}
        rows = [window.ahk_tree.item(iid, "values") for iid in window.ahk_tree.get_children()]
        assert rows == [("running", "работает", "—", str(tmp_path))]
        window.ahk_tree.selection_set(window.ahk_tree.get_children()[0])
        window.toggle_script_autostart()
        assert app.saved[-1].ahk_scripts == {str(running): True}
        window.toggle_scripts()
        assert not system.windows
        assert window.ahk_tree.item(window.ahk_tree.get_children()[0], "values")[1] == "остановлен"

        monkeypatch.setattr(window, "scripts_folder", lambda: tmp_path / "scripts")
        monkeypatch.setattr(window, "_ask", lambda title, text: "Мои клавиши")
        window.create_script()
        created = tmp_path / "scripts" / "Мои клавиши.ahk"
        assert created.exists() and app.saved[-1].ahk_scripts[str(created)] is True
        editor = ui.editors[-1]
        ROOT.update()
        assert "#Requires AutoHotkey v2.0" in editor.content()
        assert {"directive", "hotkey", "comment"} <= {tag for tag, _, _ in ahk_editor.spans(editor.content(), 2)}

        editor.text.insert("end", "::мб::может быть\n")
        assert editor.dirty
        editor.save()
        assert "::мб::может быть" in created.read_text(encoding="utf-8-sig") and not editor.dirty
        editor.show_error(2, "Строка 2: ошибка")
        assert editor.text.tag_ranges("error")

        class Claude:
            def write_ahk(self, request, script, major):
                return script + "^!t::SendText \"тест\"\n", "Добавил Ctrl+Alt+T — слово «тест»."

        class Dialog:
            def __init__(self, **kw):
                pass

            def get_input(self):
                return "Ctrl+Alt+T — напечатать «тест»"

        app.assistant, app.ai_ready = Claude(), lambda: True
        monkeypatch.setattr(ahk_editor.ctk, "CTkInputDialog", Dialog)
        monkeypatch.setattr(ahk_editor, "_set_icon", lambda window: None)
        editor.ask_claude()
        for _ in range(200):
            ROOT.update()
            if "^!t::" in editor.content():
                break
            time.sleep(0.01)
        assert '^!t::SendText "тест"' in editor.content() and editor.dirty  # shown, not saved behind the back
        assert "Ctrl+Alt+T" in editor.status.cget("text")
        editor.text.edit_undo()
        assert "^!t::" not in editor.content()
        assert ui.open_script(str(created)) is editor  # one editor per script
        editor.text_value = editor.content()  # nothing unsaved: closes without asking
        editor.close()
    finally:
        for editor in ui.editors:
            if editor.winfo_exists():
                editor.destroy()
        window.destroy()
        ROOT.update()


def test_snippets_can_be_switched_off_in_the_settings(window):
    assert window.var_snippets_save.get() is True  # file names only, unless the user says otherwise
    window.var_snippets.set(False)
    window.var_snippets_save.set(False)
    new = window.collect()
    assert new.snippets_enabled is False and new.snippets_only_in_save_dialogs is False


def test_clashes_with_autohotkey_are_shown(profile, keyboard, monkeypatch, tmp_path):
    from switcher import ahk

    monkeypatch.setattr(autostart, "is_enabled", lambda: False)
    app = FakeApp(profile, keyboard)
    clash = tmp_path / "clash.ahk"
    clash.write_text("#Requires AutoHotkey v2.0\n^!s::MsgBox 1\n^!q::MsgBox 2\n", encoding="utf-8")
    app.config.ahk_scripts = {str(clash): False}
    app.ahk = ahk.AhkManager(app.config, system=FakeAhkSystem())
    ui = gui.Ui(app, root=ROOT)
    ui.open_settings(tab="ahk")
    window = ui.window
    ROOT.update()
    try:
        assert window.ahk_tree.item(window.ahk_tree.get_children()[0], "values")[1] == "остановлен ⚠"
        text = window.ahk_clashes.cget("text")
        assert "«clash», строка 2: ^!s — это Ctrl + Alt + S, в Switcher «Пауза»" in text

        assert window.set_hotkey("ai_fix", "<ctrl>+<alt>+q")  # allowed, but Switcher is told who else has it
        assert "занято скриптом AutoHotkey «clash» (строка 3: ^!q)" in window.hotkey_hint.cget("text")
        assert window.set_hotkey("ai_fix", "<ctrl>+<alt>+w")
        assert "занято" not in window.hotkey_hint.cget("text")

        editor = ui.open_script(str(clash))
        ROOT.update()
        editor.save()
        assert editor.text.tag_ranges("conflict")  # line 2 marked
        assert "строка 2" in editor.status.cget("text")
        editor.text.delete("2.0", "2.0 lineend")
        editor.text.insert("2.0", "^!z::MsgBox 1")
        editor.save()
        assert not editor.text.tag_ranges("conflict")
        assert editor.status.cget("text") == "Сохранено ✓"
        editor.close()
    finally:
        for editor in ui.editors:
            if editor.winfo_exists():
                editor.destroy()
        window.destroy()
        ROOT.update()


def test_autohotkey_hotkeys_are_listed_reassigned_and_added(profile, keyboard, monkeypatch, tmp_path):
    from switcher import ahk, ahk_dialogs
    from switcher.ahk_hotkeys import MY_SCRIPT

    monkeypatch.setattr(autostart, "is_enabled", lambda: False)
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *a, **k: True)
    app = FakeApp(profile, keyboard)
    script = tmp_path / "keys.ahk"
    script.write_text('#Requires AutoHotkey v2.0\n; дата\n^!d::SendText "сегодня"\n::btw::by the way\n',
                      encoding="utf-8")
    system = FakeAhkSystem()
    system.windows = {str(script): 3}
    app.config.ahk_scripts = {str(script): True}
    app.ahk = ahk.AhkManager(app.config, system=system)
    ui = gui.Ui(app, root=ROOT)
    ui.open_settings(tab="ahk_keys")
    window = ui.window
    ROOT.update()
    try:
        rows = [window.keys_tree.item(iid, "values") for iid in window.keys_tree.get_children()]
        assert rows == [("Ctrl + Alt + D", "дата", "keys, стр. 3"), ("набрать «btw»", "by the way", "keys, стр. 4")]
        binding = window._bindings[window.keys_tree.get_children()[0]]
        assert window.assign_binding(binding, "<ctrl>+<alt>+t") is None
        assert '^!t::SendText "сегодня"' in script.read_text(encoding="utf-8")
        assert window.keys_tree.item(window.keys_tree.get_children()[0], "values")[0] == "Ctrl + Alt + T"

        monkeypatch.setattr(window, "scripts_folder", lambda: tmp_path / "AutoHotkey")
        dialog = ahk_dialogs.AddHotkeyDialog(window, window.add_hotkey)
        ROOT.update()
        dialog.recording = True
        dialog._recorded("<ctrl>+<alt>+m")
        dialog.value.insert(0, "me@example.com")
        dialog.note.insert(0, "почта")
        dialog.done()
        mine = tmp_path / "AutoHotkey" / MY_SCRIPT
        assert '^!m::SendText "me@example.com"' in mine.read_text(encoding="utf-8-sig")
        assert app.saved[-1].ahk_scripts[str(mine)] is True and str(mine) in system.windows  # started
        keys = {window.keys_tree.item(iid, "values")[0]: iid for iid in window.keys_tree.get_children()}
        window.keys_tree.selection_set(keys["Ctrl + Alt + M"])
        window.remove_selected_binding()
        assert "^!m::" not in mine.read_text(encoding="utf-8-sig")
        window.keys_tree.selection_set(window.keys_tree.get_children()[0])
        window.remove_selected_binding()  # not Switcher's own: only in the editor
        assert "^!t::" in script.read_text(encoding="utf-8") and "в редакторе" in window.keys_hint.cget("text")
        assert window.assign_binding(binding, "double_shift") is not None
    finally:
        for editor in ui.editors:
            if editor.winfo_exists():
                editor.destroy()
        window.destroy()
        ROOT.update()


# -- the «Сохранить как» panel ---------------------------------------------------------------------

class FakeDialogs:
    """The Windows side of the panel: one save dialog, a folder in it, the clipboard."""

    def __init__(self):
        from switcher.savebar import Geometry

        self.front = 0
        self.dialogs = {77: Geometry((100, 100, 1500, 900), (108, 160, 1492, 196), 330, 1.0, "#191919")}
        self.folders = {77: (r"\\srv\f\2026\НОЯБРЬ\2,11,26\готов\Заказ   (id_1)", "Заказ   (id_1)")}
        self.copied, self.made, self.went, self.shown, self.carets = [], [], [], {}, []
        self.new_folders, self.subs, self.folder_made = [], {"PDF"}, True
        self.fail = None

    def foreground(self):
        return self.front

    def is_save_dialog(self, hwnd):
        return hwnd in self.dialogs

    def alive(self, hwnd):
        return True

    def geometry(self, hwnd, measure=True):
        return self.dialogs.get(hwnd)

    def dialog_rect(self, hwnd):
        return self.dialogs[hwnd].dialog if hwnd in self.dialogs else None

    def folder(self, hwnd):
        return self.folders.get(hwnd)

    def make_folder(self, path):
        if self.fail:
            raise self.fail
        self.made.append(path)

    def go_into(self, hwnd, folder):
        import ntpath

        self.went.append(folder)
        self.folders[hwnd] = (folder, ntpath.basename(folder))
        return True

    def copy(self, text):
        self.copied.append(text)

    def new_folder(self, hwnd):
        self.new_folders.append(hwnd)
        if self.folder_made:
            self.subs.add("Новая папка")
        return True

    def subfolders(self, path):
        return set(self.subs)

    def caret_to_start(self, hwnd):
        self.carets.append(hwnd)

    def prepare(self, window):
        window.update_idletasks()
        return window.winfo_id()

    def show(self, hwnd, x, y):
        self.shown[hwnd] = (x, y)

    def hide(self, hwnd):
        self.shown.pop(hwnd, None)

    def visible(self, hwnd):
        return hwnd in self.shown

    def focus(self, hwnd):
        self.front = hwnd


class BarApp:
    def __init__(self):
        self.config = Config()
        self.stop_event = threading.Event()
        self.notes = []
        self.backend = type("Backend", (), {})()
        self.backend.notify = self.notes.append


@pytest.fixture
def bar():
    from switcher.savebar_ui import SaveBarPanel

    app, dialogs = BarApp(), FakeDialogs()
    panel = SaveBarPanel(ROOT, app, system=dialogs)
    panel.SETTLE = 0
    panel.app_, panel.dialogs = app, dialogs
    yield panel
    panel.hide()
    if panel.window is not None:
        panel.window.destroy()
    ROOT.update()


def test_the_panel_shows_on_a_save_dialog_only(bar):
    dialogs, app = bar.dialogs, bar.app_
    bar.update()
    assert not dialogs.shown and bar.dialog == 0  # nothing in front
    dialogs.front = 77
    bar.update()
    assert bar.dialog == 77 and bar.where == "inside" and dialogs.shown[bar.hwnd][0] == 330 + 16  # the gap
    assert [b.label for b in bar.buttons] == ["Копировать имя папки", "PANCH", "PANCH + ЗАГИБ", "ОБЖИГ"]
    assert app.backend.save_bar_dialog == 77
    assert app.backend.save_bar_keys[(frozenset({"ctrl", "shift"}), 0x32)] == 1
    dialogs.front = 5  # another window: an «Открыть» dialog, a message box over the dialog, another program
    bar.update()
    assert not dialogs.shown and app.backend.save_bar_dialog == 0
    dialogs.front = 77
    app.config.save_bar.enabled = False
    bar.update()
    assert not dialogs.shown
    app.config.save_bar.enabled = True
    app.config.save_bar.place = "above"
    bar.update()
    assert bar.where == "above" and dialogs.shown[bar.hwnd][1] < 100
    app.config.save_bar.place = "inside"
    moved = dialogs.dialogs[77]
    dialogs.dialogs[77] = type(moved)((150, 120, 1550, 920), (158, 180, 1542, 216), 380, 1.0, "#191919")
    bar.update()  # the dialog was dragged: the panel follows at once
    assert dialogs.shown[bar.hwnd][0] == 380 + 16


def test_a_button_copies_the_folder_name_and_opens_pdf(bar):
    dialogs, app = bar.dialogs, bar.app_
    dialogs.front = 77
    bar.update()
    order = dialogs.folders[77][0]
    assert bar.run(bar.buttons[0], 77) == "Заказ   (id_1)"
    assert dialogs.made == [order + "\\PDF"] and dialogs.went == [order + "\\PDF"]
    assert bar.run(bar.buttons[1], 77) == "Заказ   (id_1) PANCH"  # now in "PDF": the order's name, no PDF\PDF
    assert dialogs.made == [order + "\\PDF"] and dialogs.copied == ["Заказ   (id_1)", "Заказ   (id_1) PANCH"]
    assert dialogs.carets == [77, 77]  # the caret goes before the file name each time
    assert app.notes == []  # notifications are off by default
    app.config.save_bar.notifications = True
    bar.run(bar.buttons[3], 77)
    assert app.notes == ["Скопировано: Заказ   (id_1) ОБЖИГ"]


def test_an_edited_button_shows_at_once(bar):
    import copy

    dialogs, app = bar.dialogs, bar.app_
    dialogs.front = 77
    bar.update()
    first = bar.window
    edited = copy.deepcopy(app.config)
    edited.save_bar.buttons[1].update(label="PANCH_", suffix=" PANCH_")
    app.config.__dict__.update(edited.__dict__)  # what App.update_config does
    bar.update()
    assert bar.window is not first and bar.dialog == 77 and bar.hwnd in dialogs.shown
    assert [b.label for b in bar.buttons][:2] == ["Копировать имя папки", "PANCH_"]
    assert bar.run(bar.buttons[1], 77) == "Заказ   (id_1) PANCH_"
    assert not first.winfo_exists()


def test_buttons_are_drawn_in_their_colours(bar):
    dialogs, app = bar.dialogs, bar.app_
    app.config.save_bar.buttons[0]["color"] = "#3E63DD"
    app.config.save_bar.buttons[2]["color"] = "#30A46C"
    dialogs.front = 77
    bar.update()
    from switcher import savebar_ui

    widgets = bar.window.winfo_children()
    usual = savebar_ui.OTHER[savebar_ui.ctk.get_appearance_mode() == "Dark"]
    assert [w.cget("fg_color") for w in widgets[:4]] == ["#3E63DD", usual, "#30A46C", usual]
    assert widgets[0].cget("text_color") == "#FFFFFF"  # white reads better on blue
    # the middle of each gap between buttons (the Windows check looks there for the dialog behind)
    first = widgets[0].winfo_reqwidth()
    assert len(bar.gap_points) == 3 and bar.gap_points[0] == first + 3  # 4 buttons, 3 gaps
    assert bar.gap_points[1] == first + 6 + widgets[1].winfo_reqwidth() + 3


def test_the_gap_after_new_folder(bar):
    dialogs, app = bar.dialogs, bar.app_
    dialogs.front = 77
    app.config.save_bar.gap = 60
    bar.update()
    assert dialogs.shown[bar.hwnd][0] == 330 + 60


def test_a_new_folder_button(bar):
    from switcher.savebar import new_folder_button

    dialogs, app = bar.dialogs, bar.app_
    assert bar.run(new_folder_button(), 77) == "Новая папка"
    assert dialogs.new_folders == [77] and dialogs.copied == [] and dialogs.carets == []  # its name is being typed
    dialogs.folder_made = False  # no rights there: the folder never shows up
    bar.run(new_folder_button(), 77)
    assert "новая папка не появилась" in app.notes[-1]


def test_errors_are_told_even_with_notifications_off(bar):
    from switcher.savebar import Button

    dialogs, app = bar.dialogs, bar.app_
    dialogs.fail = PermissionError(13, "Access denied")
    assert bar.run(Button("Копировать имя папки"), 77) == "Заказ   (id_1)"  # copied all the same
    assert dialogs.copied == ["Заказ   (id_1)"] and "Нет прав создать папку «PDF»" in app.notes[-1]
    dialogs.folders[77] = None  # «Этот компьютер»: no folder on a disk
    assert bar.run(Button("Копировать имя папки"), 77) is None
    assert "Не понял, какая папка" in app.notes[-1]


def test_the_save_bar_page(window):
    from switcher.savebar import Button

    window.show("savebar")
    ROOT.update()
    assert len(window.save_button_tree.get_children()) == 4
    window.var_bar_tips.set(True)
    window.bar_place.set(gui.SAVE_BAR_PLACES["above"])
    window.save()
    saved = window.app.saved[-1].save_bar
    assert saved.tooltips and saved.place == "above" and saved.enabled
    assert window.put_save_button(None, Button("Мой", suffix=" X", hotkey="<ctrl>+<shift>+2")) == (
        "Ctrl + Shift + 2 уже у кнопки «PANCH».")
    assert window.put_save_button(None, Button("Мой", suffix=" X", hotkey="<ctrl>+<shift>+9")) is None
    assert window.app.saved[-1].save_bar.buttons[-1]["label"] == "Мой"  # at once, without «Сохранить»
    window.save_button_tree.selection_set("4")
    window.move_save_button(-1)
    window.toggle_save_button()
    labels = [b["label"] for b in window.app.saved[-1].save_bar.buttons]
    assert labels == ["Копировать имя папки", "PANCH", "PANCH + ЗАГИБ", "Мой", "ОБЖИГ"]
    assert window.app.saved[-1].save_bar.buttons[3]["enabled"] is False
    window.remove_save_button()
    assert len(window.app.saved[-1].save_bar.buttons) == 4


def test_a_colour_is_chosen_for_a_button(window):
    from switcher.savebar import Button
    from switcher.savebar_dialogs import ButtonDialog

    window.show("savebar")
    taken = []
    dialog = ButtonDialog(window, lambda button: taken.append(button), Button("ОБЖИГ", suffix=" ОБЖИГ"))
    ROOT.update()
    dialog.pick("#30a46c")
    assert dialog.sample.cget("fg_color") == "#30A46C" and dialog.swatches["#30A46C"].cget("border_width") == 3
    dialog.done()
    assert taken == [Button("ОБЖИГ", suffix=" ОБЖИГ", color="#30A46C")]
    assert window.put_save_button(3, taken[0]) is None
    tree = window.save_button_tree
    assert tree.item("3")["values"][3] == "Зелёный" and tree.item("0")["values"][3] == "жёлтый"
    window.save_button_tree.selection_set("3")
    window.toggle_save_button()  # switching it off keeps the colour
    assert window.app.saved[-1].save_bar.buttons[3] == {**taken[0].to_dict(), "enabled": False}


def test_a_button_for_a_new_folder_is_set_up_in_its_dialog(window):
    from switcher.savebar import NEW_FOLDER
    from switcher.savebar_dialogs import ACTION_TITLES, ButtonDialog

    window.show("savebar")
    window.var_bar_gap.set(40)
    window.save()
    assert window.app.saved[-1].save_bar.gap == 40
    taken = []
    dialog = ButtonDialog(window, lambda button: taken.append(button))
    ROOT.update()
    dialog.actions.set(ACTION_TITLES[NEW_FOLDER])
    dialog._action_chosen(ACTION_TITLES[NEW_FOLDER])
    assert dialog.label.get() == "Новая папка" and dialog.prefix.cget("state") == "disabled"
    assert "Создаст новую папку" in dialog.preview.cget("text")
    dialog.pick("#3E63DD")
    dialog.done()
    assert taken[0].action == NEW_FOLDER and taken[0].label == "Новая папка" and taken[0].color == "#3E63DD"
    assert window.put_save_button(None, taken[0]) is None
    assert window.save_button_tree.item("4")["values"][2] == "создаёт новую папку"


def docker_rows(window, column: int = 1) -> list[str]:
    tree = window.docker_tree
    return [str(tree.item(item)["values"][column]) for item in tree.get_children()]


def test_the_docker_page(window, docker_folder, monkeypatch, tmp_path):
    from switcher import coreldocker
    from switcher.coreldocker import Preset, find_config

    monkeypatch.setenv("SWITCHER_HOME", str(tmp_path / "home"))  # the copies for «Отменить» go there
    window.config_copy.corel_docker.folder = str(docker_folder)
    window.show("docker")
    ROOT.update()

    def in_docker() -> list[str]:
        page = (docker_folder / "Politext.html").read_text(encoding="utf-8")
        return [p["title"] for p in find_config(page)[0]["presets"]]

    assert docker_rows(window) == ["450×370", "510×+32", "510×+36", "525×+94,5"]
    assert window.docker_title.cget("text") == "POLITEXT — кнопок: 4"
    assert window.docker_hint.cget("text") == window.DOCKER_READY
    assert window.docker_undo.cget("state") == "disabled" and window.docker_buttons[0].cget("state") == "normal"
    assert window.docker_allow.winfo_manager() == ""  # nothing to ask Windows for
    # a new button is written into the docker at once
    new = Preset("900×+70", "900", "→45mm", "#0EA5E9", 900, 70, guideline=45)
    assert window.put_docker_preset(None, new) is None
    assert in_docker() == ["450×370", "510×+32", "510×+36", "525×+94,5", "900×+70"] == docker_rows(window)
    assert docker_rows(window, 3)[-1] == "900 мм" and docker_rows(window, 4)[-1] == "+70 мм"
    assert window.docker_tree.selection() == (window.docker_presets[-1].id,)
    assert window.docker_undo.cget("state") == "normal"
    # the selected one moves inside its group, is copied, and the copy is removed
    window.docker_tree.selection_set("b2")
    window.move_docker_preset(1)
    assert in_docker()[1:3] == ["510×+36", "510×+32"] and window.docker_tree.selection() == ("b2",)
    window.move_docker_preset(1)  # the last of «510» stays there
    assert in_docker()[1:3] == ["510×+36", "510×+32"]
    window.copy_docker_preset()
    assert in_docker()[1:4] == ["510×+36", "510×+32", "510×+32"]
    window.remove_docker_preset()
    assert in_docker() == ["450×370", "510×+36", "510×+32", "525×+94,5", "900×+70"]
    window.docker_tree.selection_set(window.docker_presets[-1].id)
    window.move_docker_group(-1)
    assert in_docker() == ["450×370", "510×+36", "510×+32", "900×+70", "525×+94,5"]
    window.undo_docker()
    assert in_docker() == ["450×370", "510×+36", "510×+32", "525×+94,5", "900×+70"] == docker_rows(window)
    # an edited button: its dialog hands it back to the page
    assert window.put_docker_preset(0, dataclasses_replace(window.docker_presets[0], title="A2")) is None
    assert in_docker()[0] == "A2"
    # a folder closed by Windows: nothing can be changed until the user allows it
    monkeypatch.setattr(coreldocker.Docker, "writable", lambda self: False)
    window._show_docker(window.docker)
    ROOT.update()
    assert window.docker_allow.winfo_manager() == "pack" and window.docker_hint.cget("text") == window.DOCKER_CLOSED
    assert all(b.cget("state") == "disabled" for b in (*window.docker_buttons, window.docker_undo))
    window.add_docker_preset()
    window.edit_docker_preset()
    assert not [w for w in window.winfo_children() if type(w).__name__ == "PresetDialog"]
    # no docker on this computer
    window._dockers_found([])
    assert window.docker_title.cget("text") == "Докер не найден" and docker_rows(window) == []
    assert window.docker_hint.cget("text") == window.DOCKER_NOT_FOUND
    # one found in CorelDRAW's folders is remembered
    window.config_copy.corel_docker.folder = ""
    window._dockers_found([tmp_path / "nothing", docker_folder])
    assert window.app.saved[-1].corel_docker.folder == str(docker_folder) and len(docker_rows(window)) == 5


def dataclasses_replace(obj, **changes):
    import dataclasses

    return dataclasses.replace(obj, **changes)


def test_a_docker_button_is_set_up_in_its_dialog(window):
    from switcher.coreldocker import Preset
    from switcher.coreldocker_dialogs import EXACT_TITLE, WHAT_IT_DOES, PresetDialog

    def fill(entry, text: str) -> None:
        entry.delete(0, "end")
        entry.insert(0, text)
        dialog._numbers_changed()

    taken = []
    dialog = PresetDialog(window, lambda preset: taken.append(preset), groups=["510", "525"],
                          colors=["#3B82F6", "#10B981"], group="510", color="#3B82F6")
    ROOT.update()
    assert dialog.group.get() == "510" and dialog.status.cget("text") == WHAT_IT_DOES
    dialog.done()
    assert not taken and "надпись" in dialog.status.cget("text")
    fill(dialog.width, "510")
    fill(dialog.height, "47")
    fill(dialog.guideline, "30")
    assert (dialog.label.get(), dialog.subtitle.get()) == ("510×+47", "→30mm")  # the caption follows the numbers
    assert dialog.sample.cget("text") == "510×+47\n→30mm" and dialog.sample.cget("fg_color") == "#3B82F6"
    dialog.mode.set(EXACT_TITLE)
    dialog._numbers_changed()
    assert dialog.label.get() == "510×47"
    fill(dialog.width, "пятьсот")
    dialog.done()
    assert not taken and "Ширина: напишите число" in dialog.status.cget("text")
    fill(dialog.width, "510")
    dialog.label.delete(0, "end")
    dialog.label.insert(0, "Моя кнопка")  # written by hand: it stays whatever the numbers become
    fill(dialog.height, "37,5")
    assert dialog.label.get() == "Моя кнопка"
    dialog.pick("#10b981")
    assert dialog.swatches["#10B981"].cget("border_width") == 3 and dialog.swatches["#3B82F6"].cget("border_width") == 0
    dialog.done()
    assert taken == [Preset("Моя кнопка", "510", "→30mm", "#10B981", 510, 37.5, True, 30)]
    # an existing button with its own caption keeps it; one of another kind has only its look to edit
    own = Preset("745 × +90", "745", "→ 50 мм", "#8000FF", 745, 90, False, 50, "x")
    dialog = PresetDialog(window, lambda preset: taken.append(preset), own, groups=["745"], colors=["#3B82F6"])
    ROOT.update()
    assert "#8000FF" in dialog.swatches  # its own colour is always offered
    fill(dialog.height, "100")
    dialog.done()
    assert taken[-1] == Preset("745 × +90", "745", "→ 50 мм", "#8000FF", 745, 100, False, 50, "x")
    macro = Preset("Кресты", "Макросы", color="#111111", id="m", action="RunMacro")
    dialog = PresetDialog(window, lambda preset: taken.append(preset), macro)
    ROOT.update()
    assert dialog.width.cget("state") == "disabled"
    dialog.label.insert("end", " 5 мм")
    dialog.done()
    assert taken[-1] == Preset("Кресты 5 мм", "Макросы", color="#111111", id="m", action="RunMacro")


def run_handed_over(window, done, seconds: float = 5) -> None:
    """Work done in a thread reports through ui.call: run what it hands over until ``done()``."""
    deadline = time.time() + seconds
    while time.time() < deadline and not done():
        while not window.ui._calls.empty():
            window.ui._calls.get_nowait()()
        ROOT.update()
        time.sleep(0.02)


def test_the_docker_goes_to_another_computer(window, docker_folder, monkeypatch, tmp_path):
    from conftest import make_docker

    from switcher import coreldocker
    from switcher.coreldocker_dialogs import InstallDialog

    monkeypatch.setenv("SWITCHER_HOME", str(tmp_path / "home"))
    window.config_copy.corel_docker.folder = str(docker_folder)
    window.show("docker")
    ROOT.update()
    assert window.docker_export.cget("state") == "normal"
    # this computer: the docker is saved into a file
    package = tmp_path / "POLITEXT — докер.zip"
    monkeypatch.setattr(gui.filedialog, "asksaveasfilename", lambda **kw: str(package))
    window.export_docker_file()
    assert package.exists() and "Установить из файла" in window.docker_hint.cget("text")
    # the other computer: CorelDRAW 27 with a POLITEXT of its own
    other_pc = tmp_path / "other"
    make_docker(other_pc, version="27")
    monkeypatch.setattr(coreldocker, "corel_targets",
                        lambda roots=None, real=coreldocker.corel_targets: real([str(other_pc / "Corel")]))
    monkeypatch.setattr(gui.filedialog, "askopenfilename", lambda **kw: str(package))
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: window.errors.append(("info", *a)))
    window.install_docker_file()
    ROOT.update()
    dialog = [w for w in window.winfo_children() if isinstance(w, InstallDialog)][-1]
    assert dialog.caption.get() == "POLITEXT 2" and len(dialog.chosen) == 1
    dialog.caption.delete(0, "end")
    dialog.caption.insert(0, "POLITEXT")
    dialog.done()  # the name is taken there: told, and nothing installed yet
    run_handed_over(window, lambda: window.errors)
    assert "уже есть докер «POLITEXT»" in str(window.errors[-1])
    dialog = InstallDialog(window, lambda caption, chosen: window._install_docker(
        coreldocker.read_package(package), caption, chosen), name="POLITEXT", buttons=4,
        targets=coreldocker.corel_targets(), suggested="POLITEXT 2")
    ROOT.update()
    dialog.done()
    run_handed_over(window, lambda: window.errors[-1][0] == "info")
    assert "Докер «POLITEXT 2» установлен" in window.errors[-1][2]
    installed = other_pc / "Corel" / "CorelDRAW Graphics Suite" / "27" / "Programs64" / "Addons" / "POLITEXT_2_Switcher"
    assert window.app.saved[-1].corel_docker.folder == str(installed)
    assert window.docker_title.cget("text") == "POLITEXT 2 — кнопок: 4"
    # a file that is not a docker
    (tmp_path / "x.zip").write_text("nothing")
    monkeypatch.setattr(gui.filedialog, "askopenfilename", lambda **kw: str(tmp_path / "x.zip"))
    window.install_docker_file()
    assert "не файл докера" in str(window.errors[-1])


def test_the_size_of_the_docker_buttons_is_chosen(window, docker_folder, monkeypatch, tmp_path):
    from switcher.coreldocker import Docker, Look
    from switcher.coreldocker_dialogs import LookDialog

    monkeypatch.setenv("SWITCHER_HOME", str(tmp_path / "home"))
    window.config_copy.corel_docker.folder = str(docker_folder)
    window.show("docker")
    ROOT.update()
    window.docker_look()
    ROOT.update()
    dialog = [w for w in window.winfo_children() if isinstance(w, LookDialog)][-1]
    assert dialog.look() == Look(4, 42, 10) and dialog.values["height"].cget("text") == "42 точек"
    assert len(dialog.sample.find_all()) == 4 * 3  # four buttons: a box, a caption, the line under it
    dialog.vars["columns"].set(2)
    dialog.vars["font"].set(14)
    dialog._changed("columns", "")
    assert int(dialog.sample.cget("height")) == 2 * (42 + 4) + 4  # two rows of two
    dialog.done()
    assert Docker(docker_folder).look() == Look(2, 42, 14)
    assert "в ряду 2" in window.docker_hint.cget("text") and window.docker_undo.cget("state") == "normal"
    window.docker_look()
    ROOT.update()
    dialog = [w for w in window.winfo_children() if isinstance(w, LookDialog)][-1]
    dialog.usual()
    assert dialog.look() == Look(4, 42, 10)
    dialog.destroy()
