"""The dialog for one button of the «Сохранить как» panel: caption, what it adds, its keys."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable

import customtkinter as ctk

from .ahk_dialogs import KeyCatcher
from .gui import (
    ACCENT,
    CARD_BORDER,
    MUTED,
    NEUTRAL,
    NEUTRAL_HOVER,
    SELECTED,
    TEXT,
    _set_icon,
)
from .hotkeys import format_hotkey
from .hotkeys import problem as hotkey_problem
from .savebar import Button, hotkey_vk

SAMPLE_FOLDER = "Папка заказа"


class ButtonDialog(ctk.CTkToplevel):
    """``on_done(button)`` returns an error to show, or None when the button is taken."""

    def __init__(self, master, on_done: Callable[[Button], str | None], button: Button | None = None):
        super().__init__(master)
        _set_icon(self)
        self.title(("Кнопка «" + button.label + "»" if button else "Новая кнопка") + " — Switcher")
        self.geometry("560x470")
        self.resizable(False, False)
        self.on_done = on_done
        button = button or Button("")
        self.spec = button.hotkey
        self.recording = False
        body = ctk.CTkFont(size=13)
        bold = ctk.CTkFont(size=13, weight="bold")
        pad = {"padx": 18, "fill": "x"}

        def field(title: str, value: str, hint: str) -> ctk.CTkEntry:
            ctk.CTkLabel(self, text=title, font=bold, anchor="w").pack(pady=(12, 4), **pad)
            entry = ctk.CTkEntry(self, height=34, font=body, placeholder_text=hint)
            if value:
                entry.insert(0, value)
            entry.pack(**pad)
            entry.bind("<KeyRelease>", lambda _e: self._preview(), add="+")
            return entry

        self.label = field("Надпись на кнопке", button.label, "Например: PANCH")
        self.prefix = field("Перед именем папки", button.prefix, "Необязательно")
        self.suffix = field("После имени папки", button.suffix, "Например: « PANCH» — с пробелом впереди")
        ctk.CTkLabel(self, text="Горячая клавиша (только в окне «Сохранить как»)", font=bold, anchor="w").pack(
            pady=(12, 4), **pad)
        keys = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        keys.pack(**pad)
        self.combo = ctk.CTkButton(keys, text=self._combo_text(), height=34, font=bold, fg_color=NEUTRAL,
                                   hover_color=NEUTRAL_HOVER, text_color=TEXT, border_width=1,
                                   border_color=CARD_BORDER, command=self.start_recording)
        self.combo.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(keys, text="×", width=34, height=34, fg_color="transparent", hover_color=NEUTRAL_HOVER,
                      text_color=MUTED, font=ctk.CTkFont(size=18), command=self._clear_keys).pack(
            side="left", padx=(6, 0))
        self.enabled = tk.BooleanVar(value=button.enabled)
        ctk.CTkCheckBox(self, text="Показывать на панели", variable=self.enabled, font=body).pack(
            pady=(12, 0), **pad)
        self.preview = ctk.CTkLabel(self, text="", font=ctk.CTkFont(size=12), text_color=MUTED, anchor="w",
                                    justify="left", wraplength=520)
        self.preview.pack(pady=(10, 0), **pad)
        self.status = ctk.CTkLabel(self, text="", font=ctk.CTkFont(size=12), text_color=MUTED, anchor="w",
                                   justify="left", wraplength=520)
        self.status.pack(pady=(4, 0), **pad)
        buttons = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        buttons.pack(side="bottom", fill="x", padx=18, pady=16)
        ctk.CTkButton(buttons, text="Отмена", width=110, height=34, font=body, fg_color=NEUTRAL,
                      hover_color=NEUTRAL_HOVER, text_color=TEXT, border_width=1, border_color=CARD_BORDER,
                      command=self.destroy).pack(side="right")
        ctk.CTkButton(buttons, text="Готово", width=130, height=34, font=body, command=self.done).pack(
            side="right", padx=(0, 8))
        KeyCatcher(self, self._recorded, self._stop_recording)
        self._preview()
        self.after(50, self.lift)
        self.after(80, self.label.focus_set)

    def _combo_text(self) -> str:
        if self.recording:
            return "Нажмите клавиши…"
        return format_hotkey(self.spec) if self.spec else "Без клавиши — нажмите здесь, чтобы назначить"

    def _preview(self) -> None:
        text = Button(self.label.get().strip() or "?", self.prefix.get(), self.suffix.get()).text(SAMPLE_FOLDER)
        self.preview.configure(text=f"Скопирует: «{text}»")

    def start_recording(self) -> None:
        self.recording = True
        self.combo.configure(text=self._combo_text(), fg_color=SELECTED, border_color=ACCENT)
        self.focus_set()

    def _stop_recording(self) -> None:
        self.recording = False
        self.combo.configure(text=self._combo_text(), fg_color=NEUTRAL, border_color=CARD_BORDER)

    def _clear_keys(self) -> None:
        self.spec = ""
        self._stop_recording()

    def _recorded(self, spec: str) -> None:
        if not self.recording:
            return
        problem = hotkey_problem(spec)
        if problem is None and hotkey_vk(spec) is None:
            problem = "эту клавишу панель не понимает — возьмите букву, цифру или F1–F12"
        if problem:
            self.status.configure(text=f"{format_hotkey(spec)}: {problem}.")
            return
        self.spec = spec
        self._stop_recording()
        self.status.configure(text="")

    def done(self) -> None:
        label = self.label.get().strip()
        if not label:
            self.status.configure(text="Напишите надпись для кнопки.")
            return
        error = self.on_done(Button(label, self.prefix.get(), self.suffix.get(), self.spec, self.enabled.get()))
        if error:
            self.status.configure(text=error)
            return
        self.destroy()
