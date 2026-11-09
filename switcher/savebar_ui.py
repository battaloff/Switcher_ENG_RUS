"""The «Сохранить как» panel on screen: a strip of buttons that follows the file-save dialog in front.

It lives on the Tk thread and looks at the foreground window a few times a second.  The work of a
button (copy the name, create "PDF", open it) runs on a thread of its own, so the panel never
waits on a slow network folder.  ``system`` is the Windows side (platform/win_savebar.py); tests
give a fake one.
"""

from __future__ import annotations

import logging
import threading
import time
import tkinter as tk

import customtkinter as ctk

from .savebar import (
    NEW_FOLDER,
    Button,
    Geometry,
    darker,
    hotkey_map,
    place_bar,
    plan,
    shown_buttons,
    text_on,
)

log = logging.getLogger(__name__)

MAIN = ("#FFC83D", "#FFC83D")          # the main button: yellow, as in the dialog's own accent
MAIN_HOVER = ("#F5B700", "#F5B700")
MAIN_TEXT = ("#1B1B1B", "#1B1B1B")
OTHER = ("#FBFBFB", "#2D2D2D")
OTHER_HOVER = ("#F0F0F0", "#3A3A3A")
OTHER_BORDER = ("#D1D1D1", "#474747")
OTHER_TEXT = ("#1B1B1B", "#F2F2F2")
# the panel's own background: a colour Windows makes see-through (between and around the buttons the
# dialog itself shows), close to the dialog's row so that the buttons' rounded corners blend in
SEE_THROUGH = ("#F7F7F8", "#1B1B1C")


def button_look(button: Button, main: bool, dark: bool) -> dict:
    """The colours of a panel button: its own colour if it has one, else yellow for the main one
    and the dialog's grey for the others."""
    if button.color:
        return {"fg_color": button.color, "hover_color": darker(button.color), "text_color": text_on(button.color),
                "border_width": 0, "border_color": button.color}
    if main:
        return {"fg_color": MAIN[dark], "hover_color": MAIN_HOVER[dark], "text_color": MAIN_TEXT[dark],
                "border_width": 0, "border_color": MAIN[dark]}
    return {"fg_color": OTHER[dark], "hover_color": OTHER_HOVER[dark], "text_color": OTHER_TEXT[dark],
            "border_width": 1, "border_color": OTHER_BORDER[dark]}


class SaveBarPanel:
    POLL_MS = 60          # how often it looks at the window in front
    RECHECK_EVERY = 0.3   # a dialog that is not (yet) a save dialog: look again this often
    RECHECK_FOR = 8.0     # … for this long: a slow dialog gets its file browser late
    LAYOUT_EVERY = 0.4    # re-measure the dialog's rows this often (and whenever it moves)
    SETTLE = 0.3          # a dialog that just came to the front is still being drawn: wait this long

    def __init__(self, root, app, system=None):
        if system is None:
            from .platform.win_savebar import WindowsSaveDialogs

            system = WindowsSaveDialogs()
        self.root, self.app, self.system = root, app, system
        self.window: tk.Toplevel | None = None
        self.hwnd = 0                     # the panel's own window
        self.dialog = 0                   # the dialog the panel is shown on (0: hidden)
        self.where = ""                   # "inside" or "above"
        self.buttons: list[Button] = []
        self.size = (0, 0)
        self.position: tuple[int, int] | None = None
        self._built: tuple | None = None
        self._checked: dict[int, tuple[bool, float, float]] = {}  # hwnd → (save dialog, first seen, last look)
        self._layout: tuple[int, tuple, float, Geometry] | None = None
        self.gap_points: list[int] = []   # the middle of each gap between buttons, from the panel's left
        self._see_through = ""
        self._work = threading.Lock()
        self.tip: tk.Toplevel | None = None
        self.tip_hwnd = 0
        self._failed_at = 0.0
        self._front = (0, 0.0)  # the window in front and since when

    # -- running ---------------------------------------------------------------------------------

    def start(self) -> None:
        backend = self.app.backend
        backend.save_bar_press = self.press_hotkey  # called by the keyboard hook
        self._create_window()  # now, not when a dialog opens: showing a window may disturb the focus
        self.root.after(self.POLL_MS, self._tick)

    def _tick(self) -> None:
        try:
            self.update()
        except Exception:
            if time.monotonic() - self._failed_at > 60:  # not every 60 ms
                self._failed_at = time.monotonic()
                log.exception("save bar failed")
            self.hide()
        if not self.app.stop_event.is_set():
            self.root.after(self.POLL_MS, self._tick)

    def update(self) -> None:
        config = self.app.config.save_bar
        buttons = shown_buttons(config) if config.enabled else []
        front = self.system.foreground()
        if front and front in (self.hwnd, self.tip_hwnd):
            return  # the panel itself got the focus for a moment: keep things as they are
        if front != self._front[0]:
            self._front = (front, time.monotonic())
        if not buttons or not front or not self._is_save_dialog(front):
            self.hide()
            return
        if self.dialog != front and time.monotonic() - self._front[1] < self.SETTLE:
            return
        geometry = self._geometry(front)
        if geometry is None:
            self.hide()
            return
        self._build(buttons, geometry, front)
        after = None if geometry.after_x is None else geometry.after_x + round(max(0, config.gap) * geometry.scale)
        x, y, self.where = place_bar(geometry.dialog, geometry.row, after, self.size, config.place, geometry.scale)
        self._show(front, x, y)

    def _is_save_dialog(self, hwnd: int) -> bool:
        now = time.monotonic()
        known = self._checked.get(hwnd)
        if known is not None:
            found, first, last = known
            if found or now - first > self.RECHECK_FOR or now - last < self.RECHECK_EVERY:
                return found
        found = self.system.is_save_dialog(hwnd)
        self._checked[hwnd] = (found, known[1] if known else now, now)
        if len(self._checked) > 60:  # forget windows long gone
            self._checked = {h: v for h, v in self._checked.items() if self.system.alive(h)}
        return found

    def _geometry(self, hwnd: int) -> Geometry | None:
        """The dialog's rows, measured again when it moves or resizes (the panel follows at once)
        and now and then (its panes may change)."""
        now = time.monotonic()
        layout = self._layout
        if layout is not None and layout[0] == hwnd and now - layout[2] < self.LAYOUT_EVERY \
                and self.system.dialog_rect(hwnd) == layout[1]:
            return layout[3]
        geometry = self.system.geometry(hwnd, measure=self.position is None)
        if geometry is None:
            self._layout = None
            return None
        self._layout = (hwnd, geometry.dialog, now, geometry)
        return geometry

    # -- the window ------------------------------------------------------------------------------

    def _build(self, buttons: list[Button], geometry: Geometry, dialog: int) -> None:
        dark = ctk.get_appearance_mode() == "Dark"
        row_height = geometry.row[3] - geometry.row[1] if geometry.row else round(34 * geometry.scale)
        height = max(22, row_height - round(8 * geometry.scale))
        background = SEE_THROUGH[dark]
        key = (tuple(buttons), dark, height, background)
        if key == self._built and self.window is not None:
            return
        if self.window is not None and (self._built is not None or self._see_through != background):
            # new buttons (edited in the settings) or another theme: a fresh window, made the way the
            # first one was
            self.system.hide(self.hwnd)
            self.window.destroy()
            self.window, self.hwnd, self.position = None, 0, None
            log.info("save bar: buttons changed: %s", ", ".join(b.label for b in buttons))
        self._create_window(background)
        try:  # how much CustomTkinter enlarges its widgets on this screen (sizes here are real pixels)
            scaling = ctk.ScalingTracker.get_window_dpi_scaling(self.window) * ctk.ScalingTracker.widget_scaling
        except (KeyError, AttributeError, tk.TclError):
            scaling = 1.0
        scaling = scaling or 1.0
        font = ctk.CTkFont(family="Segoe UI", size=12)
        pad = round(6 * geometry.scale)  # between buttons, in real pixels like the window's
        for index, button in enumerate(buttons):
            main = index == 0
            look = button_look(button, main, dark)
            widget = ctk.CTkButton(
                self.window, text=button.label, width=round((font.measure(button.label) + 22 * scaling) / scaling),
                height=round(height / scaling), corner_radius=4, font=font, bg_color=background,
                command=lambda i=index: self.press(i), **look)
            widget.pack(side="left", padx=(0 if main else pad, 0), pady=0)
            widget.bind("<Enter>", lambda _e, i=index: self._show_tip(i), add="+")
            widget.bind("<Leave>", lambda _e: self._hide_tip(), add="+")
        self.window.update_idletasks()
        self.size = (self.window.winfo_reqwidth(), self.window.winfo_reqheight())
        self.window.geometry(f"{self.size[0]}x{self.size[1]}+-32000+-32000")
        self.window.update_idletasks()
        # where the gaps are, from the buttons' own widths (Tk tells no positions in a hidden window)
        self.gap_points, x = [], 0
        for index, widget in enumerate(self.window.winfo_children()):
            if index:
                self.gap_points.append(x + pad // 2)
                x += pad
            x += widget.winfo_reqwidth()
        self.buttons = list(buttons)
        self._built = key
        self.position = None

    def _create_window(self, background: str | None = None) -> None:
        if self.window is not None:
            return
        background = background or SEE_THROUGH[ctk.get_appearance_mode() == "Dark"]
        self.window = self._popup("Switcher — панель «Сохранить как»", background)
        self._see_through = background
        self.hwnd = self._popup_ready(self.window)

    def _popup(self, title: str, see_through: str | None = None) -> tk.Toplevel:
        window = tk.Toplevel(self.root)
        window.withdraw()
        window.overrideredirect(True)
        window.title(title)
        window.geometry("1x1+-32000+-32000")
        if see_through:
            window.configure(background=see_through)
            try:  # Windows: this colour is not drawn at all — the dialog shows through. Set before the
                # window gets its other styles (never the focus, on top), which Tk might redo otherwise
                window.attributes("-transparentcolor", see_through)
            except tk.TclError:
                pass  # not on Windows (the tests)
        return window

    def _popup_ready(self, window: tk.Toplevel) -> int:
        """Tk must think the window is shown, or it draws nothing in it: show it off the screen once,
        make it a window that never takes the focus, then hide and show it with Windows' own calls."""
        front = self.system.foreground()
        window.deiconify()
        window.update_idletasks()
        hwnd = self.system.prepare(window)
        self.system.hide(hwnd)
        if front and self.system.foreground() == hwnd:
            self.system.focus(front)
        return hwnd

    def _show(self, dialog: int, x: int, y: int) -> None:
        self._arm_hotkeys(dialog)
        if self.dialog != dialog:
            log.info("save bar: on the save dialog %#x, %s, at %d,%d", dialog, self.where, x, y)
        self.dialog = dialog
        if self.position != (x, y) or not self.system.visible(self.hwnd):
            self.window.geometry(f"{self.size[0]}x{self.size[1]}+{x}+{y}")
            self.system.show(self.hwnd, x, y)
            self.position = (x, y)

    def hide(self) -> None:
        if self.dialog:
            self.dialog = 0
            self.app.backend.save_bar_dialog = 0
            self._hide_tip()
        if self.hwnd and self.position is not None:
            self.system.hide(self.hwnd)
            self.position = None

    def _arm_hotkeys(self, dialog: int) -> None:
        backend = self.app.backend
        backend.save_bar_keys = hotkey_map(self.buttons)
        backend.save_bar_dialog = dialog

    # -- tooltips --------------------------------------------------------------------------------

    def _show_tip(self, index: int) -> None:
        if not self.app.config.save_bar.tooltips or index >= len(self.buttons) or not self.dialog:
            return
        button = self.buttons[index]
        if button.action == NEW_FOLDER:
            text = "Создаст новую папку здесь — останется ввести её имя"
        else:
            found = self.system.folder(self.dialog)
            what = button.text(plan(found[0], found[1], False).name) if found else None
            text = f"Скопирует: {what}" if what else "Папка не определилась"
        if self.tip is None:
            self.tip = self._popup("Switcher — подсказка")
            self.tip_label = tk.Label(self.tip, justify="left", padx=8, pady=4, borderwidth=1, relief="solid",
                                      font=("Segoe UI", 9))
            self.tip_label.pack()
            self.tip_hwnd = self._popup_ready(self.tip)
        dark = ctk.get_appearance_mode() == "Dark"
        self.tip_label.configure(text=text, background="#2B2B2B" if dark else "#FFFFFF",
                                 foreground="#F2F2F2" if dark else "#1B1B1B")
        self.tip.update_idletasks()
        x = self.root.winfo_pointerx() + 12
        y = (self.position[1] if self.position else self.root.winfo_pointery()) + self.size[1] + 4
        self.tip.geometry(f"+{x}+{y}")
        self.system.show(self.tip_hwnd, x, y)

    def _hide_tip(self) -> None:
        if self.tip_hwnd:
            self.system.hide(self.tip_hwnd)

    # -- what a button does ------------------------------------------------------------------------

    def press(self, index: int) -> None:
        """A click on the panel (Tk thread)."""
        self._start(index, self.dialog)

    def press_hotkey(self, index: int) -> None:
        """A hotkey from the keyboard hook: never do the work in the hook itself."""
        self._start(index, self.app.backend.save_bar_dialog)

    def _start(self, index: int, dialog: int) -> None:
        buttons = self.buttons
        if not dialog or not 0 <= index < len(buttons):
            return
        threading.Thread(target=self.run, args=(buttons[index], dialog), name="switcher-save-bar",
                         daemon=True).start()

    def run(self, button: Button, dialog: int) -> str | None:
        """Copy what the button makes of the dialog's folder; then make "PDF" there and open it.
        Returns what was copied (None: nothing)."""
        config = self.app.config.save_bar
        notify = self.app.backend.notify
        try:
            if button.action == NEW_FOLDER:
                return self._new_folder(dialog)
            found = self.system.folder(dialog)
            if found is None:
                notify("Не понял, какая папка открыта в окне сохранения: ничего не скопировано.")
                return None
            what = plan(found[0], found[1], config.make_pdf_folder)
            text = button.text(what.name)
            self.system.copy(text)
            log.info("save bar: %s → copied %r%s", button.label, text, f", then into {what.go_to}" if what.go_to else "")
            if what.go_to and self._work.acquire(blocking=False):
                try:
                    try:
                        self.system.make_folder(what.go_to)
                    except PermissionError:
                        notify(f"Нет прав создать папку «PDF» в «{what.name}». Имя скопировано: {text}")
                        return text
                    except OSError as exc:
                        notify(f"Не удалось создать папку «PDF» в «{what.name}» ({exc.strerror or exc}). "
                               f"Имя скопировано: {text}")
                        return text
                    if not self.system.go_into(dialog, what.go_to):
                        notify(f"Папка «PDF» есть, но окно сохранения в неё не перешло. Имя скопировано: {text}")
                        return text
                finally:
                    self._work.release()
            self.system.caret_to_start(dialog)  # the name is typed or pasted in front of the file name
            if config.notifications:
                notify(f"Скопировано: {text}")
            return text
        except Exception as exc:
            log.exception("save bar button failed")
            notify(f"Панель «Сохранить как»: ошибка — {exc}")
            return None


    def _new_folder(self, dialog: int) -> str | None:
        """The dialog's own «Новая папка»: a folder appears and its name is ready to be typed.
        Returns the new folder's name ("" when it can't be seen: not a folder on a disk)."""
        notify = self.app.backend.notify
        found = self.system.folder(dialog)
        before = self.system.subfolders(found[0]) if found else set()
        if not self.system.new_folder(dialog):
            notify("Не получилось создать новую папку в окне сохранения.")
            return None
        if not found:
            return ""
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            made = self.system.subfolders(found[0]) - before
            if made:
                log.info("save bar: new folder %s in %s", sorted(made), found[0])
                return min(made)
            time.sleep(0.15)
        notify(f"В «{found[1]}» новая папка не появилась: возможно, нет прав.")
        return None


def start_save_bar(root, app) -> SaveBarPanel | None:
    """The panel for the desktop app (Windows only)."""
    import sys

    if sys.platform != "win32":
        return None
    try:
        panel = SaveBarPanel(root, app)
        panel.start()
        return panel
    except Exception:
        log.exception("could not start the save bar")
        return None
