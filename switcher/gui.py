"""Settings window (CustomTkinter): everything a user needs without the command line.

Tk lives on the main thread.  Other threads (tray menu, AI calls) hand work
to it through :meth:`Ui.call`.
"""

from __future__ import annotations

import copy
import logging
import queue
import sys
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

import customtkinter as ctk

from . import __version__, autostart, updater
from .controller import parse_hotkey
from .engine import split_core
from .hotkeys import MODIFIER_KEYSYMS, build_spec as build_hotkey, format_hotkey, key_name as hotkey_key_name
from .hotkeys import problem as hotkey_problem
from .layouts import EN, RU, canonical_keys, text_lang
from .paths import data_dir
from .report import rule_rows, stats_text

log = logging.getLogger(__name__)

MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5"]
HOTKEY_ACTIONS = (
    ("convert_last", "Исправить / отменить последнее слово"),
    ("convert_selection", "Перевести выделенный текст"),
    ("ai_fix", "Исправить фразу с Claude"),
    ("toggle", "Пауза"),
)
HOTKEY_HINTS = {
    "convert_last": "Если Switcher ошибся или пропустил слово",
    "convert_selection": "Текст, набранный не в той раскладке",
    "ai_fix": "Claude перепишет фразу в нужной раскладке",
    "toggle": "Выключить и снова включить автоисправление",
}
HOTKEY_HELP = ("Подойдёт Ctrl или Alt с любой клавишей, Shift или Ctrl дважды, Pause, F1–F12. "
               "Esc — отменить, × — отключить сочетание.")
KEY_PLACEHOLDER = "••••••••••••••••"


class Ui:
    def __init__(self, app, root: tk.Tk | None = None):
        self.app = app
        apply_theme()
        self.root = root or make_root()
        self.root.withdraw()
        self.root.title("Switcher")
        _set_icon(self.root, default=True)
        install_clipboard_support(self.root)
        self._calls: queue.Queue = queue.Queue()
        self.window: SettingsWindow | None = None

    def call(self, fn) -> None:
        """Run ``fn`` on the Tk thread (safe from any thread)."""
        self._calls.put(fn)

    def _pump(self) -> None:
        while True:
            try:
                fn = self._calls.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception:
                log.exception("ui call failed")
        if self.app.stop_event.is_set():
            self.root.quit()
            return
        self.root.after(100, self._pump)

    def loop(self) -> None:
        self.root.after(100, self._pump)
        self.root.mainloop()

    def quit(self) -> None:
        self.root.quit()

    def open_settings(self, tab: str | None = None, welcome: bool = False) -> None:
        if self.window is not None and self.window.winfo_exists():
            self.window.show(tab)
            return
        self.window = SettingsWindow(self, welcome=welcome)
        self.window.show(tab or ("ai" if welcome else None))


# Physical keys of Ctrl+V/C/X/A.  Tk binds these shortcuts to the Latin letters,
# so they die while the Russian layout is active; match the key itself instead.
_SHORTCUT_KEYCODES = {
    "win32": {86: "<<Paste>>", 67: "<<Copy>>", 88: "<<Cut>>", 65: "select_all"},
    "linux": {55: "<<Paste>>", 54: "<<Copy>>", 53: "<<Cut>>", 38: "select_all"},
}.get(sys.platform, {})
_TEXT_CLASSES = ("Entry", "TEntry", "Text", "TCombobox", "TSpinbox", "Spinbox")


def select_all(widget) -> None:
    if isinstance(widget, tk.Text):
        widget.tag_add("sel", "1.0", "end-1c")
    else:
        widget.select_range(0, "end")
        widget.icursor("end")


def ctrl_shortcut(event):
    """Ctrl+V/C/X/A by physical key, whatever the keyboard layout."""
    if event.keysym.lower() in ("v", "c", "x", "a"):
        return None  # Latin layout: Tk's own bindings already handle it
    action = _SHORTCUT_KEYCODES.get(event.keycode)
    widget = event.widget
    if action is None or widget.winfo_class() not in _TEXT_CLASSES:
        return None
    if action == "select_all":
        select_all(widget)
    else:
        widget.event_generate(action)
    return "break"


def context_menu(event) -> None:
    """Right-click menu for text fields (Tk has none by default)."""
    widget = event.widget
    if not hasattr(widget, "winfo_class") or widget.winfo_class() not in _TEXT_CLASSES:
        return
    widget.focus_set()
    menu = tk.Menu(widget, tearoff=0)
    menu.add_command(label="Вырезать", command=lambda: widget.event_generate("<<Cut>>"))
    menu.add_command(label="Копировать", command=lambda: widget.event_generate("<<Copy>>"))
    menu.add_command(label="Вставить", command=lambda: widget.event_generate("<<Paste>>"))
    menu.add_separator()
    menu.add_command(label="Выделить всё", command=lambda: select_all(widget))
    try:
        menu.tk_popup(event.x_root, event.y_root)
    finally:
        menu.grab_release()


def install_clipboard_support(root) -> None:
    if getattr(root, "_switcher_clipboard", False):
        return
    root._switcher_clipboard = True
    root.bind_all("<Control-KeyPress>", ctrl_shortcut, add="+")
    root.bind_all("<Button-3>", context_menu, add="+")


def _set_icon(window, default: bool = False) -> None:
    from .tray import bundled_icon_path

    path = bundled_icon_path()
    if path is not None:
        try:
            # CustomTkinter puts its own icon on every window unless we set ours first
            window.iconbitmap(default=str(path)) if default else window.iconbitmap(str(path))
        except tk.TclError:
            pass


# Light / dark pairs, Windows 11 style; the accent is the tray icon's blue.
ACCENT = ("#2563EB", "#3B82F6")
ACCENT_HOVER = ("#1D4ED8", "#2563EB")
WINDOW_BG = ("#F3F3F3", "#202020")
CARD_BG = ("#FFFFFF", "#2B2B2B")
CARD_BORDER = ("#E5E5E5", "#3A3A3A")
TEXT = ("#1B1B1B", "#F2F2F2")
MUTED = ("#6B6B6B", "#A8A8A8")
NEUTRAL = ("#F7F7F7", "#363636")
NEUTRAL_HOVER = ("#EBEBEB", "#414141")
SELECTED = ("#E4E9F7", "#2F3645")
BANNER = ("#E8EFFD", "#1E2A44")
DANGER = ("#C42B1C", "#FF99A4")
_theme_ready = False


def apply_theme() -> None:
    """CustomTkinter's blue theme recoloured to match Windows 11, light or dark as the system is."""
    global _theme_ready
    if _theme_ready:
        return
    _theme_ready = True
    ctk.set_appearance_mode("system")
    ctk.set_default_color_theme("blue")
    theme = ctk.ThemeManager.theme
    if sys.platform == "win32":
        theme["CTkFont"]["family"] = "Segoe UI"
    theme["CTk"]["fg_color"] = theme["CTkToplevel"]["fg_color"] = list(WINDOW_BG)
    theme["CTkFrame"].update(fg_color=list(CARD_BG), top_fg_color=list(CARD_BG), border_color=list(CARD_BORDER),
                             corner_radius=10)
    theme["CTkButton"].update(fg_color=list(ACCENT), hover_color=list(ACCENT_HOVER), text_color=["#FFFFFF"] * 2)
    theme["CTkLabel"]["text_color"] = list(TEXT)
    theme["CTkSwitch"].update(progress_color=list(ACCENT), fg_color=["#9A9A9A", "#5C5C5C"],
                              button_color=["#FFFFFF", "#FFFFFF"], button_hover_color=["#F0F0F0", "#E0E0E0"],
                              border_width=0)  # with a border the white knob melts into a white card
    theme["CTkSlider"].update(progress_color=list(ACCENT), fg_color=["#D6D6D6", "#4A4A4A"],
                              button_color=list(ACCENT), button_hover_color=list(ACCENT_HOVER))
    for name in ("CTkEntry", "CTkComboBox"):
        theme[name].update(fg_color=["#FFFFFF", "#1F1F1F"], border_color=["#D0D0D0", "#4A4A4A"], border_width=1,
                           text_color=list(TEXT))
    theme["CTkComboBox"].update(button_color=["#D0D0D0", "#4A4A4A"], button_hover_color=["#B8B8B8", "#5C5C5C"])
    theme["CTkTextbox"].update(fg_color=list(CARD_BG), border_color=list(CARD_BORDER), text_color=list(TEXT))
    theme["DropdownMenu"].update(fg_color=list(CARD_BG), hover_color=list(SELECTED), text_color=list(TEXT))
    theme["CTkScrollbar"].update(button_color=["#C4C4C4", "#4D4D4D"], button_hover_color=["#A8A8A8", "#636363"])


def make_root() -> tk.Tk:
    apply_theme()
    root = ctk.CTk()
    root.withdraw()
    return root


def _autohide_scrollbar(page: ctk.CTkScrollableFrame) -> None:
    """Show the page's scrollbar only when the page does not fit.

    The bar stays in the layout and is just painted in the background colour:
    CustomTkinter re-grids a removed bar whenever it rescales the window.
    """
    try:
        canvas, bar = page._parent_canvas, page._scrollbar
    except AttributeError:  # another CustomTkinter version: leave the scrollbar as it is
        return
    theme = ctk.ThemeManager.theme["CTkScrollbar"]
    shown = {"button_color": theme["button_color"], "button_hover_color": theme["button_hover_color"]}
    hidden = {"button_color": WINDOW_BG, "button_hover_color": WINDOW_BG}
    state = {"need": True}

    def check(_event=None):
        need = page.winfo_reqheight() > canvas.winfo_height()
        if need != state["need"]:
            state["need"] = need
            bar.configure(**(shown if need else hidden))

    page.bind("<Configure>", check, add="+")
    canvas.bind("<Configure>", check, add="+")


def _logo_image():
    try:
        from .tray import draw_icon

        image = draw_icon(96)
        return ctk.CTkImage(light_image=image, dark_image=image, size=(36, 36))
    except Exception:  # Pillow missing: no logo, nothing else changes
        return None


class SettingsWindow(ctk.CTkToplevel):
    PAGES = (("main", "Основное"), ("keys", "Горячие клавиши"), ("ai", "Claude (ИИ)"), ("rules", "Правила"),
             ("stats", "Что я о вас знаю"), ("updates", "Обновления"))

    def __init__(self, ui: Ui, welcome: bool = False):
        apply_theme()
        super().__init__(ui.root)
        _set_icon(self)
        self.ui = ui
        self.app = ui.app
        self.config_copy = copy.deepcopy(self.app.config)
        self.title("Switcher — настройки")
        self.geometry("900x660")
        self.minsize(780, 520)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.fonts = {
            "brand": ctk.CTkFont(size=18, weight="bold"),
            "title": ctk.CTkFont(size=24, weight="bold"),
            "section": ctk.CTkFont(size=14, weight="bold"),
            "body": ctk.CTkFont(size=13),
            "small": ctk.CTkFont(size=12),
            "key": ctk.CTkFont(size=13, weight="bold"),
            "nav": ctk.CTkFont(size=14),
        }
        # hotkey recording: while it runs, the window itself holds the keyboard focus
        self._recording: str | None = None
        self._separators: list[tk.Frame] = []
        self.bind("<KeyPress>", self._record_press, add="+")
        self.bind("<KeyRelease>", self._record_release, add="+")
        self.bind("<FocusOut>", self._focus_out, add="+")
        self._build(welcome)
        self._restyle()
        ctk.AppearanceModeTracker.add(self._restyle, self)
        listeners = getattr(self.app, "release_listeners", None)
        if listeners is not None:
            listeners.append(self._releases_arrived)

    def destroy(self) -> None:
        ctk.AppearanceModeTracker.remove(self._restyle)
        listeners = getattr(self.app, "release_listeners", None)
        if listeners is not None and self._releases_arrived in listeners:
            listeners.remove(self._releases_arrived)
        super().destroy()

    # -- layout --------------------------------------------------------------

    def _build(self, welcome: bool) -> None:
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_sidebar()

        content = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        content.grid(row=0, column=1, sticky="nsew", padx=(8, 24), pady=(20, 0))
        if welcome:
            self._banner(content)
        self.page_host = ctk.CTkFrame(content, fg_color="transparent", corner_radius=0)
        self.page_host.pack(fill="both", expand=True)
        self.tabs = {
            "main": self._main_tab(),
            "keys": self._keys_tab(),
            "ai": self._ai_tab(),
            "rules": self._rules_tab(),
            "stats": self._stats_tab(),
            "updates": self._updates_tab(),
        }
        self.current: str | None = None
        self._select("main")

        bottom = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        bottom.grid(row=1, column=1, sticky="ew", padx=(8, 24), pady=16)
        self.status = ctk.CTkLabel(bottom, text="", text_color=MUTED, font=self.fonts["body"], anchor="w")
        self.status.pack(side="left", fill="x", expand=True)
        self._button(bottom, "Закрыть", self.destroy, width=110).pack(side="right")
        self._button(bottom, "Сохранить", self.save, primary=True, width=130).pack(side="right", padx=(0, 8))

    def _build_sidebar(self) -> None:
        side = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0, width=230)
        side.grid(row=0, column=0, rowspan=2, sticky="ns", padx=(12, 0), pady=20)
        side.pack_propagate(False)
        brand = ctk.CTkFrame(side, fg_color="transparent", corner_radius=0)
        brand.pack(fill="x", padx=8, pady=(0, 18))
        logo = _logo_image()
        if logo is not None:
            ctk.CTkLabel(brand, text="", image=logo).pack(side="left", padx=(0, 10))
        names = ctk.CTkFrame(brand, fg_color="transparent", corner_radius=0)
        names.pack(side="left", fill="x")
        ctk.CTkLabel(names, text="Switcher", font=self.fonts["brand"], anchor="w").pack(fill="x")
        ctk.CTkLabel(names, text="раскладка RU / EN", font=self.fonts["small"], text_color=MUTED,
                     anchor="w").pack(fill="x")
        self.nav: dict[str, tuple[ctk.CTkFrame, ctk.CTkButton]] = {}
        for key, title in self.PAGES:
            row = ctk.CTkFrame(side, fg_color="transparent", corner_radius=0)
            row.pack(fill="x", pady=1)
            marker = ctk.CTkFrame(row, width=3, height=18, corner_radius=2, fg_color="transparent")
            marker.pack(side="left", padx=(0, 4))
            button = ctk.CTkButton(row, text=title, anchor="w", height=38, corner_radius=6, font=self.fonts["nav"],
                                   fg_color="transparent", hover_color=NEUTRAL_HOVER, text_color=TEXT,
                                   command=lambda k=key: self._select(k))
            button.pack(side="left", fill="x", expand=True)
            self.nav[key] = (marker, button)
        ctk.CTkLabel(side, text=f"Версия {__version__}", font=self.fonts["small"], text_color=MUTED,
                     anchor="w").pack(side="bottom", fill="x", padx=12)

    def _banner(self, parent) -> None:
        banner = ctk.CTkFrame(parent, fg_color=BANNER, corner_radius=10)
        banner.pack(fill="x", pady=(0, 14))
        ctk.CTkButton(banner, text="×", width=28, height=28, fg_color="transparent", hover_color=SELECTED,
                      text_color=MUTED, font=ctk.CTkFont(size=18), command=banner.destroy).pack(
            side="right", anchor="n", padx=6, pady=6)
        ctk.CTkLabel(banner, font=self.fonts["body"], justify="left", anchor="w", wraplength=560, text=(
            "Switcher уже работает — его значок в трее у часов. Просто печатайте: слова, набранные не в той "
            "раскладке, исправятся на пробеле. Двойной Shift исправляет или отменяет последнее слово, и каждое "
            "такое исправление Switcher запоминает.\n\nЧтобы подключить Claude (разбор ваших ошибок и исправление "
            "целых фраз), вставьте ключ API ниже и нажмите «Сохранить».")).pack(
            side="left", fill="x", expand=True, padx=(16, 0), pady=14)

    def _select(self, tab: str) -> None:
        if tab == self.current:
            return
        if self.current is not None:
            self.tabs[self.current].pack_forget()
        self.current = tab
        self.tabs[tab].pack(fill="both", expand=True)
        for key, (marker, button) in self.nav.items():
            active = key == tab
            marker.configure(fg_color=ACCENT if active else "transparent")
            button.configure(fg_color=SELECTED if active else "transparent")
        if tab == "stats":
            self.refresh_stats()
        if tab == "updates" and not self._releases_shown:
            if getattr(self.app, "releases", None) is not None:
                self.show_releases(self.app.releases)
            else:
                self.check_updates()

    def show(self, tab: str | None = None) -> None:
        if tab in self.tabs:
            self._select(tab)
        self.deiconify()
        self.lift()
        self.focus_force()

    # -- building blocks -----------------------------------------------------

    def _page(self, title: str, subtitle: str = "", scroll: bool = True):
        if scroll:
            page = ctk.CTkScrollableFrame(self.page_host, fg_color="transparent", corner_radius=0)
            _autohide_scrollbar(page)
        else:
            page = ctk.CTkFrame(self.page_host, fg_color="transparent", corner_radius=0)
        ctk.CTkLabel(page, text=title, font=self.fonts["title"], anchor="w").pack(fill="x", pady=(0, 2))
        if subtitle:
            ctk.CTkLabel(page, text=subtitle, font=self.fonts["body"], text_color=MUTED, anchor="w", justify="left",
                         wraplength=600).pack(fill="x")
        return page

    def _card(self, parent, title: str = "", expand: bool = False) -> ctk.CTkFrame:
        if title:
            ctk.CTkLabel(parent, text=title, font=self.fonts["section"], anchor="w").pack(fill="x", pady=(18, 6))
        card = ctk.CTkFrame(parent, border_width=1)
        card.pack(fill="both" if expand else "x", expand=expand, pady=(0 if title else 14, 0), padx=(0, 4))
        card._rows = 0
        return card

    def _row(self, card, title: str, subtitle: str = "") -> ctk.CTkFrame:
        """A card row: text on the left; the caller packs its control(s) on the right of the returned frame."""
        if card._rows:
            line = tk.Frame(card, height=1, borderwidth=0, highlightthickness=0)  # CTk frames can't be 1px thin
            line.pack(fill="x", padx=16)
            self._separators.append(line)
        card._rows += 1
        row = ctk.CTkFrame(card, fg_color="transparent", corner_radius=0)
        row.pack(fill="x", padx=16, pady=10)
        text = ctk.CTkFrame(row, fg_color="transparent", corner_radius=0)
        text.pack(side="left", fill="x", expand=True, padx=(0, 12))
        ctk.CTkLabel(text, text=title, font=self.fonts["body"], anchor="w", justify="left", height=20).pack(fill="x")
        if subtitle:
            ctk.CTkLabel(text, text=subtitle, font=self.fonts["small"], text_color=MUTED, anchor="w", justify="left",
                         wraplength=440, height=16).pack(fill="x", pady=(2, 0))
        row.text = text
        return row

    def _switch_row(self, card, var: tk.BooleanVar, title: str, subtitle: str = "") -> None:
        row = self._row(card, title, subtitle)
        ctk.CTkSwitch(row, text="", variable=var, onvalue=True, offvalue=False, width=46, switch_width=40,
                      switch_height=20).pack(side="right")

    def _button(self, parent, text: str, command, primary: bool = False, danger: bool = False, **kw):
        kw.setdefault("height", 34)
        if primary:
            return ctk.CTkButton(parent, text=text, command=command, font=self.fonts["body"], **kw)
        return ctk.CTkButton(parent, text=text, command=command, font=self.fonts["body"], fg_color=NEUTRAL,
                             hover_color=NEUTRAL_HOVER, text_color=DANGER if danger else TEXT, border_width=1,
                             border_color=CARD_BORDER, **kw)

    def _textbox(self, parent, **kw) -> ctk.CTkTextbox:
        kw.setdefault("border_width", 1)
        return ctk.CTkTextbox(parent, wrap="word", font=self.fonts["body"], corner_radius=10, **kw)

    # -- pages ---------------------------------------------------------------

    def _main_tab(self):
        c = self.config_copy
        page = self._page("Основное", "Как и когда Switcher исправляет раскладку.")
        self.var_enabled = tk.BooleanVar(value=c.enabled)
        self.var_autostart = tk.BooleanVar(value=autostart.is_enabled())
        self.var_look_back = tk.BooleanVar(value=c.look_back)
        self.var_enter = tk.BooleanVar(value=c.convert_on_enter)
        self.var_caps = tk.BooleanVar(value=c.fix_caps_lock)

        card = self._card(page, "Автоисправление")
        self._switch_row(card, self.var_enabled, "Исправлять раскладку автоматически",
                         "Слово, набранное не в той раскладке, исправится на пробеле: «ghbdtn» → «привет»")
        self._switch_row(card, self.var_look_back, "Исправлять и короткое слово перед ним",
                         "«e vtyz» → «у меня»: короткие слова понятны только вместе со следующим")
        self._switch_row(card, self.var_caps, "Исправлять случайный Caps Lock",
                         "«пРИВЕТ» → «Привет», и Caps Lock выключится")
        self._switch_row(card, self.var_enter, "Исправлять слово перед Enter",
                         "В чатах сообщение может уйти раньше, чем слово исправится")

        card = self._card(page, "Осторожность")
        self._row(card, "Когда переключать", "Правее — Switcher исправляет только очевидное, левее — смелее "
                                             "исправляет короткие и редкие слова")
        line = ctk.CTkFrame(card, fg_color="transparent", corner_radius=0)
        line.pack(fill="x", padx=16, pady=(0, 14))
        self.var_threshold = tk.DoubleVar(value=c.threshold)
        ctk.CTkLabel(line, text="смелее", font=self.fonts["small"], text_color=MUTED).pack(side="left", padx=(0, 10))
        self.threshold_label = ctk.CTkLabel(line, text=f"{c.threshold:.1f}", width=34, font=self.fonts["key"])
        self.threshold_label.pack(side="right")
        ctk.CTkLabel(line, text="осторожнее", font=self.fonts["small"], text_color=MUTED).pack(side="right",
                                                                                            padx=(10, 6))
        ctk.CTkSlider(line, from_=1.0, to=4.0, number_of_steps=30, variable=self.var_threshold,
                      command=lambda value: self.threshold_label.configure(text=f"{value:.1f}")).pack(
            side="left", fill="x", expand=True)

        card = self._card(page, "Система")
        self._switch_row(card, self.var_autostart,
                         "Запускать вместе с Windows" if _is_windows() else "Запускать при входе в систему",
                         "Switcher будет ждать в трее с самого начала")
        self._row(card, "Не работать в программах",
                  "Через запятую, достаточно части имени программы. Здесь Switcher ничего не исправляет "
                  "и не запоминает")
        self.var_excluded = tk.StringVar(value=", ".join(c.excluded_apps))
        ctk.CTkEntry(card, textvariable=self.var_excluded, height=34, font=self.fonts["body"]).pack(
            fill="x", padx=16, pady=(0, 14))
        return page

    def _keys_tab(self):
        c = self.config_copy
        page = self._page("Горячие клавиши", "Нажмите на сочетание и затем нужные клавиши — так же, как будете "
                                             "нажимать их потом.")
        card = self._card(page)
        self.hotkey_specs: dict[str, str] = {}
        self.hotkey_labels: dict[str, ctk.CTkButton] = {}
        for name, title in HOTKEY_ACTIONS:
            self.hotkey_specs[name] = getattr(c.hotkeys, name)
            row = self._row(card, title, HOTKEY_HINTS[name])
            ctk.CTkButton(row, text="×", width=34, height=34, fg_color="transparent", hover_color=NEUTRAL_HOVER,
                          text_color=MUTED, font=ctk.CTkFont(size=18),
                          command=lambda n=name: self.set_hotkey(n, "")).pack(side="right", padx=(6, 0))
            chip = ctk.CTkButton(row, text=format_hotkey(self.hotkey_specs[name]), width=200, height=34,
                                 font=self.fonts["key"], fg_color=NEUTRAL, hover_color=NEUTRAL_HOVER, text_color=TEXT,
                                 border_width=1, border_color=CARD_BORDER,
                                 command=lambda n=name: self.start_recording(n))
            chip.pack(side="right")
            self.hotkey_labels[name] = chip
        self.hotkey_hint = ctk.CTkLabel(page, font=self.fonts["body"], text_color=MUTED, anchor="w", justify="left",
                                        wraplength=600, text=HOTKEY_HELP)
        self.hotkey_hint.pack(fill="x", pady=(12, 0))
        return page

    def _ai_tab(self):
        c = self.config_copy.ai
        page = self._page("Claude (ИИ)", "Claude разбирает ваши исправления, придумывает правила и исправляет "
                                         "целые фразы.")
        self.var_ai = tk.BooleanVar(value=c.enabled)
        card = self._card(page, "Подключение")
        self._switch_row(card, self.var_ai, "Использовать Claude",
                         "Без ключа Switcher тоже работает и учится, но без помощи ИИ")
        self._row(card, "Ключ API", "Хранится зашифрованным (Windows DPAPI)" if _is_windows() else "")
        key_row = ctk.CTkFrame(card, fg_color="transparent", corner_radius=0)
        key_row.pack(fill="x", padx=16, pady=(0, 6))
        self.var_key = tk.StringVar(value=KEY_PLACEHOLDER if c.api_key else "")
        self.key_entry = ctk.CTkEntry(key_row, textvariable=self.var_key, show="•", height=34,
                                      font=self.fonts["body"])
        self.key_entry.pack(side="left", fill="x", expand=True)
        self.key_entry.bind("<FocusIn>", self._clear_placeholder)
        self.key_entry.bind("<FocusOut>", self._restore_placeholder)
        self._button(key_row, "Проверить", self.check_key, primary=True, width=110).pack(side="right", padx=(8, 0))
        self._button(key_row, "Вставить", self.paste_key, width=100).pack(side="right", padx=(8, 0))
        status_row = ctk.CTkFrame(card, fg_color="transparent", corner_radius=0)
        status_row.pack(fill="x", padx=16, pady=(0, 12))
        self.key_status = ctk.CTkLabel(status_row, text="", font=self.fonts["small"], text_color=MUTED, anchor="w",
                                       justify="left", wraplength=360)
        self.key_status.pack(side="left", fill="x", expand=True)
        link = ctk.CTkLabel(status_row, text="Где взять ключ →", font=self.fonts["small"], text_color=ACCENT,
                            cursor="hand2")
        link.pack(side="right")
        link.bind("<Button-1>", lambda _: webbrowser.open("https://console.anthropic.com/settings/keys"))

        card = self._card(page, "Как Claude помогает")
        row = self._row(card, "Модель", "Opus — умнее, Haiku — быстрее и дешевле")
        self.var_model = tk.StringVar(value=c.model)
        ctk.CTkComboBox(row, variable=self.var_model, values=MODELS, width=220, height=34,
                        font=self.fonts["body"], dropdown_font=self.fonts["body"]).pack(side="right")
        row = self._row(card, "Разбирать мои исправления", "Через сколько исправлений Claude смотрит, чему научиться "
                                                         "(0 — только по кнопке)")
        self.var_every = tk.StringVar(value=str(c.review_every))
        ctk.CTkEntry(row, textvariable=self.var_every, width=70, height=34, justify="center",
                     font=self.fonts["body"]).pack(side="right")
        self.var_typos = tk.BooleanVar(value=c.fix_typos)
        self._switch_row(card, self.var_typos, "Исправлять опечатки во фразе",
                         "Когда Claude исправляет фразу, он поправит и опечатки, а не только раскладку")
        row = self._row(card, "Разобрать сейчас", "Не дожидаясь, пока наберётся нужное число исправлений")
        self._button(row, "Разобрать", self.review_now, width=110).pack(side="right")

        card = self._card(page, "Ваш стиль — по мнению Claude")
        self.style_text = self._textbox(card, height=130, fg_color="transparent", border_width=0)
        self.style_text.pack(fill="x", padx=8, pady=8)
        self._set_text(self.style_text, self.app.profile.get_meta("style_summary")
                       or "Пока пусто: Claude составит описание после первого разбора ваших исправлений.")
        return page

    def _rules_tab(self):
        page = self._page("Правила", "Правила появляются сами, когда вы исправляете Switcher, а ещё их предлагает "
                                     "Claude. Здесь их можно посмотреть, добавить или удалить.", scroll=False)
        buttons = ctk.CTkFrame(page, fg_color="transparent", corner_radius=0)
        buttons.pack(side="bottom", fill="x", pady=(12, 0))
        self._button(buttons, "Добавить слово…", self.add_word).pack(side="left")
        self._button(buttons, "Добавить автозамену…", self.add_typo).pack(side="left", padx=8)
        self._button(buttons, "Удалить выбранные", self.remove_rules, danger=True).pack(side="right")
        card = self._card(page, expand=True)
        columns = ("word", "result", "app", "source", "hits")
        self.tree = ttk.Treeview(card, columns=columns, show="headings", style="Switcher.Treeview")
        for col, title, width in (("word", "Слово", 130), ("result", "Что делать", 170), ("app", "Где", 80),
                                  ("source", "Кто добавил", 160), ("hits", "Раз", 44)):
            self.tree.heading(col, text=title, anchor="w")
            self.tree.column(col, width=width, minwidth=40, anchor="w", stretch=col != "hits")
        scroll = ctk.CTkScrollbar(card, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y", padx=(0, 4), pady=8)
        self.tree.pack(side="left", fill="both", expand=True, padx=(12, 0), pady=10)
        self._rules: dict[str, object] = {}
        self.refresh_rules()
        return page

    def _stats_tab(self):
        page = self._page("Что я о вас знаю", "Всё, что Switcher выучил о вашей печати. Данные хранятся только "
                                              "на этом компьютере.", scroll=False)
        buttons = ctk.CTkFrame(page, fg_color="transparent", corner_radius=0)
        buttons.pack(side="bottom", fill="x", pady=(12, 0))
        self._button(buttons, "Обновить", self.refresh_stats).pack(side="left")
        self._button(buttons, "Папка с данными", self.open_data).pack(side="left", padx=8)
        self._button(buttons, "Стереть всё выученное…", self.forget, danger=True).pack(side="right")
        self.stats = self._textbox(page)
        self.stats.pack(fill="both", expand=True, pady=(14, 0), padx=(0, 4))
        self.refresh_stats()
        return page

    def _updates_tab(self):
        page = self._page("Обновления", f"Сейчас установлена версия {__version__}. Выберите новую версию, чтобы "
                                        "обновиться, или прежнюю, чтобы откатиться. Настройки и всё выученное "
                                        "сохранятся.")
        self.var_auto_update = tk.BooleanVar(value=self.config_copy.updates.check_automatically)
        card = self._card(page, "Проверка")
        self._switch_row(card, self.var_auto_update, "Проверять обновления автоматически",
                         "Дважды в день. О новой версии Switcher скажет уведомлением у часов")
        row = self._row(card, "Проверить сейчас")
        self.check_button = self._button(row, "Проверить", lambda: self.check_updates(force=True), width=110)
        self.check_button.pack(side="right")
        self.update_status = ctk.CTkLabel(row.text, text=self._last_check_text(),
                                          font=self.fonts["small"], text_color=MUTED, anchor="w", justify="left",
                                          wraplength=440, height=16)
        self.update_status.pack(fill="x", pady=(2, 0))

        self.release_box = self._card(page, "Версии")
        self.release_buttons: list[ctk.CTkButton] = []
        self._releases_shown = False
        ctk.CTkLabel(self.release_box, text="Загружаю список версий…", font=self.fonts["body"], text_color=MUTED,
                     anchor="w").pack(fill="x", padx=16, pady=14)
        self.update_progress = ctk.CTkProgressBar(page, height=8, progress_color=ACCENT)
        self.update_progress.set(0)
        return page

    def _last_check_text(self) -> str:
        stamp = float(self.app.profile.get_meta("update_checked_at", "0") or 0)
        if not stamp:
            return "Ещё не проверялось"
        return "Проверено " + time.strftime("%d.%m в %H:%M", time.localtime(stamp))

    def _releases_arrived(self, releases) -> None:
        """From the background check (any thread)."""
        self.ui.call(lambda: self.winfo_exists() and self.show_releases(releases))

    def check_updates(self, force: bool = False) -> None:
        self.update_status.configure(text="Проверяю…")
        self.check_button.configure(state="disabled")

        def work():
            try:
                releases = self.app.check_updates(force=force)
            except updater.UpdateError as exc:
                message = f"Не получилось: {exc}."
                self.ui.call(lambda: self.winfo_exists() and self._check_failed(message))
                return
            if not force:  # a fresh list reaches show_releases through the listener
                self.ui.call(lambda: self.winfo_exists() and self.show_releases(releases))

        threading.Thread(target=work, daemon=True).start()

    def _check_failed(self, message: str) -> None:
        self.check_button.configure(state="normal")
        self.update_status.configure(text=message)
        if not self._releases_shown:
            self._clear_releases()
            ctk.CTkLabel(self.release_box, text="Список версий пока недоступен.", font=self.fonts["body"],
                         text_color=MUTED, anchor="w").pack(fill="x", padx=16, pady=14)

    def _clear_releases(self) -> None:
        for child in self.release_box.winfo_children():
            child.destroy()
        self._separators = [line for line in self._separators if line.winfo_exists()]
        self.release_box._rows = 0
        self.release_buttons = []

    def show_releases(self, releases) -> None:
        self._releases_shown = True
        self.check_button.configure(state="normal")
        newer = any(r.relation == "newer" and not r.prerelease for r in releases)
        checked = self._last_check_text()
        if releases and not newer:
            checked = f"У вас последняя версия ✓ · {checked[0].lower()}{checked[1:]}"
        self.update_status.configure(text=checked)
        self._clear_releases()
        if not releases:
            ctk.CTkLabel(self.release_box, text="На GitHub пока нет ни одной версии.", font=self.fonts["body"],
                         text_color=MUTED, anchor="w").pack(fill="x", padx=16, pady=14)
        for release in releases:
            self._release_row(release)
        self.nav["updates"][1].configure(text="Обновления  ●" if newer else "Обновления")

    def _release_row(self, release) -> None:
        box = self.release_box
        if box._rows:
            line = tk.Frame(box, height=1, borderwidth=0, highlightthickness=0)
            line.pack(fill="x", padx=16)
            self._separators.append(line)
            self._restyle()
        box._rows += 1
        row = ctk.CTkFrame(box, fg_color="transparent", corner_radius=0)
        row.pack(fill="x", padx=16, pady=12)
        relation = release.relation
        if relation == "current":
            ctk.CTkLabel(row, text="Установлена", font=self.fonts["small"], text_color=MUTED, width=110).pack(
                side="right", anchor="n")
        else:
            newer = relation == "newer"
            button = self._button(row, "Обновить" if newer else "Откатить", lambda r=release: self.choose_release(r),
                                  primary=newer, width=110)
            button.pack(side="right", anchor="n")
            self.release_buttons.append(button)
        text = ctk.CTkFrame(row, fg_color="transparent", corner_radius=0)
        text.pack(side="left", fill="x", expand=True, padx=(0, 12))
        head = ctk.CTkFrame(text, fg_color="transparent", corner_radius=0)
        head.pack(fill="x")
        ctk.CTkLabel(head, text=f"Версия {release.version}", font=self.fonts["section"], height=22).pack(side="left")
        badge = {"newer": ("новая", BANNER, ACCENT), "current": ("у вас сейчас", SELECTED, TEXT),
                 "older": ("прежняя", NEUTRAL, MUTED)}[relation]
        if release.prerelease:
            badge = ("тестовая", NEUTRAL, DANGER)
        ctk.CTkLabel(head, text=badge[0], fg_color=badge[1], text_color=badge[2], corner_radius=6, height=20,
                     font=self.fonts["small"]).pack(side="left", padx=8)
        if release.date:
            day = ".".join(reversed(release.date.split("-")))
            ctk.CTkLabel(head, text=day, font=self.fonts["small"], text_color=MUTED, height=20).pack(side="left")
        notes = release.notes or ["Без описания"]
        ctk.CTkLabel(text, text="\n".join(f"•  {note}" for note in notes), font=self.fonts["small"],
                     text_color=MUTED, anchor="w", justify="left", wraplength=440).pack(fill="x", pady=(4, 0))
        if release.url:
            link = ctk.CTkLabel(text, text="Подробнее на GitHub →", font=self.fonts["small"], text_color=ACCENT,
                                cursor="hand2", anchor="w", height=18)
            link.pack(anchor="w", pady=(4, 0))
            link.bind("<Button-1>", lambda _e, url=release.url: webbrowser.open(url))

    def choose_release(self, release) -> None:
        if not updater.can_install():
            if messagebox.askyesno("Switcher", "Обновление из программы работает в установленной версии для "
                                   "Windows. Открыть страницу этой версии на GitHub?", parent=self):
                webbrowser.open(release.url)
            return
        size = f" ({release.size / 1_048_576:.0f} МБ)" if release.size else ""
        if release.relation == "newer":
            question = f"Обновиться до версии {release.version}?"
        else:
            question = f"Откатиться на версию {release.version}?"
        details = (f"Switcher скачает установщик{size}, закроется на несколько секунд и запустится снова. "
                   "Настройки и всё выученное сохранятся.")
        if release.relation == "older":
            details += "\n\nВернуться на новую версию можно здесь же."
        if not messagebox.askyesno("Switcher", f"{question}\n\n{details}", parent=self):
            return
        for button in self.release_buttons + [self.check_button]:
            button.configure(state="disabled")
        self.update_progress.set(0)
        self.update_progress.pack(fill="x", pady=(14, 0), padx=(0, 4))
        self.update_status.configure(text=f"Скачиваю версию {release.version}…")

        def progress(done: int, total: int) -> None:
            self.ui.call(lambda: self.winfo_exists() and self._show_progress(release, done, total))

        def work():
            try:
                setup = updater.download(release, progress=progress)
            except updater.UpdateError as exc:
                message = f"Не получилось: {exc}."
                self.ui.call(lambda: self.winfo_exists() and self._download_failed(message))
                return
            self.ui.call(lambda: self.winfo_exists() and self._install(release, setup))

        threading.Thread(target=work, daemon=True).start()

    def _show_progress(self, release, done: int, total: int) -> None:
        if total:
            self.update_progress.set(done / total)
            self.update_status.configure(text=f"Скачиваю версию {release.version}: {done / 1_048_576:.0f} из "
                                              f"{total / 1_048_576:.0f} МБ")

    def _download_failed(self, message: str) -> None:
        self.update_progress.pack_forget()
        self.update_status.configure(text=message)
        for button in self.release_buttons + [self.check_button]:
            button.configure(state="normal")

    def _install(self, release, setup) -> None:
        self.update_progress.set(1)
        self.update_status.configure(text=f"Устанавливаю версию {release.version}. Switcher перезапустится сам.")
        self.update_idletasks()
        self.app.install_update(release, setup)

    def _restyle(self, _mode: str | None = None) -> None:
        """Plain Tk and ttk widgets do not follow CustomTkinter's light/dark colours by themselves."""
        dark = ctk.get_appearance_mode() == "Dark"
        pick = (lambda pair: pair[1]) if dark else (lambda pair: pair[0])
        for line in self._separators:
            line.configure(background=pick(CARD_BORDER))
        family = ctk.ThemeManager.theme["CTkFont"]["family"]
        scale = ctk.ScalingTracker.get_widget_scaling(self)
        style = ttk.Style(self)
        if style.theme_use() != "clam":
            style.theme_use("clam")  # the built-in theme whose headings can be recoloured
        style.layout("Switcher.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        style.configure("Switcher.Treeview", background=pick(CARD_BG), fieldbackground=pick(CARD_BG),
                        foreground=pick(TEXT), rowheight=int(30 * scale), borderwidth=0, font=(family, 10))
        style.map("Switcher.Treeview", background=[("selected", pick(SELECTED))],
                  foreground=[("selected", pick(TEXT))])
        style.configure("Switcher.Treeview.Heading", background=pick(CARD_BG), foreground=pick(MUTED), relief="flat",
                        borderwidth=0, font=(family, 10, "bold"), padding=(4, 6))
        style.map("Switcher.Treeview.Heading", background=[("active", pick(NEUTRAL_HOVER))])

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _set_text(widget: ctk.CTkTextbox, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def _ask(self, title: str, text: str) -> str | None:
        dialog = ctk.CTkInputDialog(title=title, text=text, font=self.fonts["body"])
        _set_icon(dialog)
        return dialog.get_input()

    def _clear_placeholder(self, _event=None) -> None:
        if self.var_key.get() == KEY_PLACEHOLDER:
            self.var_key.set("")

    # -- hotkey recording ------------------------------------------------------

    def start_recording(self, name: str) -> None:
        if self._recording and self._recording != name:
            self._stop_recording(self._recording)
        self._recording = name
        self._rec_mods: set[str] = set()
        self._rec_tap: tuple[str | None, float] = (None, 0.0)
        self._rec_last_tap: tuple[str | None, float] = (None, 0.0)
        self.hotkey_labels[name].configure(text="Нажмите клавиши…", fg_color=SELECTED, border_color=ACCENT)
        self.hotkey_hint.configure(text=HOTKEY_HELP)
        self.focus_set()  # keys now reach the window's own bindings, not a text field

    def _stop_recording(self, name: str) -> None:
        if self._recording == name:
            self._recording = None
        self._show_hotkey(name)

    def _show_hotkey(self, name: str) -> None:
        self.hotkey_labels[name].configure(text=format_hotkey(self.hotkey_specs[name]), fg_color=NEUTRAL,
                                           border_color=CARD_BORDER)

    def _focus_out(self, event) -> None:
        if event.widget is self and self._recording:  # switched to another window
            self._stop_recording(self._recording)

    def set_hotkey(self, name: str, spec: str) -> bool:
        """Assign ``spec`` to ``name``; returns False (and explains why) if it cannot be used."""
        if spec:
            reason = hotkey_problem(spec)
            if reason:
                self.hotkey_hint.configure(text=f"{format_hotkey(spec)}: {reason}.")
                return False
            for other, other_spec in self.hotkey_specs.items():
                if other != name and other_spec.strip().lower() == spec.lower():
                    title = dict(HOTKEY_ACTIONS)[other]
                    self.hotkey_hint.configure(text=f"{format_hotkey(spec)} уже назначено на «{title}».")
                    return False
        self.hotkey_specs[name] = spec
        self._recording = None
        self._show_hotkey(name)
        self.hotkey_hint.configure(text=f"{format_hotkey(spec)} — готово. Не забудьте нажать «Сохранить»."
                                   if spec else "Отключено. Не забудьте нажать «Сохранить».")
        return True

    def _record_press(self, event):
        name = self._recording
        if name is None:
            return None
        mod = MODIFIER_KEYSYMS.get(event.keysym)
        if mod:
            self._rec_mods.add(mod)
            self._rec_tap = (mod, time.monotonic()) if self._rec_mods == {mod} else (None, 0.0)
            return "break"
        self._rec_tap = self._rec_last_tap = (None, 0.0)
        if event.keysym == "Escape" and not self._rec_mods:
            self._stop_recording(name)
            return "break"
        key = hotkey_key_name(event.keysym, event.keycode, event.char)
        if key:
            self.set_hotkey(name, build_hotkey(self._rec_mods, key))
            self._rec_mods = set()
        return "break"

    def _record_release(self, event):
        name = self._recording
        mod = MODIFIER_KEYSYMS.get(event.keysym)
        if name is None or mod is None:
            return None
        self._rec_mods.discard(mod)
        tapped, pressed_at = self._rec_tap
        self._rec_tap = (None, 0.0)
        now = time.monotonic()
        if tapped != mod or now - pressed_at > 0.35 or mod not in ("shift", "ctrl"):
            return "break"
        last, last_at = self._rec_last_tap
        if last == mod and now - last_at < 0.6:
            self.set_hotkey(name, f"double_{mod}")
        else:
            self._rec_last_tap = (mod, now)
        return "break"

    def paste_key(self) -> None:
        try:
            text = self.clipboard_get().strip()
        except tk.TclError:
            text = ""
        if not text:
            self.key_status.configure(text="В буфере обмена пусто: сначала скопируйте ключ.")
            return
        self.var_key.set(text)
        self.key_status.configure(text="Ключ вставлен. Нажмите «Проверить», затем «Сохранить».")

    def _restore_placeholder(self, _event=None) -> None:
        if not self.var_key.get().strip() and self.config_copy.ai.api_key:
            self.var_key.set(KEY_PLACEHOLDER)

    def _entered_key(self) -> str | None:
        """The newly typed key, or None if the field was left as it was."""
        value = self.var_key.get().strip()
        if not value or value == KEY_PLACEHOLDER:
            return None
        return value

    def flash(self, text: str) -> None:
        self.status.configure(text=text)

    # -- actions -------------------------------------------------------------

    def collect(self):
        from .secrets import protect

        new = copy.deepcopy(self.config_copy)  # app.config is updated asynchronously
        new.enabled = self.var_enabled.get()
        new.look_back = self.var_look_back.get()
        new.convert_on_enter = self.var_enter.get()
        new.fix_caps_lock = self.var_caps.get()
        new.threshold = round(float(self.var_threshold.get()), 1)
        for name, spec in self.hotkey_specs.items():
            spec = spec.strip()
            if spec and parse_hotkey(spec) is None:
                raise ValueError(f"Не понял сочетание клавиш «{spec}»")
            setattr(new.hotkeys, name, spec)
        new.excluded_apps = [part.strip() for part in self.var_excluded.get().split(",") if part.strip()]
        new.ai.enabled = self.var_ai.get()
        new.ai.model = self.var_model.get().strip() or MODELS[0]
        try:
            new.ai.review_every = max(0, int(self.var_every.get().strip() or 0))
        except ValueError:
            raise ValueError("«Разбирать мои исправления»: нужно число, например 20") from None
        new.ai.fix_typos = self.var_typos.get()
        new.updates.check_automatically = self.var_auto_update.get()
        key = self._entered_key()
        if key is not None:
            new.ai.api_key = protect(key)
        return new

    def save(self) -> None:
        try:
            new = self.collect()
        except (ValueError, tk.TclError) as exc:
            messagebox.showerror("Switcher", str(exc), parent=self)
            return
        try:
            if self.var_autostart.get() != autostart.is_enabled():
                autostart.enable() if self.var_autostart.get() else autostart.disable()
        except OSError as exc:
            messagebox.showwarning("Switcher", f"Не удалось изменить автозапуск: {exc}", parent=self)
        self.app.update_config(new)
        self.config_copy = copy.deepcopy(new)
        if new.ai.api_key:
            self.var_key.set(KEY_PLACEHOLDER)
        self.flash("Сохранено ✓")

    def check_key(self) -> None:
        from .ai import AIError, Assistant

        try:
            new = self.collect()
        except (ValueError, tk.TclError) as exc:
            messagebox.showerror("Switcher", str(exc), parent=self)
            return
        assistant = Assistant(new.ai)
        if not assistant.has_credentials():
            self.key_status.configure(text="Сначала вставьте ключ API.")
            return
        self.key_status.configure(text="Проверяю…")

        def work():
            try:
                name = assistant.check_key()
                text = f"Ключ работает ✓ ({name}). Не забудьте нажать «Сохранить»."
            except AIError as exc:
                text = f"Не получилось: {exc}"
            except Exception as exc:  # network stack errors
                text = f"Не получилось: {exc}"
            self.ui.call(lambda: self.winfo_exists() and self.key_status.configure(text=text))

        threading.Thread(target=work, daemon=True).start()

    def review_now(self) -> None:
        if not self.app.ai_ready():
            messagebox.showinfo("Switcher", "Сначала включите Claude, вставьте ключ API и нажмите «Сохранить».",
                                parent=self)
            return
        self.flash("Claude изучает ваши исправления…")

        def work():
            try:
                _, _, outcome = self.app.review()
                message = f"Готово: {outcome.summary()}"
                style = outcome.style
            except Exception as exc:
                message, style = f"Не получилось: {exc}", ""

            def show():
                if not self.winfo_exists():
                    return
                self.flash(message)
                if style:
                    self._set_text(self.style_text, style)
                self.refresh_rules()

            self.ui.call(show)

        threading.Thread(target=work, daemon=True).start()

    def refresh_rules(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self._rules.clear()
        for row in rule_rows(self.app.profile, self.app.keyboard):
            item = self.tree.insert("", "end", values=(row["word"], row["result"], row["app"], row["source"],
                                                       row["hits"]))
            self._rules[item] = row["rule"]

    def add_word(self) -> None:
        word = self._ask("Добавить слово", "Слово, которое всегда должно быть именно таким (наберите его в нужной "
                                           "раскладке, например Kubernetes или коммит):")
        if not word or not word.strip():
            return
        word = word.strip()
        lang = text_lang(word)
        if lang not in (EN, RU):
            messagebox.showerror("Switcher", "Слово должно быть целиком на русском или на английском.", parent=self)
            return
        start, end = split_core(word, lang)
        strokes = self.app.keyboard.strokes(word[start:end].lower(), lang)
        if not strokes:
            messagebox.showerror("Switcher", "Это слово нельзя набрать на клавиатуре.", parent=self)
            return
        self.app.profile.add_rule("layout", canonical_keys(strokes), lang, source="user", note="добавлено вручную")
        self.refresh_rules()

    def add_typo(self) -> None:
        wrong = self._ask("Автозамена", "Как вы ошибаетесь (например, превет):")
        if not wrong or not wrong.strip():
            return
        right = self._ask("Автозамена", f"На что заменять «{wrong.strip()}»:")
        if not right or not right.strip():
            return
        self.app.profile.add_rule("replace", wrong.strip().lower(), right.strip().lower(), source="user",
                                  note="добавлено вручную")
        self.refresh_rules()

    def remove_rules(self) -> None:
        for item in self.tree.selection():
            rule = self._rules.get(item)
            if rule is not None:
                self.app.profile.remove_rule(rule.kind, rule.pattern, rule.app)
        self.refresh_rules()

    def refresh_stats(self) -> None:
        self._set_text(self.stats, stats_text(self.app.profile) or "Пока ничего — просто печатайте.")

    def open_data(self) -> None:
        from .tray import open_folder

        open_folder(data_dir())

    def forget(self) -> None:
        if messagebox.askyesno("Switcher", "Стереть все выученные правила, слова и журнал исправлений?",
                               parent=self):
            self.app.profile.forget()
            self.refresh_rules()
            self.refresh_stats()


def _is_windows() -> bool:
    import sys

    return sys.platform == "win32"
