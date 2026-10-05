"""Small dialogs for AutoHotkey hotkeys: record a key combination, add a hotkey without code."""

from __future__ import annotations

import time
import tkinter as tk
from typing import Callable

import customtkinter as ctk

from .ahk_hotkeys import ACTIONS
from .gui import ACCENT, CARD_BORDER, MUTED, NEUTRAL, NEUTRAL_HOVER, SELECTED, TEXT, _set_icon
from .hotkeys import MODIFIER_KEYSYMS, build_spec, format_hotkey, key_name
from .hotkeys import problem as hotkey_problem

PLACEHOLDERS = {
    "text": "Что напечатать, например: С уважением, Ильдар",
    "run": "Программа, папка или сайт: notepad, C:\\Работа, https://claude.ai",
    "code": "Одна строка AutoHotkey, например: Send \"{Volume_Mute}\"",
}


class KeyCatcher:
    """Turns key presses on a widget into a combination: "<ctrl>+<alt>+d" (no double taps)."""

    def __init__(self, widget, on_combo: Callable[[str], None], on_cancel: Callable[[], None] | None = None):
        self.on_combo, self.on_cancel = on_combo, on_cancel
        self.mods: set[str] = set()
        widget.bind("<KeyPress>", self.press, add="+")
        widget.bind("<KeyRelease>", self.release, add="+")

    def press(self, event):
        mod = MODIFIER_KEYSYMS.get(event.keysym)
        if mod:
            self.mods.add(mod)
            return "break"
        if event.keysym == "Escape" and not self.mods:
            if self.on_cancel:
                self.on_cancel()
            return "break"
        key = key_name(event.keysym, event.keycode, event.char)
        if key:
            self.on_combo(build_spec(self.mods, key))
            self.mods = set()
        return "break"

    def release(self, event):
        mod = MODIFIER_KEYSYMS.get(event.keysym)
        if mod:
            self.mods.discard(mod)
        return "break"


def combo_problem(spec: str) -> str | None:
    return hotkey_problem(spec)


class AddHotkeyDialog(ctk.CTkToplevel):
    """A new hotkey: its keys, what it does, and a note for the list."""

    def __init__(self, master, on_done: Callable[[str, str, str, str], str | None], spec: str = ""):
        super().__init__(master)
        _set_icon(self)
        self.title("Новая горячая клавиша — Switcher")
        self.geometry("560x380")
        self.resizable(False, False)
        self.on_done = on_done
        self.spec = spec
        self.recording = False
        body = ctk.CTkFont(size=13)
        pad = {"padx": 18, "fill": "x"}

        ctk.CTkLabel(self, text="Сочетание клавиш", font=ctk.CTkFont(size=13, weight="bold"), anchor="w").pack(
            pady=(16, 4), **pad)
        self.combo = ctk.CTkButton(self, text=self._combo_text(), height=36, font=ctk.CTkFont(size=13, weight="bold"),
                                   fg_color=NEUTRAL, hover_color=NEUTRAL_HOVER, text_color=TEXT, border_width=1,
                                   border_color=CARD_BORDER, command=self.start_recording)
        self.combo.pack(**pad)
        ctk.CTkLabel(self, text="Что делать", font=ctk.CTkFont(size=13, weight="bold"), anchor="w").pack(
            pady=(14, 4), **pad)
        self.kind = tk.StringVar(value="text")
        self.kinds = ctk.CTkSegmentedButton(self, values=[title for _, title in ACTIONS], font=body,
                                            command=self._kind_chosen)
        self.kinds.set(ACTIONS[0][1])
        self.kinds.pack(**pad)
        self.value = ctk.CTkEntry(self, height=34, font=body, placeholder_text=PLACEHOLDERS["text"])
        self.value.pack(pady=(8, 0), **pad)
        self.note = ctk.CTkEntry(self, height=34, font=body, placeholder_text="Подпись для списка (необязательно)")
        self.note.pack(pady=(8, 0), **pad)
        self.status = ctk.CTkLabel(self, text="", font=ctk.CTkFont(size=12), text_color=MUTED, anchor="w",
                                   justify="left", wraplength=520)
        self.status.pack(pady=(8, 0), **pad)
        buttons = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        buttons.pack(side="bottom", fill="x", padx=18, pady=16)
        ctk.CTkButton(buttons, text="Отмена", width=110, height=34, font=body, fg_color=NEUTRAL,
                      hover_color=NEUTRAL_HOVER, text_color=TEXT, border_width=1, border_color=CARD_BORDER,
                      command=self.destroy).pack(side="right")
        ctk.CTkButton(buttons, text="Добавить", width=130, height=34, font=body, command=self.done).pack(
            side="right", padx=(0, 8))
        KeyCatcher(self, self._recorded, self._stop_recording)
        self.after(50, self.lift)
        if not spec:
            self.after(100, self.start_recording)

    def _combo_text(self) -> str:
        if self.recording:
            return "Нажмите клавиши…"
        return format_hotkey(self.spec) if self.spec else "Нажмите здесь, затем нужные клавиши"

    def _kind_chosen(self, title: str) -> None:
        kind = next(k for k, t in ACTIONS if t == title)
        self.kind.set(kind)
        self.value.configure(placeholder_text=PLACEHOLDERS[kind])

    def start_recording(self) -> None:
        self.recording = True
        self.combo.configure(text=self._combo_text(), fg_color=SELECTED, border_color=ACCENT)
        self.focus_set()

    def _stop_recording(self) -> None:
        self.recording = False
        self.combo.configure(text=self._combo_text(), fg_color=NEUTRAL, border_color=CARD_BORDER)

    def _recorded(self, spec: str) -> None:
        if not self.recording:
            return
        problem = combo_problem(spec)
        if problem:
            self.status.configure(text=f"{format_hotkey(spec)}: {problem}.")
            return
        self.spec = spec
        self._stop_recording()
        self.status.configure(text="")
        self.value.focus_set()

    def done(self) -> None:
        if not self.spec:
            self.status.configure(text="Сначала нажмите сочетание клавиш.")
            return
        value = self.value.get().strip()
        if not value:
            self.status.configure(text="Напишите, что делать.")
            return
        error = self.on_done(self.spec, self.kind.get(), value, self.note.get().strip())
        if error:
            self.status.configure(text=error)
            return
        self.destroy()


class RecordDialog(ctk.CTkToplevel):
    """Press the new keys for a hotkey; ``on_done(spec)`` returns an error to show, or None."""

    def __init__(self, master, title: str, on_done: Callable[[str], str | None]):
        super().__init__(master)
        _set_icon(self)
        self.title("Новое сочетание — Switcher")
        self.geometry("480x190")
        self.resizable(False, False)
        self.on_done = on_done
        ctk.CTkLabel(self, text=title, font=ctk.CTkFont(size=13, weight="bold"), anchor="w", justify="left",
                     wraplength=440).pack(fill="x", padx=18, pady=(16, 6))
        self.prompt = ctk.CTkLabel(self, text="Нажмите новое сочетание клавиш (Esc — отмена)", height=40,
                                   font=ctk.CTkFont(size=14), fg_color=SELECTED, corner_radius=8)
        self.prompt.pack(fill="x", padx=18)
        self.status = ctk.CTkLabel(self, text="", font=ctk.CTkFont(size=12), text_color=MUTED, anchor="w",
                                   justify="left", wraplength=440)
        self.status.pack(fill="x", padx=18, pady=(8, 0))
        KeyCatcher(self, self._recorded, self.destroy)
        self.after(50, self.lift)
        self.after(80, self.focus_force)
        self._done_at = 0.0

    def _recorded(self, spec: str) -> None:
        problem = combo_problem(spec)
        if problem:
            self.status.configure(text=f"{format_hotkey(spec)}: {problem}.")
            return
        error = self.on_done(spec)
        if error:
            self.status.configure(text=error)
            return
        self._done_at = time.monotonic()
        self.destroy()
