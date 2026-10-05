"""A small editor for AutoHotkey scripts: highlighting, line numbers, syntax check, reload, Claude."""

from __future__ import annotations

import logging
import re
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from .ahk import name_of, read_script, script_version, write_script
from .gui import CARD_BG, MUTED, TEXT, _set_icon

log = logging.getLogger(__name__)

# (tag, pattern); later tags win where they overlap
_KEYWORDS = ("if|else|return|loop|while|for|in|until|break|continue|goto|gosub|switch|case|default|try|catch|"
             "finally|throw|global|local|static|class|extends|and|or|not|is|true|false")
_COMMANDS = ("Send|SendInput|SendText|SendRaw|SendEvent|SendPlay|SendMode|SetKeyDelay|Sleep|MsgBox|InputBox|"
             "ToolTip|TrayTip|Run|RunWait|WinActivate|WinWait|WinWaitActive|WinClose|WinExist|WinActive|"
             "IfWinActive|IfWinExist|ExitApp|Reload|Suspend|Pause|SetTimer|Hotkey|Hotstring|Click|MouseMove|"
             "ControlSend|ControlClick|KeyWait|GetKeyState|SetTitleMatchMode|FormatTime|FileRead|FileAppend|"
             "StrReplace|SubStr|InStr|StrLen|RegExMatch|RegExReplace|Clipboard|A_Clipboard|ClipWait")
RULES = (
    ("number", re.compile(r"\b(?:0x[0-9a-fA-F]+|\d+(?:\.\d+)?)\b")),
    ("keyword", re.compile(rf"\b(?:{_KEYWORDS})\b", re.I)),
    ("command", re.compile(rf"\b(?:{_COMMANDS})\b", re.I)),
    ("variable", re.compile(r"%\w+%|\bA_\w+")),
    ("hotkey", re.compile(r"^[ \t]*(?::[^:\n]*:[^\n]*?::|[^\s;:][^\n]*?::)", re.M)),
    ("directive", re.compile(r"^[ \t]*#\w+", re.M)),
    ("string", re.compile(r'"(?:[^"\n`]|`.)*"')),
    ("comment", re.compile(r"(?:^|(?<=[ \t]));[^\n]*|^[ \t]*/\*.*?(?:\*/|\Z)", re.M | re.S)),
)
SINGLE_QUOTED = re.compile(r"'(?:[^'\n`]|`.)*'")  # strings in v2 only: in v1 "don't" is plain text
COLORS = {
    "comment": ("#6A9955", "#6A9955"),
    "string": ("#A31515", "#CE9178"),
    "directive": ("#AF00DB", "#C586C0"),
    "hotkey": ("#0451A5", "#4FC1FF"),
    "keyword": ("#0000FF", "#569CD6"),
    "command": ("#795E26", "#DCDCAA"),
    "variable": ("#001080", "#9CDCFE"),
    "number": ("#098658", "#B5CEA8"),
    "error": ("#FDE2E1", "#5A1D1D"),
    "conflict": ("#FFF4CE", "#4A3A00"),  # a line that takes keys from Switcher
}


def spans(text: str, major: int | None = None) -> list[tuple[str, int, int]]:
    """(tag, start, end) character offsets to colour."""
    found = [(tag, m.start(), m.end()) for tag, pattern in RULES for m in pattern.finditer(text)]
    if major == 2:
        found += [("string", m.start(), m.end()) for m in SINGLE_QUOTED.finditer(text)]
    return found


def _monospace() -> str:
    return "Consolas" if sys.platform == "win32" else "DejaVu Sans Mono"


class ScriptEditor(ctk.CTkToplevel):
    def __init__(self, master, app, path: str, on_saved=None):
        super().__init__(master)
        _set_icon(self)
        self.app = app
        self.path = path
        self.on_saved = on_saved
        self.text_value, self.encoding, self.newline = read_script(path)
        self.major = script_version(self.text_value) or self._installed_major()
        self.title(f"{name_of(path)} — AutoHotkey — Switcher")
        self.geometry("900x640")
        self.minsize(640, 400)
        self.protocol("WM_DELETE_WINDOW", self.close)
        body = ctk.CTkFont(size=13)
        code = ctk.CTkFont(family=_monospace(), size=13)

        bar = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        bar.pack(fill="x", padx=12, pady=(12, 6))
        names = ctk.CTkFrame(bar, fg_color="transparent", corner_radius=0)
        names.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(names, text=name_of(path), font=ctk.CTkFont(size=16, weight="bold"), anchor="w").pack(fill="x")
        ctk.CTkLabel(names, text=path, font=ctk.CTkFont(size=11), text_color=MUTED, anchor="w").pack(fill="x")
        buttons = [("Сохранить", self.save, True), ("Сохранить и перезапустить", self.save_and_reload, False),
                   ("Проверить", self.check, False), ("Claude…", self.ask_claude, False),
                   ("Другой редактор", self.edit_elsewhere, False)]
        self.buttons = {}
        for text, command, primary in reversed(buttons):
            kw = {} if primary else {"fg_color": ("#F7F7F7", "#363636"), "hover_color": ("#EBEBEB", "#414141"),
                                     "text_color": TEXT, "border_width": 1, "border_color": ("#E5E5E5", "#3A3A3A")}
            button = ctk.CTkButton(bar, text=text, command=command, font=body, height=32, width=10, **kw)
            button.pack(side="right", padx=(6, 0))
            self.buttons[text] = button
        if not self._ai_ready():
            self.buttons["Claude…"].configure(state="disabled")

        frame = ctk.CTkFrame(self, corner_radius=8, border_width=1)
        frame.pack(fill="both", expand=True, padx=12)
        self.gutter = tk.Canvas(frame, width=44, highlightthickness=0, borderwidth=0)
        self.gutter.pack(side="left", fill="y", padx=(6, 0), pady=6)
        self.text = ctk.CTkTextbox(frame, wrap="none", font=code, undo=True, maxundo=-1, border_width=0,
                                   corner_radius=0, activate_scrollbars=True)
        self.text.pack(side="left", fill="both", expand=True, padx=(0, 4), pady=4)
        self.text.insert("1.0", self.text_value)
        self.text.edit_reset()
        self.text.edit_modified(False)
        self.code_font = code

        self.status = ctk.CTkLabel(self, text="", font=body, text_color=MUTED, anchor="w", justify="left",
                                   wraplength=860)
        self.status.pack(fill="x", padx=14, pady=(6, 10))

        self.text.bind("<Return>", self._newline)
        self.text.bind("<<Modified>>", self._modified)
        self.bind("<Control-KeyPress>", self._ctrl_key)
        self._colours()
        ctk.AppearanceModeTracker.add(self._colours, self)
        self._highlight_job = None
        self._view = None
        self._note_until = 0.0  # a message stays this long before the cursor position comes back
        self._claude_result = None  # (script asked about, answer or error), left by the Claude thread
        self._highlight()
        self.show_conflicts()  # an opened script that takes Switcher's keys says so at once
        self._tick()
        self.after(50, self.text.focus_set)
        self.lift()

    # -- helpers ---------------------------------------------------------------

    def _manager(self):
        return getattr(self.app, "ahk", None)

    def _installed_major(self) -> int:
        manager = self._manager()
        return manager.preferred_major() if manager is not None else 2

    def _ai_ready(self) -> bool:
        try:
            return bool(self.app.ai_ready())
        except Exception:
            return False

    def content(self) -> str:
        return self.text.get("1.0", "end-1c")

    @property
    def dirty(self) -> bool:
        return self.content() != self.text_value

    def say(self, message: str, keep: float = 5.0) -> None:
        """Show a message under the text; ``keep``: seconds before the cursor position replaces it."""
        self.status.configure(text=message)
        self._note_until = time.monotonic() + keep
        self._view = None

    # -- look --------------------------------------------------------------------

    def _colours(self, _mode=None) -> None:
        dark = ctk.get_appearance_mode() == "Dark"
        for tag, pair in COLORS.items():
            if tag in ("error", "conflict"):
                self.text.tag_config(tag, background=pair[dark])
            else:
                self.text.tag_config(tag, foreground=pair[dark])
        for tag in ("string", "comment"):
            self.text.tag_raise(tag)
        self.text.tag_lower("error")
        self.text.tag_lower("conflict")
        self.gutter.configure(background=CARD_BG[dark])
        self._gutter_fg = MUTED[dark]
        self._view = None

    def _modified(self, _event=None) -> None:
        if self.text.edit_modified():
            self.text.edit_modified(False)
            if self.text.tag_ranges("error") or self.text.tag_ranges("conflict"):
                self.text.tag_remove("error", "1.0", "end")
                self.text.tag_remove("conflict", "1.0", "end")
                self._note_until = 0.0
            if self._highlight_job is not None:
                self.after_cancel(self._highlight_job)
            self._highlight_job = self.after(250, self._highlight)

    def _highlight(self) -> None:
        self._highlight_job = None
        text = self.content()
        for tag, _ in RULES:
            self.text.tag_remove(tag, "1.0", "end")
        for tag, start, end in spans(text, self.major):
            self.text.tag_add(tag, f"1.0+{start}c", f"1.0+{end}c")

    def _tick(self) -> None:
        """Line numbers and the cursor position, redrawn when the view changes; Claude's answer."""
        try:
            if self._claude_result is not None:
                before, result = self._claude_result
                self._claude_result = None
                self._claude_done(before, result)
            first = self.text.index("@0,0")
            cursor = self.text.index("insert")
            view = (first, self.text.index("end"), cursor, self.text.winfo_height())
            if view != self._view:
                self._view = view
                self._draw_gutter()
            if time.monotonic() > self._note_until:
                line, column = cursor.split(".")
                self.status.configure(text=f"Строка {line}, столбец {int(column) + 1} · AutoHotkey v{self.major}")
        except tk.TclError:
            return
        self.after(120, self._tick)

    def _draw_gutter(self) -> None:
        self.gutter.delete("all")
        index = self.text.index("@0,0")
        inner = getattr(self.text, "_textbox", self.text)  # the tk.Text inside CustomTkinter's frame
        offset = inner.winfo_rooty() - self.gutter.winfo_rooty()
        while True:
            info = self.text.dlineinfo(index)
            if info is None:
                break
            self.gutter.create_text(38, info[1] + offset, anchor="ne", text=index.split(".")[0],
                                    font=self.code_font, fill=self._gutter_fg)
            following = self.text.index(f"{index}+1line")
            if following == index:
                break
            index = following

    def _newline(self, _event=None):
        """Enter keeps the indentation of the line above."""
        line = self.text.get("insert linestart", "insert")
        indent = re.match(r"[ \t]*", line).group(0)
        if line.rstrip().endswith("{"):
            indent += "    "
        self.text.insert("insert", "\n" + indent)
        self.text.see("insert")
        return "break"

    def _ctrl_key(self, event):
        # by the physical key: with the Russian layout Tk gets "ы" for Ctrl+S
        if event.keysym.lower() in ("s", "cyrillic_yeru") or (sys.platform == "win32" and event.keycode == 83):
            self.save()
            return "break"
        return None

    def goto(self, line: int) -> None:
        """Put the cursor on a line and show it (opened from the list of hotkeys)."""
        self.text.mark_set("insert", f"{line}.0")
        self.text.see(f"{line}.0")
        self.text.tag_remove("sel", "1.0", "end")
        self.text.tag_add("sel", f"{line}.0", f"{line}.0 lineend")
        self.text.focus_set()

    def show_error(self, line: int | None, message: str) -> None:
        self.text.tag_remove("error", "1.0", "end")
        if line:
            self.text.tag_add("error", f"{line}.0", f"{line}.0 lineend+1c")
            self.text.see(f"{line}.0")
            self.text.mark_set("insert", f"{line}.0")
        self.say("⚠ " + message, keep=3600)  # until the text is edited

    def show_conflicts(self, saved: bool = False) -> list:
        """Mark the lines that take keys from Switcher and say what each one does to it."""
        manager = self._manager()
        found = manager.conflicts(self.path, self.content()) if manager is not None else []
        self.text.tag_remove("conflict", "1.0", "end")
        for conflict in found:
            self.text.tag_add("conflict", f"{conflict.line}.0", f"{conflict.line}.0 lineend+1c")
        if found:
            first = found[0]
            more = f" (и ещё {len(found) - 1}, они подсвечены)" if len(found) > 1 else ""
            done = "Сохранено. " if saved else ""
            self.say(f"{done}⚠ Пересечение со Switcher — строка {first.line}: {first.message}{more}", keep=60)
        return found

    # -- actions -----------------------------------------------------------------

    def save(self) -> bool:
        try:
            used = write_script(self.path, self.content(), self.encoding, self.newline)
        except OSError as exc:
            messagebox.showerror("Switcher", f"Не удалось сохранить: {exc}", parent=self)
            return False
        self.encoding = used
        self.text_value = self.content()
        self.major = script_version(self.text_value) or self.major
        self.say("Сохранено ✓")
        self.show_conflicts(saved=True)
        if self.on_saved:
            try:
                self.on_saved(self.path)
            except Exception:
                log.exception("on_saved failed")
        return True

    def check(self) -> bool | None:
        manager = self._manager()
        if manager is None:
            self.say("Проверить нельзя: AutoHotkey не найден")
            return None
        if self.dirty and not self.save():
            return None
        ok, message, line = manager.check(self.path)
        if ok is False:
            self.show_error(line, message)
        else:
            self.say(("✓ " if ok else "") + message)
        return ok

    def save_and_reload(self) -> None:
        if not self.save():
            return
        manager = self._manager()
        if manager is None:
            return
        if self.check() is False:
            return  # a broken script would stop with an error box: fix it first
        error = manager.reload(self.path)
        self.say("⚠ " + error if error else "Сохранено и перезапущено ✓")

    def edit_elsewhere(self) -> None:
        if self.dirty and not self.save():
            return
        manager = self._manager()
        try:
            if manager is None:
                raise OSError("нет менеджера скриптов")
            manager.system.edit_elsewhere(self.path)
        except OSError as exc:
            messagebox.showerror("Switcher", f"Не удалось открыть: {exc}", parent=self)

    def ask_claude(self) -> None:
        assistant = getattr(self.app, "assistant", None)
        if assistant is None or not self._ai_ready():
            self.say("Claude не подключён: вставьте ключ в «Настройки» → «Claude (ИИ)»")
            return
        dialog = ctk.CTkInputDialog(title="Claude и AutoHotkey", text=(
            "Что сделать со скриптом? Например: «Ctrl+Alt+D — вставить сегодняшнюю дату» "
            "или «почини ошибку»"))
        _set_icon(dialog)
        request = (dialog.get_input() or "").strip()
        if not request:
            return
        script, major = self.content(), self.major
        self.buttons["Claude…"].configure(state="disabled")
        self.say("Claude пишет скрипт…", keep=3600)

        def work():
            try:
                result = assistant.write_ahk(request, script, major)
            except Exception as exc:
                result = exc
            self._claude_result = (script, result)  # picked up on the Tk thread by _tick

        threading.Thread(target=work, name="switcher-ahk-claude", daemon=True).start()

    def _claude_done(self, before: str, result) -> None:
        if not self.winfo_exists():
            return
        self.buttons["Claude…"].configure(state="normal")
        if isinstance(result, Exception):
            self.say(f"⚠ Claude не смог: {result}")
            return
        new, summary = result
        if self.content() != before:
            self.say("Скрипт изменился, пока Claude думал, — попросите ещё раз")
            return
        self.text.edit_separator()
        self.text.delete("1.0", "end")
        self.text.insert("1.0", new)
        self.text.edit_separator()
        self._highlight()
        self.say(f"Claude: {summary}  Проверьте и сохраните (Ctrl+Z — вернуть как было).", keep=60)

    def close(self) -> None:
        if self.dirty:
            answer = messagebox.askyesnocancel("Switcher", f"Сохранить изменения в «{name_of(self.path)}»?",
                                               parent=self)
            if answer is None or (answer and not self.save()):
                return
        ctk.AppearanceModeTracker.remove(self._colours)
        self.destroy()


def open_editor(master, app, path: str | Path, on_saved=None) -> ScriptEditor:
    return ScriptEditor(master, app, str(path), on_saved)
