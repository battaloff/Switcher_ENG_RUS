"""The dialog for one button of a CorelDRAW docker: page size, guideline, colour and caption."""

from __future__ import annotations

import dataclasses
import tkinter as tk
from collections.abc import Callable

import customtkinter as ctk

from .coreldocker import (
    DEFAULT_COLOR,
    Preset,
    _number,
    auto_subtitle,
    auto_title,
    problem,
)
from .gui import CARD_BORDER, MUTED, NEUTRAL, NEUTRAL_HOVER, TEXT, _set_icon
from .savebar import darker, normal_color

ADD_TITLE, EXACT_TITLE = "Прибавить к текущей", "Задать ровно"
SWATCHES = 16  # how many colours fit in the row
WHAT_IT_DOES = ("По нажатию кнопка ставит эту ширину всем страницам документа, меняет их высоту, убирает старые "
                "направляющие и ставит новую горизонтальную.")
OTHER_KIND = "У этой кнопки другое действие: здесь меняются только её группа, цвет и надписи."
INSTALL_NOTE = ("Докер встанет отдельным докером: в свою папку и со своими ключами (GUID), поэтому не помешает "
                "другим докерам и макросам на этом компьютере, даже с тем же названием.")
MACROS_NOTE = ("Макросы VBA с ним не ставятся (у них общее имя проекта, оно мешало бы другим макросам): все кнопки "
               "работают кодом самой панели докера — так же, как изменённые в Switcher кнопки. После установки "
               "перезапустите CorelDRAW.")
BAD_NUMBER = object()


def entered(text: str, empty=0):
    """The number typed in a field ("37,5"); ``empty`` for nothing, BAD_NUMBER for anything else."""
    text = text.strip().replace(",", ".").replace(" ", "")
    if not text:
        return empty
    try:
        float(text)
    except ValueError:
        return BAD_NUMBER
    return _number(text)


class PresetDialog(ctk.CTkToplevel):
    """``on_done(preset)`` returns an error to show, or None when the button is taken."""

    def __init__(self, master, on_done: Callable[[Preset], str | None], preset: Preset | None = None,
                 groups: list[str] | None = None, colors: list[str] | None = None, group: str = "",
                 color: str = ""):
        super().__init__(master)
        _set_icon(self)
        self.title(("Кнопка «" + preset.title + "»" if preset else "Новая кнопка докера") + " — Switcher")
        self.geometry("580x640")
        self.resizable(False, False)
        self.on_done = on_done
        self.base = preset or Preset("", group=group, color=normal_color(color) or DEFAULT_COLOR)
        self.color = self.base.color  # (a colour written in another way, "#abc", stays until another is picked)
        body = ctk.CTkFont(size=13)
        bold = ctk.CTkFont(size=13, weight="bold")
        small = ctk.CTkFont(size=12)
        pad = {"padx": 18, "fill": "x"}
        sets_page = self.base.sets_page

        def caption(text: str, top: int = 12) -> None:
            ctk.CTkLabel(self, text=text, font=bold, anchor="w").pack(pady=(top, 4), **pad)

        def entry(parent, value: str, hint: str = "", width: int = 0) -> ctk.CTkEntry:
            box = ctk.CTkEntry(parent, height=34, font=body, placeholder_text=hint, **({"width": width} if width else {}))
            if value:
                box.insert(0, value)
            box.bind("<KeyRelease>", lambda _e: self._numbers_changed(), add="+")
            return box

        def shown(value) -> str:
            return str(_number(value)).replace(".", ",") if _number(value) else ""

        caption("Группа (заголовок, под которым стоит кнопка)", top=14)
        self.group = ctk.CTkComboBox(self, values=list(groups or []), height=34, font=body, dropdown_font=body)
        self.group.set(self.base.group)
        self.group.pack(**pad)

        caption("Ширина страницы, мм")
        self.width = entry(self, shown(self.base.width), "Например: 510")
        self.width.pack(**pad)

        caption("Высота страницы, мм")
        row = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        row.pack(**pad)
        self.mode = ctk.CTkSegmentedButton(row, values=[ADD_TITLE, EXACT_TITLE], font=small,
                                           command=lambda _v: self._numbers_changed())
        self.mode.set(EXACT_TITLE if self.base.absolute else ADD_TITLE)
        self.mode.pack(side="left")
        self.height = entry(row, shown(self.base.height) or ("0" if preset and not preset.absolute else ""),
                            "Например: 32", width=150)
        self.height.pack(side="left", padx=(10, 0))

        caption("Направляющая: сколько мм от нижнего края")
        self.guideline = entry(self, shown(self.base.guideline), "Пусто — без направляющей")
        self.guideline.pack(**pad)
        if not sets_page:  # a button of another kind (a macro, a built-in command): only its look is edited
            for box in (self.width, self.height, self.guideline):
                box.configure(state="disabled")
            self.mode.configure(state="disabled")

        caption("Цвет кнопки")
        swatches = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        swatches.pack(**pad)
        self.swatches: dict[str, ctk.CTkButton] = {}
        # the colours the docker already uses, in its order; the button's own one is always among them
        listed = list(dict.fromkeys(c for c in map(normal_color, colors or []) if c))[:SWATCHES]
        if self.color not in listed and normal_color(self.color):
            listed = [*listed[:SWATCHES - 1], self.color]
        for value in listed:
            swatch = ctk.CTkButton(swatches, text="", width=24, height=24, corner_radius=12, fg_color=value,
                                   hover_color=darker(value), border_width=0, border_color=TEXT,
                                   command=lambda v=value: self.pick(v))
            swatch.pack(side="left", padx=(0, 4))
            self.swatches[value] = swatch
        ctk.CTkButton(swatches, text="Другой…", width=76, height=28, font=small, fg_color=NEUTRAL,
                      hover_color=NEUTRAL_HOVER, text_color=TEXT, border_width=1, border_color=CARD_BORDER,
                      command=self.pick_other).pack(side="left", padx=(3, 0))

        caption("Надпись и строка под ней (заполняются сами, но можно написать свои)")
        row = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        row.pack(**pad)
        self._auto = (auto_title(self.base.width, self.base.height, self.base.absolute),
                      auto_subtitle(self.base.absolute, self.base.guideline)) if sets_page else ("", "")
        self.label = ctk.CTkEntry(row, height=34, font=body, placeholder_text="Надпись")
        self.label.insert(0, self.base.title if preset else "")
        self.label.pack(side="left", fill="x", expand=True)
        self.subtitle = ctk.CTkEntry(row, height=34, font=body, placeholder_text="Строка под ней", width=190)
        self.subtitle.insert(0, self.base.subtitle)
        self.subtitle.pack(side="left", padx=(10, 0))
        for box in (self.label, self.subtitle):
            box.bind("<KeyRelease>", lambda _e: self._show_sample(), add="+")
        if not preset:
            self._auto = ("", "")  # a new button: the caption follows the numbers from the first one typed

        self.sample = ctk.CTkButton(self, text="", height=46, width=120, corner_radius=6, hover=False,
                                    font=ctk.CTkFont(size=12, weight="bold"), text_color="#FFFFFF",
                                    command=lambda: None)
        self.sample.pack(padx=18, pady=(12, 0), anchor="w")
        self.status = ctk.CTkLabel(self, text=WHAT_IT_DOES if sets_page else OTHER_KIND, font=small,
                                   text_color=MUTED, anchor="w", justify="left", wraplength=540)
        self.status.pack(pady=(8, 0), **pad)
        buttons = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        buttons.pack(side="bottom", fill="x", padx=18, pady=16)
        ctk.CTkButton(buttons, text="Отмена", width=110, height=34, font=body, fg_color=NEUTRAL,
                      hover_color=NEUTRAL_HOVER, text_color=TEXT, border_width=1, border_color=CARD_BORDER,
                      command=self.destroy).pack(side="right")
        ctk.CTkButton(buttons, text="Готово", width=130, height=34, font=body, command=self.done).pack(
            side="right", padx=(0, 8))
        self.bind("<Return>", lambda _e: self.done(), add="+")
        self.bind("<Escape>", lambda _e: self.destroy(), add="+")
        self._numbers_changed()
        self.after(50, self.lift)
        self.after(80, (self.width if sets_page else self.label).focus_set)

    def _numbers(self) -> tuple:
        """(width, height, guideline, exact height) as typed; BAD_NUMBER where it is not a number."""
        return (entered(self.width.get()), entered(self.height.get()), entered(self.guideline.get()),
                self.mode.get() == EXACT_TITLE)

    def _numbers_changed(self) -> None:
        """The caption follows the numbers while it is the one made from them (not written by hand)."""
        width, height, guideline, absolute = self._numbers()
        if self.base.sets_page and BAD_NUMBER not in (width, height, guideline) and width:
            new = (auto_title(width, height, absolute), auto_subtitle(absolute, guideline))
            for box, old, text in ((self.label, self._auto[0], new[0]), (self.subtitle, self._auto[1], new[1])):
                if box.get().strip() == old:
                    box.delete(0, "end")
                    box.insert(0, text)
            self._auto = new
        self._show_sample()

    def _show_sample(self) -> None:
        """The button as the docker draws it, and a ring around the chosen colour."""
        title, subtitle = self.label.get().strip() or "Кнопка", self.subtitle.get().strip()
        try:
            self.sample.configure(text=title + ("\n" + subtitle if subtitle else ""), fg_color=self.color)
        except (tk.TclError, ValueError):  # a colour Tk does not know: the caption alone
            self.sample.configure(text=title + ("\n" + subtitle if subtitle else ""))
        for value, swatch in self.swatches.items():
            swatch.configure(border_width=3 if value == self.color else 0)

    def pick(self, color: str) -> None:
        self.color = normal_color(color) or self.color
        self._show_sample()

    def pick_other(self) -> None:
        from tkinter import colorchooser

        chosen = colorchooser.askcolor(color=self.color, parent=self, title="Цвет кнопки")
        if chosen and chosen[1]:
            self.pick(chosen[1])

    def done(self) -> None:
        width, height, guideline, absolute = self._numbers()
        for value, name in ((width, "Ширина"), (height, "Высота"), (guideline, "Направляющая")):
            if value is BAD_NUMBER:
                self.status.configure(text=f"{name}: напишите число, например 510 или 37,5.")
                return
        preset = dataclasses.replace(self.base, title=self.label.get().strip(), group=self.group.get().strip(),
                                     subtitle=self.subtitle.get().strip(), color=self.color)
        if self.base.sets_page:
            preset = dataclasses.replace(preset, width=width, height=height, guideline=guideline, absolute=absolute)
        error = problem(preset) or self.on_done(preset)
        if error:
            self.status.configure(text=error)
            return
        self.destroy()


class InstallDialog(ctk.CTkToplevel):
    """Install a docker from a file: its caption in CorelDRAW and which CorelDRAWs get it.
    ``on_done(caption, targets)`` returns an error to show, or None when the installation is started."""

    def __init__(self, master, on_done: Callable[[str, list], str | None], name: str, buttons: int,
                 targets: list, suggested: str):
        super().__init__(master)
        _set_icon(self)
        self.title("Установка докера — Switcher")
        self.geometry("560x" + str(420 + 50 * len(targets)))
        self.resizable(False, False)
        self.on_done = on_done
        self.targets = targets
        body = ctk.CTkFont(size=13)
        bold = ctk.CTkFont(size=13, weight="bold")
        small = ctk.CTkFont(size=12)
        pad = {"padx": 18, "fill": "x"}
        ctk.CTkLabel(self, text=f"Докер «{name}», кнопок: {buttons}", font=ctk.CTkFont(size=16, weight="bold"),
                     anchor="w").pack(pady=(16, 4), **pad)
        ctk.CTkLabel(self, text=INSTALL_NOTE, font=small, text_color=MUTED, anchor="w", justify="left",
                     wraplength=520).pack(**pad)
        ctk.CTkLabel(self, text="Название в CorelDRAW", font=bold, anchor="w").pack(pady=(14, 4), **pad)
        self.caption = ctk.CTkEntry(self, height=34, font=body)
        self.caption.insert(0, suggested)
        self.caption.pack(**pad)
        ctk.CTkLabel(self, text="Куда установить", font=bold, anchor="w").pack(pady=(14, 4), **pad)
        self.chosen = []
        for target in targets:
            var = tk.BooleanVar(value=True)
            ctk.CTkCheckBox(self, text=target.label, variable=var, font=body).pack(pady=(4, 0), **pad)
            ctk.CTkLabel(self, text=str(target.addons), font=small, text_color=MUTED, anchor="w", justify="left",
                         wraplength=490).pack(padx=(48, 18), fill="x")
            self.chosen.append(var)
        ctk.CTkLabel(self, text=MACROS_NOTE, font=small, text_color=MUTED, anchor="w", justify="left",
                     wraplength=520).pack(pady=(14, 0), **pad)
        self.status = ctk.CTkLabel(self, text="", font=small, text_color=MUTED, anchor="w", justify="left",
                                   wraplength=520)
        self.status.pack(pady=(8, 0), **pad)
        buttons_row = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        buttons_row.pack(side="bottom", fill="x", padx=18, pady=16)
        ctk.CTkButton(buttons_row, text="Отмена", width=110, height=34, font=body, fg_color=NEUTRAL,
                      hover_color=NEUTRAL_HOVER, text_color=TEXT, border_width=1, border_color=CARD_BORDER,
                      command=self.destroy).pack(side="right")
        ctk.CTkButton(buttons_row, text="Установить", width=130, height=34, font=body, command=self.done).pack(
            side="right", padx=(0, 8))
        self.bind("<Escape>", lambda _e: self.destroy(), add="+")
        self.after(50, self.lift)
        self.after(80, self.caption.focus_set)

    def done(self) -> None:
        caption = self.caption.get().strip()
        targets = [target for target, var in zip(self.targets, self.chosen, strict=True) if var.get()]
        if not caption:
            self.status.configure(text="Напишите, как назвать докер в CorelDRAW.")
            return
        if not targets:
            self.status.configure(text="Отметьте, в какой CorelDRAW установить докер.")
            return
        error = self.on_done(caption, targets)
        if error:
            self.status.configure(text=error)
            return
        self.destroy()
