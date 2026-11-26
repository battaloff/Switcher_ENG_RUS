"""The buttons of a CorelDRAW docker made by «Corel Docker Studio», read from and written to its files.

Such a docker is a folder in CorelDRAW's ``Addons``:

* ``<Name>.html`` — the panel itself.  Its buttons ("presets") are a JSON object in the page's
  script, ``var config = {…};``: the page draws them from it, and it is what CorelDRAW shows.
* ``dockerstudio.json`` — the same list as Docker Studio's own project (PascalCase keys); kept in
  step, so that Docker Studio, if it is ever opened again, shows and regenerates the same buttons.
* ``<Name>.gms`` + ``Macros.bas`` — a VBA macro per button with its numbers compiled in.  A macro
  cannot be changed from here, so a button whose numbers no longer match its macro loses the
  macro's name (``generatedMacroName``) and the page does the same work with its own code.

Plain logic and file work only, tested on any system; asking Windows for the right to write into
``Program Files`` is in ``platform/win_access.py`` and the settings page in ``gui.py``.
"""

from __future__ import annotations

import dataclasses
import glob
import hashlib
import json
import logging
import math
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from .savebar import normal_color

log = logging.getLogger(__name__)

MANIFEST = "dockerstudio.json"
MACROS = "Macros.bas"
CONFIG_MARK = "var config = "
PAGE_SETUP = "ProductionPageSetup"  # the page's width and height, and a guideline
ABSOLUTE, ADD = "Absolute", "AddToCurrent"
DEFAULT_COLOR = "#3B82F6"
MAX_MM = 45000  # CorelDRAW's largest page is 45.72 m
KEEP_UNDO = 20


class DockerError(Exception):
    """Something the user is told as it is (in Russian)."""


def _number(value) -> int | float:
    """A number from the files or the user ("37,5"); whole numbers stay whole (450, not 450.0)."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, str):
        value = value.strip().replace(",", ".").replace(" ", "")
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(number):
        return 0
    return int(number) if number == int(number) else round(number, 3)


def number_text(value) -> str:
    """A number as the buttons write it: 450, 37,5."""
    return str(_number(value)).replace(".", ",")


@dataclass(frozen=True)
class Preset:
    """One button of the docker."""
    title: str
    group: str = ""
    subtitle: str = ""
    color: str = DEFAULT_COLOR
    width: float = 0          # mm: every page gets this width
    height: float = 0         # mm: the pages' height (``absolute``) or what is added to it
    absolute: bool = False
    guideline: float = 0      # mm from the bottom edge to a horizontal guideline; 0: none
    id: str = ""
    action: str = PAGE_SETUP  # buttons of another kind keep their action; only their look is edited

    @property
    def sets_page(self) -> bool:
        return self.action == PAGE_SETUP

    def what(self) -> str:
        """What the settings list says the button does."""
        if not self.sets_page:
            return f"другое действие ({self.action})"
        height = f"высота {number_text(self.height)}" if self.absolute else f"высота +{number_text(self.height)}"
        guide = f"направляющая {number_text(self.guideline)} мм" if self.guideline else "без направляющей"
        return f"ширина {number_text(self.width)} · {height} · {guide}"

    def columns(self) -> tuple[str, str, str]:
        """Width, height and guideline for the settings list: «510 мм», «+32 мм», «26 мм»."""
        if not self.sets_page:
            return "—", "—", "другое действие"
        height = f"{number_text(self.height)} мм ровно" if self.absolute else f"+{number_text(self.height)} мм"
        return (f"{number_text(self.width)} мм", height,
                f"{number_text(self.guideline)} мм" if self.guideline else "—")

    def numbers(self) -> tuple:
        return _number(self.width), _number(self.height), _number(self.guideline), bool(self.absolute)


def auto_title(width, height, absolute: bool) -> str:
    """The caption Docker Studio gives a button: «450×370», «510×+32»."""
    return f"{number_text(width)}×{'' if absolute else '+'}{number_text(height)}"


def auto_subtitle(absolute: bool, guideline) -> str:
    """The small line under the caption: «→26mm», or «absolute» for an exact height without a guideline."""
    if _number(guideline):
        return f"→{number_text(guideline)}mm"
    return "absolute" if absolute else ""


def problem(preset: Preset) -> str | None:
    """Why a button cannot be saved (told to the user), or None."""
    if not preset.title.strip():
        return "Напишите надпись для кнопки."
    if not preset.sets_page:
        return None
    if not 0 < _number(preset.width) <= MAX_MM:
        return "Ширина страницы — число больше нуля, в миллиметрах."
    if preset.absolute and not 0 < _number(preset.height) <= MAX_MM:
        return "Высота страницы — число больше нуля, в миллиметрах."
    if not preset.absolute and not 0 <= _number(preset.height) <= MAX_MM:
        return "Прибавка к высоте — число от нуля, в миллиметрах."
    if not 0 <= _number(preset.guideline) <= MAX_MM:
        return "Направляющая — расстояние от нижнего края в миллиметрах (0 — без неё)."
    return None


# -- the order of the buttons -----------------------------------------------------------------------

def groups_of(presets: list[Preset]) -> list[str]:
    """The groups in the order the docker shows them: by their first button."""
    return list(dict.fromkeys(p.group for p in presets))


def arranged(presets: list[Preset]) -> list[Preset]:
    """The buttons as the docker draws them: group after group, each group's buttons together."""
    return [p for group in groups_of(presets) for p in presets if p.group == group]


def move(presets: list[Preset], index: int, step: int) -> tuple[list[Preset], int]:
    """Move a button one place inside its group; the new list and where the button is now."""
    presets = arranged(presets)
    other = index + step
    if not 0 <= index < len(presets) or not 0 <= other < len(presets) or presets[other].group != presets[index].group:
        return presets, index
    presets[index], presets[other] = presets[other], presets[index]
    return presets, other


def move_group(presets: list[Preset], index: int, step: int) -> tuple[list[Preset], int]:
    """Move the whole group of the button at ``index`` one group up or down."""
    presets = arranged(presets)
    if not 0 <= index < len(presets):
        return presets, index
    moved = presets[index]
    groups = groups_of(presets)
    at = groups.index(moved.group)
    if not 0 <= at + step < len(groups):
        return presets, index
    groups[at], groups[at + step] = groups[at + step], groups[at]
    result = [p for group in groups for p in presets if p.group == group]
    return result, result.index(moved)


# -- the docker's files -----------------------------------------------------------------------------

def find_dockers(roots: list[str] | None = None) -> list[Path]:
    """Folders of Docker Studio dockers in CorelDRAW's Addons (…\\Corel\\…\\Addons\\<name>\\dockerstudio.json)."""
    if roots is None:
        roots = []
        for variable in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
            base = os.environ.get(variable)
            if base and os.path.join(base, "Corel") not in roots:
                roots.append(os.path.join(base, "Corel"))
    found: list[Path] = []
    for root in roots:
        for depth in range(4):
            pattern = os.path.join(glob.escape(root), *["*"] * depth, "Addons", "*", MANIFEST)
            for manifest in sorted(glob.glob(pattern)):
                folder = Path(manifest).parent
                if folder not in found and _page_of(folder) is not None:
                    found.append(folder)
    return found


def _read(path: Path) -> tuple[str, bool]:
    """A UTF-8 file's text exactly as it is (line ends untouched) and whether it starts with a BOM."""
    raw = path.read_bytes()
    return raw.decode("utf-8-sig"), raw.startswith(b"\xef\xbb\xbf")


def _encoded(text: str, bom: bool) -> bytes:
    return (b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8")


def _page_of(folder: Path) -> Path | None:
    """The docker's page: the .html in the folder that holds the list of buttons."""
    try:
        pages = sorted(p for p in folder.iterdir() if p.suffix.lower() in (".html", ".htm") and p.is_file())
    except OSError:
        return None
    pages.sort(key=lambda p: p.stem.lower() != folder.name.lower())  # <Folder>.html first
    for page in pages:
        try:
            if CONFIG_MARK in _read(page)[0]:
                return page
        except (OSError, UnicodeDecodeError):
            continue
    return None


def find_config(html: str) -> tuple[dict, int, int]:
    """The page's ``config`` object and where its text begins and ends in the page."""
    mark = html.find(CONFIG_MARK)
    if mark < 0:
        raise DockerError("В файле панели нет списка кнопок — это не докер из Docker Studio.")
    begin = mark + len(CONFIG_MARK)
    try:
        config, end = json.JSONDecoder().raw_decode(html, begin)
    except ValueError as exc:
        raise DockerError(f"Список кнопок в файле панели не читается: {exc}") from exc
    if not isinstance(config, dict) or not isinstance(config.get("presets"), list):
        raise DockerError("Список кнопок в файле панели не читается: в нём нет кнопок.")
    return config, begin, end


def script_json(value) -> str:
    """JSON that is safe inside a <script> of the page, written as Docker Studio writes it (compact)."""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return text.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def _color(value) -> str:
    """A button's colour: "#RRGGBB" in capitals; one written another way ("#abc", "red") stays as it is."""
    return normal_color(value) or str(value or "").strip() or DEFAULT_COLOR


def _preset_of(item: dict) -> Preset:
    return Preset(title=str(item.get("title") or ""), group=str(item.get("group") or ""),
                  subtitle=str(item.get("subtitle") or ""), color=_color(item.get("color")),
                  width=_number(item.get("width")), height=_number(item.get("height")),
                  absolute=item.get("heightMode") == ABSOLUTE, guideline=_number(item.get("guidelineDistanceMm")),
                  id=str(item.get("id") or ""), action=str(item.get("actionType") or PAGE_SETUP))


_SUB = re.compile(r"^\s*Public\s+Sub\s+(\w+)\s*\(\s*\)(.*?)^\s*End\s+Sub",
                  re.DOTALL | re.MULTILINE | re.IGNORECASE)
_CALL = re.compile(r"^\s*ApplyProductionPreset\s+([^\r\n']+)", re.MULTILINE | re.IGNORECASE)
_MODULE = re.compile(r'^Attribute\s+VB_Name\s*=\s*"(\w+)"', re.MULTILINE | re.IGNORECASE)


def macro_numbers(source: str) -> dict[str, tuple]:
    """What every generated macro does: {"Macros.Preset003": (490, 60, 35, False)} — width, height,
    guideline, exact height — from the text of Macros.bas."""
    module = _MODULE.search(source)
    prefix = (module.group(1) if module else "Macros") + "."
    found: dict[str, tuple] = {}
    for name, body in _SUB.findall(source):
        call = _CALL.search(body)
        if not call:
            continue
        parts = [part.strip() for part in call.group(1).split(",")]
        if len(parts) != 4 or parts[3].lower() not in ("true", "false"):
            continue
        try:
            numbers = [_number(float(part.rstrip("#!&@%"))) for part in parts[:3]]
        except ValueError:
            continue
        found[prefix + name] = (*numbers, parts[3].lower() == "true")
    return found


# the page's own code for a button draws the guideline without a colour; the macros make it blue
_GUIDE = re.compile(r"(var guide = layer\.CreateGuideAngle\([^;]*;)(\r?\n)([ \t]*)(guide\.Visible = true;)")
_BLUE = "try { guide.Outline.Color.RGBAssign(0, 0, 255); } catch (colorError) {}"


def blue_guideline(html: str) -> str:
    """The page's own code makes its guideline blue, as the macros do (added once)."""
    if "guide.Outline.Color.RGBAssign" in html:
        return html
    return _GUIDE.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{_BLUE}{m.group(2)}{m.group(3)}{m.group(4)}",
                      html, count=1)


class Docker:
    """One docker's folder: its buttons are read with ``load`` and written with ``save``."""

    def __init__(self, folder, backups: Path | None = None):
        self.folder = Path(folder)
        page = _page_of(self.folder) if self.folder.is_dir() else None
        if page is None:
            raise DockerError("В этой папке нет докера из Docker Studio (нет его файла .html со списком кнопок).")
        self.page = page
        self.manifest = self.folder / MANIFEST
        self.name = self.folder.name
        key = hashlib.sha1(str(self.folder).lower().encode("utf-8")).hexdigest()[:8]
        if backups is None:
            from .paths import data_dir

            backups = data_dir() / "corel_docker"
        self.backups = Path(backups) / f"{self.folder.name}-{key}"

    # -- reading ---------------------------------------------------------------------------------

    def load(self) -> list[Preset]:
        try:
            html, _bom = _read(self.page)
        except (OSError, UnicodeDecodeError) as exc:
            raise DockerError(f"Не удалось прочитать файл панели {self.page.name}: {exc}") from exc
        config, _begin, _end = find_config(html)
        self.name = str(config.get("name") or self.folder.name)
        presets, seen = [], set()
        for item in config["presets"]:
            if isinstance(item, dict):
                preset = _preset_of(item)
                if not preset.id or preset.id in seen:  # the settings list tells the buttons apart by their ids
                    preset = dataclasses.replace(preset, id=str(uuid.uuid4()))
                seen.add(preset.id)
                presets.append(preset)
        return arranged(presets)

    def writable(self) -> bool:
        """Whether Switcher may change the docker's files (Program Files is closed to ordinary programs)."""
        probe = self.folder / f".switcher-{os.getpid()}.tmp"
        try:
            probe.write_bytes(b"")
            probe.unlink()
            for path in (self.page, self.manifest):
                if path.exists():
                    os.close(os.open(path, os.O_RDWR))
        except OSError:
            return False
        return True

    # -- writing ---------------------------------------------------------------------------------

    def save(self, presets: list[Preset]) -> None:
        """Write the buttons into the page and into Docker Studio's project; the files as they were
        are kept for ``undo``."""
        presets = arranged([p if p.id else dataclasses.replace(p, id=str(uuid.uuid4())) for p in presets])
        try:
            html, bom = _read(self.page)
        except (OSError, UnicodeDecodeError) as exc:
            raise DockerError(f"Не удалось прочитать файл панели {self.page.name}: {exc}") from exc
        config, begin, end = find_config(html)
        config["presets"] = self._page_items(config, presets)
        new_html = blue_guideline(html[:begin] + script_json(config) + html[end:])
        files = {self.page: _encoded(new_html, bom)}
        project = self._project(presets)
        if project is not None:
            files[self.manifest] = project
        self._backup()
        try:
            for path, data in files.items():
                self._write(path, data)
        except OSError as exc:
            raise DockerError(f"Не удалось записать {exc.filename or self.folder}: Windows не разрешает менять "
                              "файлы в этой папке.") from exc
        log.info("corel docker: %d buttons written to %s", len(presets), self.folder)

    def _page_items(self, config: dict, presets: list[Preset]) -> list[dict]:
        old = {str(item.get("id")): item for item in config["presets"] if isinstance(item, dict)}
        try:
            macros = macro_numbers(_read(self.folder / MACROS)[0])
        except (OSError, UnicodeDecodeError):
            macros = None  # no text of the macros: a button keeps its macro while its numbers are not touched
        items = []
        for preset in presets:
            before = old.get(preset.id)
            item = dict(before) if before else {
                "id": preset.id or str(uuid.uuid4()), "group": "", "title": "", "subtitle": "", "color": "",
                "actionType": PAGE_SETUP, "width": 0, "height": 0, "unit": 3, "heightMode": ADD,
                "guidelineDistanceMm": 0, "macroProject": "", "macroName": "", "macroFileName": "",
                "generatedMacroName": "", "minimumCorelVersion": config.get("minimumCorelVersion", 25)}
            item.update(group=preset.group, title=preset.title, subtitle=preset.subtitle, color=preset.color)
            if preset.sets_page:
                name = str(item.get("generatedMacroName") or "")
                same = macros.get(name) == preset.numbers() if macros is not None else (
                    before is not None and _preset_of(before).numbers() == preset.numbers())
                item.update(width=_number(preset.width), height=_number(preset.height),
                            heightMode=ABSOLUTE if preset.absolute else ADD,
                            guidelineDistanceMm=_number(preset.guideline),
                            generatedMacroName=name if name and same else "")
            items.append(item)
        return items

    def _project(self, presets: list[Preset]) -> bytes | None:
        """dockerstudio.json with the same buttons, or None when there is none to keep in step."""
        try:
            text, bom = _read(self.manifest)
            project = json.loads(text)
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            log.warning("corel docker: %s is left as it is: %s", self.manifest, exc)
            return None
        if not isinstance(project, dict):
            return None
        listed = project.get("Presets") if isinstance(project.get("Presets"), list) else []
        old = {str(item.get("Id")): item for item in listed if isinstance(item, dict)}
        items = []
        for order, preset in enumerate(presets):
            item = dict(old.get(preset.id) or {
                "Id": preset.id, "Order": 0, "Group": "", "Title": "", "Subtitle": "", "Color": "",
                "ActionType": PAGE_SETUP, "Width": 0, "Height": 0, "Unit": "Millimeter", "HeightMode": ADD,
                "GuidelineDistanceMm": 0, "MacroProject": "", "MacroName": "", "MacroFileName": "",
                "MinimumCorelVersion": project.get("MinimumCorelVersion", 25)})
            item.update(Order=order, Group=preset.group, Title=preset.title, Subtitle=preset.subtitle,
                        Color=preset.color)
            if preset.sets_page:
                item.update(Width=_number(preset.width), Height=_number(preset.height),
                            HeightMode=ABSOLUTE if preset.absolute else ADD,
                            GuidelineDistanceMm=_number(preset.guideline))
            items.append(item)
        project["Presets"] = items
        newline = "\r\n" if "\r\n" in text else "\n"
        return _encoded(json.dumps(project, ensure_ascii=True, indent=2).replace("\n", newline), bom)

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        """Replace the file in one step, so that CorelDRAW never reads half of it."""
        temp = path.with_name(f".switcher-{os.getpid()}-{path.name}.tmp")
        try:
            temp.write_bytes(data)
            os.replace(temp, path)
        except OSError:
            try:
                temp.unlink()
            except OSError:
                pass
            raise

    # -- the files as they were ------------------------------------------------------------------------

    def _kept(self) -> list[Path]:
        try:
            return sorted((p for p in (self.backups / "undo").iterdir() if p.is_dir() and p.name.isdigit()),
                          key=lambda p: int(p.name))
        except OSError:
            return []

    def _backup(self) -> None:
        """Copy the page and the project as they are now: the first copy ever is kept for good
        (``original``), the rest are steps for ``undo``."""
        kept = self._kept()
        targets = [self.backups / "undo" / str(int(kept[-1].name) + 1 if kept else 1)]
        if not (self.backups / "original").exists():
            targets.append(self.backups / "original")
        try:
            for target in targets:
                target.mkdir(parents=True, exist_ok=True)
                for path in (self.page, self.manifest):
                    if path.exists():
                        shutil.copyfile(path, target / path.name)
            for old in kept[:max(0, len(kept) + 1 - KEEP_UNDO)]:
                shutil.rmtree(old, ignore_errors=True)
        except OSError as exc:
            raise DockerError(f"Не удалось сохранить копию файлов докера в {self.backups}: {exc}") from exc

    def can_undo(self) -> bool:
        return bool(self._kept())

    def undo(self) -> bool:
        """Put back the files as they were before the last ``save``; False: nothing to put back."""
        kept = self._kept()
        if not kept:
            return False
        last = kept[-1]
        try:
            for path in (self.page, self.manifest):
                if (last / path.name).exists():
                    self._write(path, (last / path.name).read_bytes())
        except OSError as exc:
            raise DockerError(f"Не удалось вернуть файлы докера: {exc}") from exc
        shutil.rmtree(last, ignore_errors=True)
        log.info("corel docker: the files of %s are put back as they were", self.folder)
        return True
