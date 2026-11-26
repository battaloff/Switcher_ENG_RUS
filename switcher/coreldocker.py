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

    def look(self) -> Look:
        try:
            return look_of(_read(self.page)[0])
        except (OSError, UnicodeDecodeError) as exc:
            raise DockerError(f"Не удалось прочитать файл панели {self.page.name}: {exc}") from exc

    def save_look(self, look: Look) -> None:
        """How big the docker draws its buttons: into the page (and the number in a row into the
        project, as Docker Studio keeps it); the files as they were are kept for ``undo``."""
        error = look.problem()
        if error:
            raise DockerError(error)
        try:
            html, bom = _read(self.page)
        except (OSError, UnicodeDecodeError) as exc:
            raise DockerError(f"Не удалось прочитать файл панели {self.page.name}: {exc}") from exc
        config, begin, end = find_config(html)
        config["columns"] = look.columns
        files = {self.page: _encoded(with_look(html[:begin] + script_json(config) + html[end:], look), bom)}
        try:
            text, project_bom = _read(self.manifest)
            project = json.loads(text)
        except (OSError, UnicodeDecodeError, ValueError):
            project = None
        if isinstance(project, dict):
            project["Columns"] = look.columns
            newline = "\r\n" if "\r\n" in text else "\n"
            files[self.manifest] = _encoded(json.dumps(project, ensure_ascii=True, indent=2).replace("\n", newline),
                                            project_bom)
        self._backup()
        try:
            for path, data in files.items():
                self._write(path, data)
        except OSError as exc:
            raise DockerError(f"Не удалось записать {exc.filename or self.folder}: Windows не разрешает менять "
                              "файлы в этой папке.") from exc
        log.info("corel docker: buttons of %s: %s", self.folder, look)

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


# -- carrying a docker to another computer ---------------------------------------------------------
#
# A docker is saved as a .zip of its files and installed from it into the Addons of every CorelDRAW
# on the other computer — as a docker of its own: another folder, another caption if that one is
# taken, and new GUIDs (CorelDRAW tells dockers, their menu commands and their pages apart by the
# GUIDs in AppUI.xslt; two add-ons with the same ones clash).  The VBA macros are left out: their
# project name ("Politext") is compiled into the .gms and two projects of one name clash too; every
# button of the installed docker does its work with the page's own code instead.

PACKAGE_SKIP = (".gms", ".bas", ".bak", ".tmp", ".log", ".zip")
PACKAGE_LIMIT = 50 * 1024 * 1024  # bytes in all: a docker is a few hundred KB
PACKAGE_FILES = 300
INSTALLED = "switcher-install.json"  # marks a docker that Switcher installed (and its GUIDs)
REGISTRATION = ("AppUI.xslt", "UserUI.xslt")
CORELDRAW_EXE = "CorelDRW.exe"


def docker_files(folder) -> dict[str, bytes]:
    """The files of a docker that go to another computer: "Politext.html" → its bytes (paths with "/").
    Not the macros, Switcher's own marks, nor copies left by Docker Studio."""
    folder = Path(folder)
    if _page_of(folder) is None:
        raise DockerError("В этой папке нет докера из Docker Studio (нет его файла .html со списком кнопок).")
    files: dict[str, bytes] = {}
    total = 0
    for path in sorted(folder.rglob("*")):
        relative = path.relative_to(folder).as_posix()
        name = path.name.lower()
        if not path.is_file() or name.endswith(PACKAGE_SKIP) or ".bak" in name or name.startswith(".switcher-") \
                or name == INSTALLED or name == MACROS.lower():
            continue
        data = path.read_bytes()
        total += len(data)
        if total > PACKAGE_LIMIT or len(files) >= PACKAGE_FILES:
            raise DockerError("В папке докера слишком много файлов — это не похоже на докер.")
        files[relative] = data
    return files


def export_docker(folder, target) -> int:
    """Save a docker into a .zip for another computer; how many files went in."""
    import zipfile

    files = docker_files(folder)
    top = Path(folder).name
    target = Path(target)
    temp = target.with_name(f".switcher-{os.getpid()}-{target.name}.tmp")
    try:
        with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as package:
            for relative, data in files.items():
                package.writestr(f"{top}/{relative}", data)
        os.replace(temp, target)
    except OSError as exc:
        try:
            temp.unlink()
        except OSError:
            pass
        raise DockerError(f"Не удалось сохранить {target}: {exc}") from exc
    log.info("corel docker: %d files of %s saved to %s", len(files), folder, target)
    return len(files)


def _safe_part(part: str) -> bool:
    return bool(part) and part not in (".", "..") and not re.search(r'[<>:"|?*\x00-\x1f]', part)


def read_package(source) -> dict[str, bytes]:
    """The docker's files from a .zip made by ``export_docker`` (or any zip of a docker's folder), or
    from a docker's folder.  Names that would land outside the docker's folder are refused."""
    import zipfile

    source = Path(source)
    if source.is_dir():
        return docker_files(source)
    try:
        package = zipfile.ZipFile(source)
    except (OSError, zipfile.BadZipFile) as exc:
        raise DockerError(f"Это не файл докера (нужен .zip, сохранённый в Switcher): {exc}") from exc
    files: dict[str, bytes] = {}
    with package:
        entries = [entry for entry in package.infolist() if not entry.is_dir()]
        if len(entries) > PACKAGE_FILES or sum(e.file_size for e in entries) > PACKAGE_LIMIT:
            raise DockerError("В архиве слишком много файлов — это не похоже на докер.")
        parts = []
        for entry in entries:
            path = entry.filename.replace("\\", "/").split("/")
            if entry.filename.startswith(("/", "\\")) or not all(_safe_part(p) for p in path):
                raise DockerError(f"В архиве файл с недопустимым именем: {entry.filename}")
            parts.append((path, entry))
        tops = {path[0] for path, _ in parts if len(path) > 1}
        strip = len(tops) == 1 and all(len(path) > 1 for path, _ in parts)  # everything in one folder
        for path, entry in parts:
            name = "/".join(path[1:] if strip else path)
            lower = name.lower()
            if lower.endswith(PACKAGE_SKIP) or ".bak" in lower or lower.split("/")[-1] in (INSTALLED, MACROS.lower()):
                continue
            files[name] = package.read(entry)
    if _page_in(files) is None:
        raise DockerError("В архиве нет докера из Docker Studio (нет файла .html со списком кнопок).")
    return files


def _page_in(files: dict[str, bytes]) -> str | None:
    """Which of the files is the docker's page."""
    for name in sorted(files, key=lambda n: (n.count("/"), n.lower())):
        if name.lower().endswith((".html", ".htm")) and "/" not in name:
            try:
                if CONFIG_MARK in files[name].decode("utf-8-sig"):
                    return name
            except UnicodeDecodeError:
                continue
    return None


def _file_named(files: dict[str, bytes], wanted: str) -> str | None:
    return next((name for name in files if name.lower() == wanted.lower()), None)


@dataclass(frozen=True)
class Target:
    """A CorelDRAW on this computer: where its add-ons go, and what the user is shown."""
    addons: Path
    label: str


def corel_targets(roots: list[str] | None = None) -> list[Target]:
    """Every CorelDRAW on this computer (…\\Corel\\…\\Programs64\\CorelDRW.exe) and its Addons folder."""
    if roots is None:
        roots = []
        for variable in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(variable)
            if base and os.path.join(base, "Corel") not in roots:
                roots.append(os.path.join(base, "Corel"))
    found: list[Target] = []
    seen: set[str] = set()
    for root in roots:
        for depth in range(1, 4):
            pattern = os.path.join(glob.escape(root), *["*"] * depth, CORELDRAW_EXE)
            for exe in sorted(glob.glob(pattern)):
                programs = Path(exe).parent
                key = os.path.normcase(str(programs))
                if key in seen:
                    continue
                seen.add(key)
                suite = programs.parent.relative_to(root).parts
                found.append(Target(programs / "Addons", "CorelDRAW " + " ".join(suite).replace(
                    "CorelDRAW Graphics Suite", "").strip() if suite else "CorelDRAW"))
    return found


_TAG = re.compile(r"<(itemData|dockerData)\b([^>]*)>", re.DOTALL)
_ATTR = re.compile(r'\b(\w+)\s*=\s*"([^"]*)"')
_GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def own_guids(app_ui: str) -> dict[str, str]:
    """The docker's own GUIDs in its AppUI.xslt: {"command": …, "browser": …, "docker": …} — the menu
    command that opens it, its web page and the docker itself (CorelDRAW's own GUIDs are left out)."""
    roles: dict[str, str] = {}
    for tag, attributes in _TAG.findall(app_ui):
        values = dict(_ATTR.findall(attributes))
        guid = values.get("guid", "")
        if not _GUID.match(guid):
            continue
        role = "docker" if tag == "dockerData" else "browser" if values.get("type") == "browser" else "command"
        roles.setdefault(role, guid.lower())
    if set(roles) != {"command", "browser", "docker"}:
        raise DockerError("Не понял файл AppUI.xslt докера: в нём нет его команды, страницы или самого докера.")
    return roles


def _xml_text(value: str) -> str:
    return value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def captions_in(addons) -> dict[str, Path]:
    """The captions of the dockers already in a CorelDRAW's Addons (lower case) → their folders."""
    return {key: folder for key, (_caption, folder) in _captions(addons).items()}


def _captions(addons) -> dict[str, tuple[str, Path]]:
    """Lower-case caption → (the caption as CorelDRAW shows it, its docker's folder)."""
    import html as html_text

    found: dict[str, tuple[str, Path]] = {}
    for app_ui in sorted(Path(addons).glob("*/AppUI.xslt")):
        try:
            text = _read(app_ui)[0]
        except (OSError, UnicodeDecodeError):
            continue
        for tag, attributes in _TAG.findall(text):
            caption = dict(_ATTR.findall(attributes)).get("userCaption")
            if tag == "dockerData" and caption:
                shown = html_text.unescape(caption).strip()
                found.setdefault(shown.lower(), (shown, app_ui.parent))
    return found


def _installed_mark(folder: Path) -> dict:
    try:
        mark = json.loads((folder / INSTALLED).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return mark if isinstance(mark, dict) else {}


def suggest_caption(name: str, targets: list[Target]) -> str:
    """The docker's name if no CorelDRAW here has a docker of that name yet (or only one Switcher
    installed under it), else «POLITEXT 2», «POLITEXT 3»…"""
    name = name.strip() or "Докер"
    taken: set[str] = set()
    for target in targets:
        for caption, folder in captions_in(target.addons).items():
            if str(_installed_mark(folder).get("caption", "")).lower() != caption:
                taken.add(caption)
    candidate, number = name, 1
    while candidate.lower() in taken:
        number += 1
        candidate = f"{name} {number}"
    return candidate


_CYRILLIC = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                     ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s",
                      "t", "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"], strict=True))


def folder_for(caption: str) -> str:
    """The folder of an installed docker: Latin letters, digits and "_" only (CorelDRAW opens the page
    by a path in AppUI.xslt), ending with "_Switcher": «Плиты 2» → "Plity_2_Switcher"."""
    letters = "".join(_CYRILLIC.get(ch.lower(), ch).upper() if ch.isupper() and ch.lower() in _CYRILLIC
                      else _CYRILLIC.get(ch, ch) for ch in caption)
    slug = re.sub(r"[^A-Za-z0-9]+", "_", letters).strip("_")[:40]
    return f"{slug or 'Docker'}_Switcher"


def prepare_install(files: dict[str, bytes], caption: str, folder: str, guids: dict[str, str]) -> dict[str, bytes]:
    """The docker's files as a docker of its own: its caption, folder and GUIDs, and no macros."""
    import html as html_text

    caption = caption.strip()
    page_name = _page_in(files)
    names = {wanted: _file_named(files, wanted) for wanted in REGISTRATION}
    if page_name is None:
        raise DockerError("В файлах нет докера из Docker Studio (нет файла .html со списком кнопок).")
    if None in names.values():
        raise DockerError("В файлах докера нет AppUI.xslt и UserUI.xslt — без них CorelDRAW его не покажет.")
    app_ui_name, user_ui_name = names["AppUI.xslt"], names["UserUI.xslt"]
    app_ui, app_bom = _read_bytes(files[app_ui_name])
    old = own_guids(app_ui)
    renamed = {old[role]: guids[role] for role in old}

    def new_guids(text: str) -> str:
        return re.sub("|".join(map(re.escape, renamed)), lambda m: renamed[m.group(0).lower()], text,
                      flags=re.IGNORECASE)

    result = dict(files)
    # AppUI.xslt: the GUIDs, the caption, and the page's path in the new folder
    app_ui = new_guids(app_ui)
    app_ui = re.sub(r'(\buserCaption\s*=\s*")[^"]*(")', lambda m: m.group(1) + _xml_text(caption) + m.group(2),
                    app_ui)
    app_ui = re.sub(r"(\[VGAppAddonsDir\][/\\])[^/\\\"]+(?=[/\\])", lambda m: m.group(1) + folder, app_ui)
    result[app_ui_name] = _encoded(app_ui, app_bom)
    user_ui, user_bom = _read_bytes(files[user_ui_name])
    result[user_ui_name] = _encoded(new_guids(user_ui), user_bom)
    # the page: its name, no macros (every button does its work with the page's own code)
    page, page_bom = _read_bytes(files[page_name])
    config, begin, end = find_config(page)
    config.update(name=caption, macroProjectName="", generateVbaMacros=False)
    for item in config["presets"]:
        if isinstance(item, dict) and item.get("actionType", PAGE_SETUP) == PAGE_SETUP:
            item["generatedMacroName"] = ""
    page = blue_guideline(page[:begin] + script_json(config) + page[end:])
    shown = html_text.escape(caption, quote=False)
    page = re.sub(r"(<title>)[^<]*(</title>)", lambda m: m.group(1) + shown + m.group(2), page, count=1)
    page = re.sub(r'(<div class="panel-title">\s*)[^<\s][^<]*?(\s*<)', lambda m: m.group(1) + shown + m.group(2),
                  page, count=1)
    result[page_name] = _encoded(page, page_bom)
    # Docker Studio's project, if any: the same name, folder and GUIDs
    manifest_name = _file_named(files, MANIFEST)
    if manifest_name is not None:
        text, bom = _read_bytes(files[manifest_name])
        try:
            project = json.loads(new_guids(text))
        except ValueError:
            project = None
        if isinstance(project, dict):
            project.update(Name=caption, FolderName=folder, GenerateVbaMacros=False, MacroProjectName="")
            newline = "\r\n" if "\r\n" in text else "\n"
            result[manifest_name] = _encoded(json.dumps(project, ensure_ascii=True, indent=2).replace("\n", newline),
                                             bom)
    return result


def _read_bytes(data: bytes) -> tuple[str, bool]:
    try:
        return data.decode("utf-8-sig"), data.startswith(b"\xef\xbb\xbf")
    except UnicodeDecodeError as exc:
        raise DockerError(f"Файл докера не в UTF-8: {exc}") from exc


def _unblock(path: Path) -> None:
    """Take off Windows' «downloaded from another computer» mark: with it CorelDRAW shows the docker empty."""
    if os.name == "nt":
        try:
            os.remove(f"{path}:Zone.Identifier")
        except OSError:
            pass


def install_docker(files: dict[str, bytes], caption: str, targets: list[Target],
                   make_writable=None) -> list[Path]:
    """Install the docker into every CorelDRAW in ``targets`` under ``caption``; its folders.

    A docker Switcher installed before under the same caption is updated in place (same folder, same
    GUIDs).  ``make_writable(folders)`` is called once, for all the folders Windows does not let
    Switcher create or change (it asks the user for an administrator's right)."""
    caption = caption.strip()
    if not caption:
        raise DockerError("Напишите, как назвать докер в CorelDRAW.")
    if not targets:
        raise DockerError("На этом компьютере не найден CorelDRAW.")
    plans: list[tuple[Path, dict[str, str]]] = []
    for target in targets:
        shown, owner = _captions(target.addons).get(caption.lower(), (caption, None))
        mark = _installed_mark(owner) if owner is not None else {}
        if owner is not None and str(mark.get("caption", "")).lower() != caption.lower():
            raise DockerError(f"В {target.label} уже есть докер «{shown}» — назовите этот по-другому.")
        if owner is not None:  # Switcher's own, from before: updated in place, with its GUIDs
            saved = mark.get("guids") if isinstance(mark.get("guids"), dict) else {}
            guids = {role: str(saved.get(role)) for role in ("command", "browser", "docker")
                     if _GUID.match(str(saved.get(role, "")))}
            plans.append((owner, guids))
            continue
        name, number = folder_for(caption), 1
        while (target.addons / name).exists():
            number += 1
            name = f"{folder_for(caption)}{number}"
        plans.append((target.addons / name, {}))
    fresh = {role: str(uuid.uuid4()) for role in ("command", "browser", "docker")}
    prepared = [(folder, prepare_install(files, caption, folder.name, {**fresh, **guids}), {**fresh, **guids})
                for folder, guids in plans]

    def write_all() -> list[Path]:
        refused = []
        for folder, content, guids in prepared:
            try:
                folder.mkdir(parents=True, exist_ok=True)
                for relative, data in content.items():
                    path = folder.joinpath(*relative.split("/"))
                    path.parent.mkdir(parents=True, exist_ok=True)
                    Docker._write(path, data)
                    _unblock(path)
                mark = {"caption": caption, "guids": guids, "by": "Switcher"}
                Docker._write(folder / INSTALLED, json.dumps(mark, ensure_ascii=False, indent=2).encode("utf-8"))
            except PermissionError:
                refused.append(folder)
        return refused

    try:
        refused = write_all()
        if refused and make_writable is not None:
            make_writable(refused)
            refused = write_all()
    except OSError as exc:
        raise DockerError(f"Не удалось записать докер: {exc}") from exc
    if refused:
        raise DockerError("Windows не разрешила записать докер в " + ", ".join(str(f) for f in refused) + ".")
    for folder, _content, _guids in prepared:
        log.info("corel docker: «%s» installed into %s", caption, folder)
    return [folder for folder, _content, _guids in prepared]


# -- how big the buttons are -----------------------------------------------------------------------
#
# The page draws its buttons by its own CSS: ".preset { float:left; width:25%; min-height:42px;
# font-size:10px; … }" — four in a row.  Switcher adds a style of its own after the page's styles
# (one block, replaced each time), so the page itself is not rewritten.

LOOK_ID = "switcher-look"
_LOOK_BLOCK = re.compile(r'[ \t]*<style id="switcher-look">.*?</style>\r?\n?', re.DOTALL)
_PRESET_RULE = re.compile(r"\.preset\s*\{([^}]*)\}")
COLUMNS, HEIGHTS, FONTS = (1, 8), (24, 120), (7, 24)  # what the settings let choose


@dataclass(frozen=True)
class Look:
    columns: int = 4   # buttons in a row
    height: int = 42   # px: the least height of a button
    font: int = 10     # px: its caption; the line under it is a little smaller

    @property
    def subtitle_font(self) -> int:
        return max(FONTS[0], round(self.font * 0.8))

    def css(self) -> str:
        width = f"{100 / self.columns:.4f}".rstrip("0").rstrip(".")
        return (f".preset{{width:{width}%;min-height:{self.height}px;font-size:{self.font}px}}"
                f".preset .subtitle{{font-size:{self.subtitle_font}px}}")

    def problem(self) -> str | None:
        for value, (low, high), what in ((self.columns, COLUMNS, "Кнопок в ряду"), (self.height, HEIGHTS, "Высота"),
                                         (self.font, FONTS, "Размер надписи")):
            if not low <= value <= high:
                return f"{what}: от {low} до {high}."
        return None


def look_of(html: str) -> Look:
    """How big the page draws its buttons: Switcher's own style if the page has it, else the page's."""
    block = _LOOK_BLOCK.search(html)
    rule = _PRESET_RULE.search(block.group(0) if block else html)
    if rule is None:
        return Look()
    body = rule.group(1)

    def value(pattern: str, default: float) -> float:
        found = re.search(pattern, body)
        return float(found.group(1)) if found else default

    width = value(r"(?<![-\w])width\s*:\s*([\d.]+)%", 25)
    return Look(columns=max(COLUMNS[0], min(COLUMNS[1], round(100 / width) if width else 4)),
                height=round(value(r"min-height\s*:\s*([\d.]+)px", 42)),
                font=round(value(r"font-size\s*:\s*([\d.]+)px", 10)))


def with_look(html: str, look: Look) -> str:
    """The page with Switcher's style for its buttons (put in or replaced; after the page's own styles)."""
    newline = "\r\n" if "\r\n" in html else "\n"
    block = f'  <style id="{LOOK_ID}">{look.css()}</style>{newline}'
    html = _LOOK_BLOCK.sub("", html, count=1)
    head = re.search(r"</head\s*>", html, re.IGNORECASE)
    at = head.start() if head else (re.search(r"<body\b", html, re.IGNORECASE) or re.search(r"$", html)).start()
    return html[:at] + block + html[at:]
