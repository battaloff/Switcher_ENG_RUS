"""The «Сохранить как» panel: buttons in file-save dialogs that copy the name of the dialog's folder.

Plain logic only (tested on any system): which folder the dialog shows, what a button copies,
where the "PDF" folder goes and where the panel sits.  ``platform/win_savebar.py`` talks to
Windows and ``savebar_ui.py`` draws the panel.
"""

from __future__ import annotations

import collections
import ntpath
import re
from dataclasses import dataclass

from .controller import parse_hotkey

PDF = "PDF"
Rect = tuple[int, int, int, int]  # left, top, right, bottom (screen pixels)

# ready-made colours for the buttons: (name, colour); "" is the usual look (yellow main, grey others)
PALETTE = (
    ("Жёлтый", "#FFC83D"), ("Оранжевый", "#FF8C42"), ("Красный", "#E5484D"), ("Розовый", "#E93D82"),
    ("Фиолетовый", "#8E4EC6"), ("Синий", "#3E63DD"), ("Голубой", "#0EA5E9"), ("Зелёный", "#30A46C"),
    ("Серый", "#8B8D98"),
)
_HEX = re.compile(r"#[0-9a-fA-F]{6}")


def normal_color(value) -> str:
    """A colour as "#RRGGBB", or "" (the usual look) for anything else."""
    value = str(value or "").strip()
    return value.upper() if _HEX.fullmatch(value) else ""


def color_name(color: str) -> str:
    """What the settings list says: «Зелёный», «#12AB34», or «обычный»."""
    if not color:
        return "обычный"
    return next((name for name, value in PALETTE if value == color.upper()), color.upper())


def _rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def text_on(color: str) -> str:
    """Dark or white text, whichever reads better on ``color``."""
    def linear(channel: int) -> float:
        c = channel / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (linear(c) for c in _rgb(color))
    luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return "#1B1B1B" if luminance > 0.33 else "#FFFFFF"


def darker(color: str, by: float = 0.12) -> str:
    """The colour under the mouse: a little darker."""
    return "#" + "".join(f"{round(c * (1 - by)):02X}" for c in _rgb(color))


@dataclass(frozen=True)
class Button:
    label: str
    prefix: str = ""
    suffix: str = ""
    hotkey: str = ""
    enabled: bool = True
    color: str = ""  # "#RRGGBB"; "" — the usual look

    def text(self, folder_name: str) -> str:
        """What the button puts on the clipboard: «<prefix><folder><suffix>»."""
        return f"{self.prefix}{folder_name}{self.suffix}"

    def to_dict(self) -> dict:
        return {"label": self.label, "prefix": self.prefix, "suffix": self.suffix, "hotkey": self.hotkey,
                "enabled": self.enabled, "color": self.color}


def default_buttons() -> list[dict]:
    return [
        Button("Копировать имя папки", hotkey="<ctrl>+<shift>+1").to_dict(),
        Button("PANCH", suffix=" PANCH", hotkey="<ctrl>+<shift>+2").to_dict(),
        Button("PANCH + ЗАГИБ", suffix=" PANCH + ЗАГИБ", hotkey="<ctrl>+<shift>+3").to_dict(),
        Button("ОБЖИГ", suffix=" ОБЖИГ", hotkey="<ctrl>+<shift>+4").to_dict(),
    ]


def buttons_of(items) -> list[Button]:
    """The buttons from the settings; whatever a hand-edited config.json got wrong is skipped."""
    found = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not str(item.get("label", "")).strip():
            continue
        found.append(Button(str(item["label"]).strip(), str(item.get("prefix", "") or ""),
                            str(item.get("suffix", "") or ""), str(item.get("hotkey", "") or "").strip(),
                            bool(item.get("enabled", True)), normal_color(item.get("color"))))
    return found


def shown_buttons(config) -> list[Button]:
    """The buttons on the panel, in order; the first is the main one (yellow)."""
    return [b for b in buttons_of(config.buttons) if b.enabled]


# -- the dialog's folder ------------------------------------------------------------------------

_DRIVE = re.compile(r"\(([A-Za-z]):\)\s*$")


def folder_from_address(text: str, known: dict[str, str]) -> tuple[str, str] | None:
    """The folder a file dialog shows, from the text of its address bar: (path, name to copy).

    The bar says "Адрес: \\\\192.168.1.41\\f\\Заказ" for an ordinary folder, but only a name for
    some: "Адрес: Рабочий стол", "Адрес: Библиотеки\\Документы", "Адрес: Локальный диск (C:)".
    ``known`` maps such names (lower case) to their folders.  None: not a folder on a disk
    ("Этот компьютер", "Сеть").
    """
    value = text.strip()
    label, sep, rest = value.partition(": ")
    if sep and not re.match(r"^[A-Za-z]$", label):  # "Адрес: …", not the "C" of "C: …"
        value = rest.strip()
    if re.match(r"^[A-Za-z]:\\", value) or value.startswith("\\\\"):
        path = value if re.fullmatch(r"[A-Za-z]:\\", value) else value.rstrip("\\")
        return path, ntpath.basename(path) or path
    parts = [part.strip() for part in value.split("\\") if part.strip()]
    for start, part in enumerate(parts):  # "Библиотеки\Документы\Работа", "Локальный диск (C:)\Работа"
        drive = _DRIVE.search(part)
        root = f"{drive.group(1).upper()}:\\" if drive else known.get(part.lower())
        if root:
            return ntpath.join(root, *parts[start + 1:]) if parts[start + 1:] else root, parts[-1]
    return None


@dataclass(frozen=True)
class Geometry:
    """Where things are in a file-save dialog, in screen pixels."""
    dialog: Rect
    row: Rect | None          # the row of «Упорядочить» and «Новая папка»; None: not found
    after_x: int | None       # where that row is free, right after «Новая папка»
    scale: float = 1.0        # the dialog's DPI / 96
    color: str | None = None  # the row's background, for the panel to blend in


@dataclass(frozen=True)
class Plan:
    name: str                 # the folder name the buttons copy
    go_to: str | None         # the "PDF" folder to create and open; None: stay where the dialog is


def plan(path: str, name: str, make_pdf: bool) -> Plan:
    """What a click does in ``path``: copy ``name``, then create "PDF" there and go into it.

    Already inside a "PDF" folder, the name is the one of the folder around it (the order's
    folder) and no "PDF" is made inside "PDF".
    """
    trimmed = path.rstrip("\\/")
    if ntpath.basename(trimmed).lower() == PDF.lower():
        parent = ntpath.basename(ntpath.dirname(trimmed))
        return Plan(parent or name, None)
    return Plan(name, ntpath.join(path, PDF) if make_pdf else None)


# -- hotkeys -------------------------------------------------------------------------------------

_VK_OF_KEY = {str(i): 0x30 + i for i in range(10)}
_VK_OF_KEY.update({chr(ord("a") + i): 0x41 + i for i in range(26)})
_VK_OF_KEY.update({f"f{i}": 0x6F + i for i in range(1, 25)})
_VK_OF_KEY.update({";": 0xBA, "=": 0xBB, ",": 0xBC, "-": 0xBD, ".": 0xBE, "/": 0xBF, "`": 0xC0, "[": 0xDB,
                   "\\": 0xDC, "]": 0xDD, "'": 0xDE, "space": 0x20, "insert": 0x2D, "pause": 0x13})


def hotkey_vk(spec: str) -> tuple[frozenset, int] | None:
    """"<ctrl>+<shift>+1" → ({"ctrl", "shift"}, 0x31): the modifiers and the Windows key code."""
    parsed = parse_hotkey(spec) if spec else None
    if not parsed or isinstance(parsed, str):
        return None
    mods, key = parsed
    vk = _VK_OF_KEY.get(key)
    return (frozenset(mods), vk) if vk is not None else None


def hotkey_map(buttons: list[Button]) -> dict[tuple[frozenset, int], int]:
    """(modifiers, key code) → the button's index; the first button wins a shared combination."""
    found: dict[tuple[frozenset, int], int] = {}
    for index, button in enumerate(buttons):
        combo = hotkey_vk(button.hotkey)
        if combo is not None and combo not in found:
            found[combo] = index
    return found


# -- where the panel goes ----------------------------------------------------------------------

def _differ(a: int, b: int) -> int:
    return abs((a >> 16) - (b >> 16)) + abs(((a >> 8) & 0xFF) - ((b >> 8) & 0xFF)) + abs((a & 0xFF) - (b & 0xFF))


def ink_end(pixels: list[int], width: int, scale: float) -> int | None:
    """Where «Упорядочить» and «Новая папка» end in a strip of the row (pixels from its left edge):
    the last column with text before a wide empty stretch.  None: nothing drawn there (yet)."""
    if not pixels or width <= 0:
        return None
    height = len(pixels) // width
    background = collections.Counter(pixels).most_common(1)[0][0]
    gap = round(50 * scale)
    last = None
    for x in range(width):
        if any(_differ(pixels[y * width + x], background) > 90 for y in range(height)):
            last = x
        elif last is not None and x - last > gap:
            break
    return last


def place_bar(dialog: Rect, row: Rect | None, after_x: int | None, size: tuple[int, int], place: str,
              scale: float = 1.0, screen_top: int = 0) -> tuple[int, int, str]:
    """Top-left corner of the panel and where it ended up ("inside" or "above").

    "inside": in the row of «Упорядочить» and «Новая папка», right after them, when it fits
    there (the view buttons at the right end of the row stay free); otherwise above the dialog.
    """
    width, height = size
    if place == "inside" and row is not None and after_x is not None:
        _, top, right, bottom = row
        reserve = int(110 * scale)  # the view and help buttons at the right end of the row
        x = after_x
        if x + width <= right - reserve and bottom - top >= height * 0.7:
            return x, top + max(0, (bottom - top - height) // 2), "inside"
    x = dialog[0] + int(8 * scale)
    y = max(screen_top, dialog[1] - height - int(4 * scale))
    return x, y, "above"
