"""Settings window (tkinter): everything a user needs without the command line.

Tk lives on the main thread.  Other threads (tray menu, AI calls) hand work
to it through :meth:`Ui.call`.
"""

from __future__ import annotations

import copy
import logging
import queue
import threading
import tkinter as tk
import webbrowser
from tkinter import messagebox, simpledialog, ttk

from . import autostart
from .controller import parse_hotkey
from .engine import split_core
from .layouts import EN, RU, canonical_keys, text_lang
from .paths import data_dir
from .report import rule_rows, stats_text

log = logging.getLogger(__name__)

MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5"]
KEY_PLACEHOLDER = "••••••••••••••••"
PAD = {"padx": 10, "pady": 4}


class Ui:
    def __init__(self, app):
        self.app = app
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("Switcher")
        _set_icon(self.root)
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


def _set_icon(window) -> None:
    from .tray import bundled_icon_path

    path = bundled_icon_path()
    if path is not None:
        try:
            window.iconbitmap(default=str(path))
        except tk.TclError:
            pass


class SettingsWindow(tk.Toplevel):
    def __init__(self, ui: Ui, welcome: bool = False):
        super().__init__(ui.root)
        self.ui = ui
        self.app = ui.app
        self.config_copy = copy.deepcopy(self.app.config)
        self.title("Switcher — настройки")
        self.minsize(640, 560)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self._build(welcome)

    # -- layout --------------------------------------------------------------

    def _build(self, welcome: bool) -> None:
        if welcome:
            banner = ttk.Label(self, wraplength=600, justify="left", text=(
                "Switcher уже работает — его значок в трее у часов. Просто печатайте: слова, набранные "
                "не в той раскладке, исправятся на пробеле. Двойной Shift исправляет или отменяет "
                "последнее слово, и каждое такое исправление Switcher запоминает.\n\n"
                "Чтобы подключить Claude (разбор ваших ошибок и исправление целых фраз), вставьте ключ API "
                "ниже и нажмите «Сохранить»."))
            banner.pack(fill="x", padx=12, pady=(12, 0))
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=8)
        self.tabs = {
            "main": self._main_tab(),
            "ai": self._ai_tab(),
            "rules": self._rules_tab(),
            "stats": self._stats_tab(),
        }
        bottom = ttk.Frame(self)
        bottom.pack(fill="x", padx=8, pady=(0, 10))
        self.status = ttk.Label(bottom, text="")
        self.status.pack(side="left", padx=4)
        ttk.Button(bottom, text="Закрыть", command=self.destroy).pack(side="right", padx=4)
        ttk.Button(bottom, text="Сохранить", command=self.save).pack(side="right", padx=4)

    def show(self, tab: str | None = None) -> None:
        if tab in self.tabs:
            self.notebook.select(self.tabs[tab])
            if tab == "stats":
                self.refresh_stats()
        self.deiconify()
        self.lift()
        self.focus_force()

    def _main_tab(self) -> ttk.Frame:
        c = self.config_copy
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="Основное")
        self.var_enabled = tk.BooleanVar(value=c.enabled)
        self.var_autostart = tk.BooleanVar(value=autostart.is_enabled())
        self.var_look_back = tk.BooleanVar(value=c.look_back)
        self.var_enter = tk.BooleanVar(value=c.convert_on_enter)
        self.var_caps = tk.BooleanVar(value=c.fix_caps_lock)
        for var, text in (
            (self.var_enabled, "Исправлять раскладку автоматически"),
            (self.var_autostart, "Запускать вместе с Windows" if _is_windows() else "Запускать при входе в систему"),
            (self.var_look_back, "Исправлять и короткое слово перед исправленным («e vtyz» → «у меня»)"),
            (self.var_enter, "Исправлять слово перед Enter (в чатах сообщение может уйти раньше)"),
            (self.var_caps, "Исправлять случайный Caps Lock («пРИВЕТ» → «Привет»)"),
        ):
            ttk.Checkbutton(frame, text=text, variable=var).pack(anchor="w", **PAD)

        box = ttk.LabelFrame(frame, text="Осторожность")
        box.pack(fill="x", padx=10, pady=8)
        self.var_threshold = tk.DoubleVar(value=c.threshold)
        ttk.Label(box, text="смелее").grid(row=0, column=0, padx=6)
        ttk.Scale(box, from_=1.0, to=4.0, variable=self.var_threshold, orient="horizontal",
                  command=lambda _: self.threshold_label.config(text=f"{self.var_threshold.get():.1f}")
                  ).grid(row=0, column=1, sticky="ew", pady=6)
        ttk.Label(box, text="осторожнее").grid(row=0, column=2, padx=6)
        self.threshold_label = ttk.Label(box, text=f"{c.threshold:.1f}", width=4)
        self.threshold_label.grid(row=0, column=3, padx=6)
        box.columnconfigure(1, weight=1)

        keys = ttk.LabelFrame(frame, text="Горячие клавиши")
        keys.pack(fill="x", padx=10, pady=8)
        self.var_hotkeys = {}
        for row, (name, label) in enumerate((
            ("convert_last", "Исправить / отменить последнее слово"),
            ("convert_selection", "Перевести выделенный текст"),
            ("ai_fix", "Исправить фразу с Claude"),
            ("toggle", "Пауза"),
        )):
            var = tk.StringVar(value=getattr(c.hotkeys, name))
            self.var_hotkeys[name] = var
            ttk.Label(keys, text=label).grid(row=row, column=0, sticky="w", padx=6, pady=2)
            ttk.Entry(keys, textvariable=var, width=26).grid(row=row, column=1, sticky="w", padx=6, pady=2)
        ttk.Label(keys, foreground="#666", text="double_shift — двойное нажатие Shift; сочетания: <ctrl>+<alt>+x"
                  ).grid(row=4, column=0, columnspan=2, sticky="w", padx=6, pady=(2, 6))

        ttk.Label(frame, text="Не работать в программах (через запятую, часть имени процесса):").pack(
            anchor="w", padx=10, pady=(8, 0))
        self.var_excluded = tk.StringVar(value=", ".join(c.excluded_apps))
        ttk.Entry(frame, textvariable=self.var_excluded).pack(fill="x", padx=10, pady=4)
        return frame

    def _ai_tab(self) -> ttk.Frame:
        c = self.config_copy.ai
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="Claude (ИИ)")
        self.var_ai = tk.BooleanVar(value=c.enabled)
        ttk.Checkbutton(frame, text="Использовать Claude", variable=self.var_ai).pack(anchor="w", **PAD)

        key_row = ttk.Frame(frame)
        key_row.pack(fill="x", padx=10, pady=4)
        ttk.Label(key_row, text="Ключ API:").pack(side="left")
        self.var_key = tk.StringVar(value=KEY_PLACEHOLDER if c.api_key else "")
        self.key_entry = ttk.Entry(key_row, textvariable=self.var_key, show="•", width=48)
        self.key_entry.pack(side="left", padx=6, fill="x", expand=True)
        self.key_entry.bind("<FocusIn>", self._clear_placeholder)
        self.key_entry.bind("<FocusOut>", self._restore_placeholder)
        ttk.Button(key_row, text="Проверить", command=self.check_key).pack(side="left")
        link = ttk.Label(frame, text="Где взять ключ: console.anthropic.com → API Keys", foreground="#2563eb",
                         cursor="hand2")
        link.pack(anchor="w", padx=10)
        link.bind("<Button-1>", lambda _: webbrowser.open("https://console.anthropic.com/settings/keys"))
        self.key_status = ttk.Label(frame, text="Ключ хранится зашифрованным (Windows DPAPI)." if _is_windows()
                                    else "")
        self.key_status.pack(anchor="w", padx=10, pady=(0, 6))

        grid = ttk.Frame(frame)
        grid.pack(fill="x", padx=10, pady=4)
        ttk.Label(grid, text="Модель:").grid(row=0, column=0, sticky="w", pady=3)
        self.var_model = tk.StringVar(value=c.model)
        ttk.Combobox(grid, textvariable=self.var_model, values=MODELS, width=24).grid(
            row=0, column=1, columnspan=2, sticky="w", padx=6)
        ttk.Label(grid, text="Разбирать мои исправления каждые").grid(row=1, column=0, sticky="w", pady=3)
        self.var_every = tk.IntVar(value=c.review_every)
        ttk.Spinbox(grid, from_=0, to=500, textvariable=self.var_every, width=6).grid(row=1, column=1, sticky="w",
                                                                                    padx=6)
        ttk.Label(grid, text="исправлений (0 — только вручную)").grid(row=1, column=2, sticky="w")
        self.var_typos = tk.BooleanVar(value=c.fix_typos)
        ttk.Checkbutton(frame, text="При исправлении фразы также исправлять опечатки",
                        variable=self.var_typos).pack(anchor="w", **PAD)

        ttk.Button(frame, text="Разобрать мои исправления сейчас", command=self.review_now).pack(
            anchor="w", padx=10, pady=6)
        ttk.Label(frame, text="Ваш стиль (по мнению Claude):").pack(anchor="w", padx=10, pady=(6, 0))
        self.style_text = tk.Text(frame, height=8, wrap="word", relief="flat", background="#f5f5f5")
        self.style_text.pack(fill="both", expand=True, padx=10, pady=(2, 10))
        self._set_text(self.style_text, self.app.profile.get_meta("style_summary")
                       or "Пока пусто: Claude составит описание после первого разбора ваших исправлений.")
        return frame

    def _rules_tab(self) -> ttk.Frame:
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="Правила")
        ttk.Label(frame, wraplength=600, justify="left", text=(
            "Правила появляются сами, когда вы исправляете Switcher, а ещё их предлагает Claude. "
            "Здесь их можно посмотреть, добавить или удалить.")).pack(anchor="w", padx=10, pady=6)
        columns = ("word", "result", "app", "source", "hits")
        self.tree = ttk.Treeview(frame, columns=columns, show="headings", height=14)
        for col, title, width in (("word", "Слово", 150), ("result", "Что делать", 190), ("app", "Где", 90),
                                  ("source", "Кто добавил", 130), ("hits", "Раз", 50)):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=10)
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", padx=10, pady=8)
        ttk.Button(buttons, text="Добавить слово…", command=self.add_word).pack(side="left")
        ttk.Button(buttons, text="Добавить автозамену…", command=self.add_typo).pack(side="left", padx=6)
        ttk.Button(buttons, text="Удалить выбранные", command=self.remove_rules).pack(side="left")
        self._rules: dict[str, object] = {}
        self.refresh_rules()
        return frame

    def _stats_tab(self) -> ttk.Frame:
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="Что я о вас знаю")
        self.stats = tk.Text(frame, wrap="word", relief="flat", background="#f5f5f5")
        self.stats.pack(fill="both", expand=True, padx=10, pady=8)
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", padx=10, pady=(0, 8))
        ttk.Button(buttons, text="Обновить", command=self.refresh_stats).pack(side="left")
        ttk.Button(buttons, text="Папка с данными", command=self.open_data).pack(side="left", padx=6)
        ttk.Button(buttons, text="Стереть всё выученное…", command=self.forget).pack(side="right")
        self.refresh_stats()
        return frame

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _set_text(widget: tk.Text, text: str) -> None:
        widget.config(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.config(state="disabled")

    def _clear_placeholder(self, _event=None) -> None:
        if self.var_key.get() == KEY_PLACEHOLDER:
            self.var_key.set("")

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
        self.status.config(text=text)

    # -- actions -------------------------------------------------------------

    def collect(self):
        from .secrets import protect

        new = copy.deepcopy(self.config_copy)  # app.config is updated asynchronously
        new.enabled = self.var_enabled.get()
        new.look_back = self.var_look_back.get()
        new.convert_on_enter = self.var_enter.get()
        new.fix_caps_lock = self.var_caps.get()
        new.threshold = round(float(self.var_threshold.get()), 1)
        for name, var in self.var_hotkeys.items():
            spec = var.get().strip()
            if spec and parse_hotkey(spec) is None:
                raise ValueError(f"Не понял сочетание клавиш «{spec}»")
            setattr(new.hotkeys, name, spec)
        new.excluded_apps = [part.strip() for part in self.var_excluded.get().split(",") if part.strip()]
        new.ai.enabled = self.var_ai.get()
        new.ai.model = self.var_model.get().strip() or MODELS[0]
        new.ai.review_every = max(0, int(self.var_every.get()))
        new.ai.fix_typos = self.var_typos.get()
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
            self.key_status.config(text="Сначала вставьте ключ API.")
            return
        self.key_status.config(text="Проверяю…")

        def work():
            try:
                name = assistant.check_key()
                text = f"Ключ работает ✓ ({name}). Не забудьте нажать «Сохранить»."
            except AIError as exc:
                text = f"Не получилось: {exc}"
            except Exception as exc:  # network stack errors
                text = f"Не получилось: {exc}"
            self.ui.call(lambda: self.winfo_exists() and self.key_status.config(text=text))

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
        word = simpledialog.askstring(
            "Добавить слово",
            "Слово, которое всегда должно быть именно таким\n(наберите его в нужной раскладке, например Kubernetes "
            "или коммит):", parent=self)
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
        wrong = simpledialog.askstring("Автозамена", "Как вы ошибаетесь (например, превет):", parent=self)
        if not wrong or not wrong.strip():
            return
        right = simpledialog.askstring("Автозамена", f"На что заменять «{wrong.strip()}»:", parent=self)
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
