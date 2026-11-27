"""The dialog for one button of a CorelDRAW docker: page size, guideline, colour and caption."""

from __future__ import annotations

import dataclasses
import tkinter as tk
from collections.abc import Callable

import customtkinter as ctk

from .coreldocker import (
    CHECKS,
    COLUMNS,
    DEFAULT_COLOR,
    FONTS,
    HEIGHTS,
    Look,
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
HEAVY_NOTE = ("Если CorelDRAW подвисает, когда открываете или закрываете докер или правите документ, выключите "
              "оба пункта: кнопки размеров работают и без них.")
CHECKS_NOTE = ("Проверка идёт по частям, и CorelDRAW между ними отвечает, но в большом документе она долгая. Чем "
               "меньше проверок отмечено, тем она быстрее. Если CorelDRAW всё равно подвисает, снимите две нижние "
               "галочки: тогда проверка будет только по кнопке «Проверить сейчас». Кнопки размеров работают и без "
               "проверки.")
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


class LookDialog(ctk.CTkToplevel):
    """How big the docker draws its buttons: how many in a row, their height and the caption's size,
    with a sample of a few buttons drawn that way; and its document check, when ``options`` (as
    ``Docker.options()`` gives them) are passed.  ``on_done(look)`` — or ``on_done(look, options)`` —
    returns an error, or None."""

    SAMPLE_WIDTH = 300  # px: about as wide as the docker usually is

    def __init__(self, master, on_done: Callable[..., str | None], look: Look, samples: list[Preset],
                 options: dict | None = None):
        super().__init__(master)
        _set_icon(self)
        self.title("Вид и проверки докера — Switcher")
        checks = (options or {}).get("checks")
        self.geometry("560x" + ("600" if options is None else "700" if checks is None else "770"))
        self.resizable(False, False)
        self.on_done = on_done
        self.samples = samples[:8] or [Preset("510×+32", subtitle="→26mm")]
        body = ctk.CTkFont(size=13)
        bold = ctk.CTkFont(size=13, weight="bold")
        small = ctk.CTkFont(size=12)
        self.vars: dict[str, tk.IntVar] = {}
        self.values: dict[str, ctk.CTkLabel] = {}
        for key, title, (low, high), value, unit in (
                ("columns", "Кнопок в ряду", COLUMNS, look.columns, ""),
                ("height", "Высота кнопки", HEIGHTS, look.height, " точек"),
                ("font", "Размер надписи", FONTS, look.font, " точек")):
            row = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
            row.pack(fill="x", padx=18, pady=(14, 0))
            ctk.CTkLabel(row, text=title, font=bold, anchor="w", width=150).pack(side="left")
            self.values[key] = ctk.CTkLabel(row, text="", font=body, width=80, anchor="e")
            self.values[key].pack(side="right")
            self.vars[key] = tk.IntVar(value=value)
            ctk.CTkSlider(row, from_=low, to=high, number_of_steps=high - low, variable=self.vars[key],
                          command=lambda _v, k=key, u=unit: self._changed(k, u)).pack(
                side="left", fill="x", expand=True, padx=(8, 8))
            self._changed(key, unit, draw=False)
        self.switches = options is not None
        options = options or {}
        self.audit = tk.BooleanVar(value=bool(options.get("audit")))
        self.live = tk.BooleanVar(value=bool(options.get("live")))
        self.on_open = tk.BooleanVar(value=options.get("on_open", True) is not False)
        self.checks = {key: tk.BooleanVar(value=bool(on)) for key, on in (checks or {}).items()}
        self.parts: list[ctk.CTkCheckBox] = []  # what the main switch turns off with it
        if self.switches:  # the heavy part of the page: CorelDRAW 27 hung for minutes on it
            ctk.CTkCheckBox(self, text="Проверка документа" + ("" if checks is not None else
                                                               " (PANTONE, шрифты, RGB, OLE, эффекты)"),
                            variable=self.audit, font=bold, command=self._audit_switched).pack(
                fill="x", padx=18, pady=(18, 0))
            if checks is not None:
                grid = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
                grid.pack(fill="x", padx=(46, 18), pady=(8, 0))
                for index, (key, label) in enumerate(CHECKS):
                    box = ctk.CTkCheckBox(grid, text=label, variable=self.checks[key], font=body, width=150)
                    box.grid(row=index // 3, column=index % 3, sticky="w", pady=(0, 6))
                    self.parts.append(box)
                self.parts.append(ctk.CTkCheckBox(self, text="Проверять сразу, как докер открылся",
                                                  variable=self.on_open, font=body))
                self.parts[-1].pack(fill="x", padx=(46, 18), pady=(4, 0))
            self.parts.append(ctk.CTkCheckBox(self, text="Обновлять проверку при каждом изменении в документе",
                                              variable=self.live, font=body))
            self.parts[-1].pack(fill="x", padx=(46 if checks is not None else 18, 18), pady=(8, 0))
            ctk.CTkLabel(self, text=CHECKS_NOTE if checks is not None else HEAVY_NOTE, font=small, text_color=MUTED,
                         anchor="w", justify="left", wraplength=520).pack(fill="x", padx=18, pady=(6, 0))
            self._audit_switched()
        ctk.CTkLabel(self, text="Так кнопки будут выглядеть в докере (шириной около 300 точек):", font=small,
                     text_color=MUTED, anchor="w").pack(fill="x", padx=18, pady=(16, 4))
        self.sample = tk.Canvas(self, width=self.SAMPLE_WIDTH, height=200, highlightthickness=0, bg="#FFFFFF")
        self.sample.pack(padx=18, anchor="w")
        self.status = ctk.CTkLabel(self, text="В CorelDRAW новый вид появится, когда панель докера закрыть и открыть "
                                              "снова.", font=small, text_color=MUTED, anchor="w", justify="left",
                                   wraplength=520)
        self.status.pack(fill="x", padx=18, pady=(10, 0))
        buttons = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        buttons.pack(side="bottom", fill="x", padx=18, pady=16)
        ctk.CTkButton(buttons, text="Отмена", width=110, height=34, font=body, fg_color=NEUTRAL,
                      hover_color=NEUTRAL_HOVER, text_color=TEXT, border_width=1, border_color=CARD_BORDER,
                      command=self.destroy).pack(side="right")
        ctk.CTkButton(buttons, text="Готово", width=130, height=34, font=body, command=self.done).pack(
            side="right", padx=(0, 8))
        ctk.CTkButton(buttons, text="Как было в докере", width=150, height=34, font=body, fg_color=NEUTRAL,
                      hover_color=NEUTRAL_HOVER, text_color=TEXT, border_width=1, border_color=CARD_BORDER,
                      command=self.usual).pack(side="left")
        self.bind("<Escape>", lambda _e: self.destroy(), add="+")
        self.draw()
        self.after(50, self.lift)

    def look(self) -> Look:
        return Look(*(int(self.vars[key].get()) for key in ("columns", "height", "font")))

    def options(self) -> dict:
        """The document check as set here, the way ``Docker.save_look`` takes it."""
        return {"audit": self.audit.get(), "live": self.live.get(),
                "on_open": self.on_open.get() if self.checks else None,
                "checks": {key: value.get() for key, value in self.checks.items()} if self.checks else None}

    def _audit_switched(self) -> None:
        state = "normal" if self.audit.get() else "disabled"
        for box in self.parts:
            box.configure(state=state)

    def _changed(self, key: str, unit: str, draw: bool = True) -> None:
        self.values[key].configure(text=f"{int(self.vars[key].get())}{unit}")
        if draw:
            self.draw()

    def usual(self) -> None:
        """Back to the docker's own look: four in a row, 42 high, captions of 10."""
        for key, value in (("columns", 4), ("height", 42), ("font", 10)):
            self.vars[key].set(value)
        for key, unit in (("columns", ""), ("height", " точек"), ("font", " точек")):
            self._changed(key, unit, draw=False)
        self.draw()

    def draw(self) -> None:
        """A few of the docker's buttons as the docker will draw them (sizes as in the panel, 1:1)."""
        look = self.look()
        canvas = self.sample
        canvas.delete("all")
        gap, width = 4, self.SAMPLE_WIDTH / look.columns
        rows = -(-len(self.samples) // look.columns)
        canvas.configure(height=max(60, min(260, rows * (look.height + gap) + gap)))
        title_font = ("Segoe UI", -look.font, "bold")
        subtitle_font = ("Segoe UI", -look.subtitle_font)
        for index, preset in enumerate(self.samples):
            row, column = divmod(index, look.columns)
            x0, y0 = column * width + 2, row * (look.height + gap) + gap
            x1, y1 = x0 + width - gap, y0 + look.height
            if y1 > 260:
                break
            color = normal_color(preset.color) or DEFAULT_COLOR
            canvas.create_rectangle(x0, y0, x1, y1, fill=color, outline=color)
            middle = (x0 + x1) / 2
            if preset.subtitle:
                canvas.create_text(middle, (y0 + y1) / 2 - look.font * 0.55, text=preset.title, fill="#FFFFFF",
                                   font=title_font, width=max(10, x1 - x0 - 4))
                canvas.create_text(middle, (y0 + y1) / 2 + look.font * 0.65, text=preset.subtitle, fill="#FFFFFF",
                                   font=subtitle_font, width=max(10, x1 - x0 - 4))
            else:
                canvas.create_text(middle, (y0 + y1) / 2, text=preset.title, fill="#FFFFFF", font=title_font,
                                   width=max(10, x1 - x0 - 4))

    def done(self) -> None:
        look = self.look()
        error = look.problem() or (self.on_done(look, self.options()) if self.switches else self.on_done(look))
        if error:
            self.status.configure(text=error)
            return
        self.destroy()
