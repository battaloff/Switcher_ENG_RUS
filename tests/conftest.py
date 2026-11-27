from __future__ import annotations

import os
import sys

import pytest

from switcher.config import Config
from switcher.controller import Controller, KeyEvent
from switcher.engine import Engine
from switcher.langmodel import load_models
from switcher.layouts import DEFAULT_KEYBOARD, EN, Keyboard, Stroke, text_lang
from switcher.learner import Learner
from switcher.profile import Profile


@pytest.fixture(scope="session")
def models():
    return load_models()


@pytest.fixture
def keyboard():
    return DEFAULT_KEYBOARD


@pytest.fixture
def profile():
    p = Profile(":memory:")
    yield p
    p.close()


# -- a CorelDRAW docker as «Corel Docker Studio» makes it (see switcher/coreldocker.py) ---------------

# The document check of a Docker Studio page, cut down: the same functions and the lines Switcher changes
# (coreldocker.lighter_check), as the page on the user's computers has them.
DOCKER_AUDIT = """
                    function setAuditStatus(message, isError) {
                  var node = document.getElementById('auditStatus');
                  text(node, message || '');
                  node.className = isError ? 'status error' : 'status';
                }

                function emptyAudit() {
                  return { pages: 0, objects: 0, textObjects: 0, fallbackId: 0, spotColors: {}, oleObjects: [],
                    fonts: {}, rgbObjectIds: {}, effects: {}, effectObjectIds: {} };
                }

                function dictionaryValues(dictionary) {
                  var values = [];
                  for (var key in dictionary) {
                    if (dictionary.hasOwnProperty(key)) values.push(dictionary[key]);
                  }
                  return values;
                }

                function collectionItem(collection, index) {
                  try { return collection.Item(index); }
                  catch (firstError) { return collection(index); }
                }

                function shapeId(shape, pageIndex, audit) {
                  try { return pageIndex + ':' + String(shape.StaticID); }
                  catch (e) {
                    audit.fallbackId++;
                    return pageIndex + ':fallback:' + audit.fallbackId;
                  }
                }

                function inspectColor(color, audit, objectId) {
                  if (!color) return;
                  var typeNumber;
                  try { typeNumber = parseInt(color.Type, 10); }
                  catch (e) { return; }

                  if (typeNumber === 5) {
                    audit.rgbObjectIds[objectId] = true;
                  }

                  if (typeNumber !== 1 && typeNumber !== 14 &&
                      typeNumber !== 22 && typeNumber !== 25 &&
                      typeNumber !== 26) return;

                  var name = String(color.Name || 'PANTONE');
                  var key = name.toLowerCase();
                  if (!audit.spotColors[key]) {
                    audit.spotColors[key] = { name: name, pantone: typeNumber === 1, ids: {} };
                  }
                  audit.spotColors[key].ids[objectId] = true;
                }

                function inspectFill(fill, audit, objectId) {
                  if (!fill) return;
                  var fillType;
                  try { fillType = parseInt(fill.Type, 10); }
                  catch (e) { return; }
                  if (fillType === 1) {
                    try { inspectColor(fill.UniformColor, audit, objectId); } catch (uniformError) {}
                  }
                }

                function inspectOutline(outline, audit, objectId) {
                  try {
                    if (outline && parseInt(outline.Type, 10) !== 0) {
                      inspectColor(outline.Color, audit, objectId);
                    }
                  } catch (e) {}
                }

                function addEffect(audit, name, objectId) {
                  var key = String(name).toLowerCase();
                  if (!audit.effects[key]) audit.effects[key] = { name: name, ids: {} };
                  audit.effects[key].ids[objectId] = true;
                  audit.effectObjectIds[objectId] = true;
                }

                function inspectShapeEffects(shape, typeNumber, audit, objectId) {
                  if (typeNumber === 16) addEffect(audit, 'Тень', objectId);
                  var safeGraphicShape = typeNumber >= 1 && typeNumber <= 6;
                  if (!safeGraphicShape) return;
                  try {
                    if (parseInt(shape.Transparency.Type, 10) !== 0) {
                      addEffect(audit, 'Прозрачность', objectId);
                    }
                  } catch (transparencyError) {}
                }

                function addFont(audit, name, objectId) {
                  name = String(name || '');
                  if (!name) return;
                  var key = name.toLowerCase();
                  if (!audit.fonts[key]) audit.fonts[key] = { name: name, ids: {} };
                  audit.fonts[key].ids[objectId] = true;
                }

                function inspectTextShape(shape, audit, objectId) {
                  var story;
                  try { story = shape.Text.Story; }
                  catch (e) { return; }

                  audit.textObjects++;
                  var font = '';
                  try { font = String(story.Font || ''); } catch (fontError) {}
                  addFont(audit, font, objectId);
                  try {
                    inspectFill(story.Fill, audit, objectId);
                  } catch (storyFillError) {}
                  try {
                    inspectOutline(story.Outline, audit, objectId);
                  } catch (storyOutlineError) {}
                }

                function inspectOle(audit, pageIndex, objectId) {
                  audit.oleObjects.push({ page: pageIndex, id: objectId, program: 'OLE / штрихкод' });
                }

                function scanShapeCollection(collection, audit, pageIndex, seen) {
                  if (!collection) return;
                  var count = 0;
                  try { count = parseInt(collection.Count, 10) || 0; }
                  catch (e) { return; }

                  for (var i = 1; i <= count; i++) {
                    var shape;
                    try { shape = collectionItem(collection, i); }
                    catch (itemError) { continue; }

                    var objectId = shapeId(shape, pageIndex, audit);
                    if (seen[objectId]) continue;
                    seen[objectId] = true;

                    var typeNumber = -1;
                    try { typeNumber = parseInt(shape.Type, 10); }
                    catch (typeError) {}
                    if (typeNumber !== 7) audit.objects++;
                    inspectShapeEffects(shape, typeNumber, audit, objectId);
                    if (typeNumber === 6) {
                      inspectTextShape(shape, audit, objectId);
                    }
                    if (typeNumber === 12) {
                      inspectOle(audit, pageIndex, objectId);
                      continue;
                    }

                    var safeGraphicShape =
                      typeNumber >= 1 && typeNumber <= 6;
                    if (safeGraphicShape) {
                      try {
                        inspectFill(shape.Fill, audit, objectId);
                      } catch (fillError) {}
                      try {
                        inspectOutline(shape.Outline, audit, objectId);
                      } catch (outlineError) {}
                    }

                    if (typeNumber === 7) {
                      try {
                        scanShapeCollection(
                          shape.Shapes.All(), audit, pageIndex, seen);
                      } catch (groupError) {}
                    }
                  }
                }

                function installedFonts(app) {
                  if (installedFontMap) return installedFontMap;
                  var result = {};
                  try {
                    var list = app.FontList;
                    for (var i = 1; i <= list.Count; i++) {
                      var name = String(collectionItem(list, i) || '');
                      if (name) result[name.toLowerCase()] = true;
                    }
                    installedFontMap = result;
                  } catch (e) {}
                  return result;
                }

                function scanDocument(app, doc) {
                  var audit = emptyAudit();
                  var seen = {};
                  audit.pages = parseInt(doc.Pages.Count, 10) || 0;
                  for (var pageIndex = 1; pageIndex <= audit.pages; pageIndex++) {
                    try {
                      var page = doc.Pages.Item(pageIndex);
                      scanShapeCollection(
                        page.Shapes.All(), audit, pageIndex, seen);
                    } catch (pageError) {}
                  }

                  var fontMap = installedFonts(app);
                  var fonts = dictionaryValues(audit.fonts);
                  for (var i = 0; i < fonts.length; i++) {
                    fonts[i].missing = !fontMap[fonts[i].name.toLowerCase()];
                  }
                  return audit;
                }

                function auditCard(label, value, note, state) {
                  return '<div class="audit-card ' + state + '"><div class="audit-value">' + value +
                    '</div><div class="audit-label">' + label + '</div><div class="audit-note">' + note +
                    '</div></div>';
                }

                function renderAudit(audit, preview) {
                  var spots = dictionaryValues(audit.spotColors);
                  var fonts = dictionaryValues(audit.fonts);
                  var missingCount = 0;
                  for (var j = 0; j < fonts.length; j++) {
                    if (fonts[j].missing) missingCount++;
                  }
                  var rgbCount = dictionaryValues(audit.rgbObjectIds).length;
                  var effectCount = dictionaryValues(audit.effectObjectIds).length;

                  var summary = '';
                  summary += auditCard(
                    'PANTONE / Spot', String(spots.length), '', spots.length ? 'notice' : 'ok');
                  summary += auditCard(
                    'OLE-объекты', String(audit.oleObjects.length), '', audit.oleObjects.length ? 'warning' : 'ok');
                  summary += auditCard(
                    'Шрифты', String(fonts.length), 'Нет в системе: ' + missingCount, missingCount ? 'warning' : 'ok');
                  summary += auditCard(
                    'RGB-объекты', String(rgbCount), '', rgbCount ? 'warning' : 'ok');
                  summary += auditCard(
                    'Эффекты', String(effectCount), '', effectCount ? 'warning' : 'ok');
                  document.getElementById('auditSummary').innerHTML = summary;
                  setAuditStatus(
                    preview
                      ? 'Пример панели.'
                      : 'Проверено: ' + audit.objects + ' объектов.',
                    false);
                }

                function refreshAudit(deepText) {
                  var panel = document.getElementById('auditPanel');
                  panel.style.display = config.showDocumentAudit ? 'block' : 'none';
                  if (!config.showDocumentAudit || auditInProgress) return;

                  var app = getApplication();
                  if (!app) {
                    renderAudit(emptyAudit(), true);
                    return;
                  }

                  auditInProgress = true;
                  try {
                    var doc = requireDocument(app);
                    renderAudit(scanDocument(app, doc), false);
                  } catch (e) {
                    document.getElementById('auditSummary').innerHTML = '';
                    setAuditStatus(String(e.message || e.description || 'Нет открытого документа'), false);
                  } finally {
                    auditInProgress = false;
                  }
                }

    function startUp() {
      refreshTimer = setTimeout(function () {
        refreshAudit(false);
      }, 1200);
      if (config.enableLiveUpdates) {
        try { window.external.RegisterEventListener('SelectionChange', 'refreshAudit(false)'); } catch (e) {}
      }
    }

    window.onload = startUp;
"""
DOCKER_PAGE = """<!DOCTYPE html>
<html>
<head><meta http-equiv="Content-Type" content="text/html; charset=utf-8" /><title>POLITEXT</title>
  <style type="text/css">
    .preset-grid { overflow:hidden; margin:0 -2px 6px -2px; }
    .preset {
      float:left;
      width:25%;
      min-height:42px;
      padding:6px 4px;
      font-size:10px;
    }
    .preset:hover { filter:brightness(.92); }
    .subtitle { display:block; margin-top:2px; font-size:8px; }
  </style>
</head>
<body>
  <div class="panel">
    <div class="panel-title">
      POLITEXT
      <span class="counter" id="presetCounter"></span>
    </div>
  </div>
  <div id="presets"></div>
  <div id="auditPanel" class="panel" style="display:none;">
    <div class="panel-title">Проверка документа <span class="counter" id="auditCounter"></span></div>
    <button class="quick" type="button" onclick="refreshAudit(true)">Проверить сейчас</button>
    <span id="auditScope" class="audit-scope">Все страницы</span>
    <div id="auditSummary" class="audit-summary"></div>
    <div id="auditDetails"></div>
    <div id="auditStatus" class="status"></div>
  </div>
  <script type="text/javascript">
    var config = %s;
    var registeredEvents = [];
    var refreshTimer = null;
    var auditInProgress = false;
    var installedFontMap = null;

    function text(node, value) {
      node.textContent = value;
    }

    function escapeHtml(value) {
      return String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    function requireDocument(app) {
      if (!app || !app.Documents || app.Documents.Count < 1 || !app.ActiveDocument) {
        throw new Error('Сначала откройте документ CorelDRAW.');
      }
      return app.ActiveDocument;
    }

    function getApplication() {
      try {
        return window.external && window.external.Application
          ? window.external.Application
          : null;
      } catch (e) {
        return null;
      }
    }

    function getCorelMajor(app) {
      try {
        var version = app.VersionMajor || app.Version || 0;
        var match = String(version).match(/\\d+/);
        return match ? parseInt(match[0], 10) : 0;
      } catch (e) { return 0; }
    }

    function applyProductionPreset(preset, doc) {
      if (preset.guidelineDistanceMm > 0) {
        try {
          var layer = doc.MasterPage.GuidesLayer;
          var guide = layer.CreateGuideAngle(
            doc.ActivePage.SizeWidth / 2, preset.guidelineDistanceMm, 0);
          guide.Visible = true;
        } catch (guideError) {}
      }
    }
%AUDIT%
  </script>
</body>
</html>
""".replace("%AUDIT%", DOCKER_AUDIT)
DOCKER_MACROS = """Attribute VB_Name = "Macros"
Option Explicit

' Generated by Corel Docker Studio.

Public Sub Preset001()
    ' 450x370
    ApplyProductionPreset 450#, 370#, 0#, True
End Sub

Public Sub Preset002()
    ' 510x+32
    ApplyProductionPreset 510#, 32#, 26#, False
End Sub

Public Sub Preset003()
    ' 525x+94
    ApplyProductionPreset 525#, 94.5, 57#, False
End Sub

Public Sub Preset004()
    ' 510x+36
    ApplyProductionPreset 510#, 36#, 28#, False
End Sub

Private Sub ApplyProductionPreset(ByVal widthMM As Double, ByVal heightValueMM As Double, _
                                  ByVal guidelineDistanceMM As Double, ByVal absoluteHeight As Boolean)
End Sub
"""
DOCKER_GUIDS = {"command": "589a0b6a-7d25-4595-8e98-a1a7dc130ec7", "browser": "791aad5e-f03b-451d-91f6-d61094e67ff3",
                "docker": "537a340d-032f-43f1-9c0c-048168fa0924"}
DOCKER_APP_UI = """<?xml version="1.0" encoding="UTF-8"?>
<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform" xmlns:frmwrk="Corel Framework Data">
  <xsl:template match="uiConfig/items">
    <xsl:copy>
      <xsl:apply-templates select="node()|@*"/>
      <itemData guid="{command}"
                noBmpOnMenu="true"
                type="checkButton"
                check="*Docker('{docker}')"
                dynamicCategory="2cc24a3e-fe24-4708-9a74-9c75406eebcd"
                userCaption="{caption}"
                enable="true"/>
      <itemData guid="{browser}"
                type="browser"
                href="[VGAppAddonsDir]/{folder}/Politext.html"
                enable="true"
                isHost="true"
                appStyles="false" />
    </xsl:copy>
  </xsl:template>
  <xsl:template match="uiConfig/dockers">
    <xsl:copy>
      <xsl:apply-templates select="node()|@*"/>
      <dockerData guid="{docker}"
                  userCaption="{caption}"
                  wantReturn="true"
                  focusStyle="noThrow">
        <container>
          <item dock="fill" margin="0,0,0,0" guidRef="{browser}"/>
        </container>
      </dockerData>
    </xsl:copy>
  </xsl:template>
</xsl:stylesheet>
"""
DOCKER_USER_UI = """<?xml version="1.0" encoding="UTF-8"?>
<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform" xmlns:frmwrk="Corel Framework Data">
  <xsl:template match="commandBarData[@guid='3eaa9bbe-28fd-4672-9128-02974ee96332']/menu">
    <xsl:copy>
      <xsl:apply-templates select="node()|@*"/>
      <xsl:if test="not(./item[@guidRef='{command}'])">
        <item guidRef="{command}"/>
      </xsl:if>
    </xsl:copy>
  </xsl:template>
</xsl:stylesheet>
"""
# (id, group, title, subtitle, colour, width, height, exact height, guideline); the last one is of
# the group «510» but was added after «525», as buttons added later in Docker Studio are
DOCKER_PRESETS = (
    ("a1", "450 – 490", "450×370", "absolute", "#6B7280", 450, 370, True, 0),
    ("b2", "510", "510×+32", "→26mm", "#3B82F6", 510, 32, False, 26),
    ("c3", "525", "525×+94,5", "→57mm", "#10B981", 525, 94.5, False, 57),
    ("d4", "510", "510×+36", "→28mm", "#2563EB", 510, 36, False, 28),
)


def make_docker(root, bom: bool = False, newline: str = "\n", macros: bool = True, version: str = "26",
                name: str = "Politext", caption: str = "POLITEXT"):
    """A docker's folder under ``root``, where CorelDRAW keeps its add-ons; the folder."""
    import json

    programs = root / "Corel" / "CorelDRAW Graphics Suite" / version / "Programs64"
    folder = programs / "Addons" / name
    folder.mkdir(parents=True)
    (programs / "CorelDRW.exe").write_bytes(b"MZ")
    (folder / "AppUI.xslt").write_bytes(DOCKER_APP_UI.format(**DOCKER_GUIDS, caption=caption, folder=name).replace(
        "\n", "\r\n").encode())
    (folder / "UserUI.xslt").write_bytes(DOCKER_USER_UI.format(**DOCKER_GUIDS).replace("\n", "\r\n").encode())
    (folder / "Coreldrw.addon").write_bytes(b"")
    page, project = [], []
    for order, (ident, group, title, subtitle, color, width, height, exact, guideline) in enumerate(DOCKER_PRESETS):
        mode = "Absolute" if exact else "AddToCurrent"
        page.append({"id": ident, "group": group, "title": title, "subtitle": subtitle, "color": color,
                     "actionType": "ProductionPageSetup", "width": width, "height": height, "unit": 3,
                     "heightMode": mode, "guidelineDistanceMm": guideline, "macroProject": "", "macroName": "",
                     "macroFileName": "", "generatedMacroName": f"Macros.Preset{order + 1:03d}",
                     "minimumCorelVersion": 25})
        project.append({"Id": ident, "Order": order, "Group": group, "Title": title, "Subtitle": subtitle,
                        "Color": color, "ActionType": "ProductionPageSetup", "Width": width, "Height": height,
                        "Unit": "Millimeter", "HeightMode": mode, "GuidelineDistanceMm": guideline,
                        "MacroProject": "", "MacroName": "", "MacroFileName": "", "MinimumCorelVersion": 25})
    config = {"name": caption, "macroProjectName": "Politext", "generateVbaMacros": True,
              "minimumCorelVersion": 25, "columns": 4, "showDocumentInfo": True, "enableLiveUpdates": True,
              "showDocumentAudit": True, "presets": page}
    html = DOCKER_PAGE.replace("%s", json.dumps(config, ensure_ascii=False, separators=(",", ":")), 1)
    (folder / "Politext.html").write_bytes((b"\xef\xbb\xbf" if bom else b"") + html.replace("\n", newline).encode())
    studio = {"SchemaVersion": 4, "Name": caption, "FolderName": name, "Columns": 4,
              "CommandGuid": DOCKER_GUIDS["command"], "BrowserGuid": DOCKER_GUIDS["browser"],
              "DockerGuid": DOCKER_GUIDS["docker"], "GenerateVbaMacros": True, "MacroProjectName": "Politext",
              "Presets": project}
    (folder / "dockerstudio.json").write_bytes(json.dumps(studio, indent=2).replace("\n", "\r\n").encode())
    if macros:
        (folder / "Macros.bas").write_bytes(DOCKER_MACROS.replace("\n", "\r\n").encode())
    (folder / "Politext.gms").write_bytes(b"\xd0\xcf\x11\xe0compiled macros")
    return folder


@pytest.fixture
def docker_folder(tmp_path):
    return make_docker(tmp_path)


@pytest.fixture
def engine(models, keyboard, profile):
    return Engine(models, keyboard, profile)


class FakeScreen:
    """A text field plus a user at the keyboard, wired to a Controller.

    The user presses physical keys; what appears depends on the current
    layout, exactly like a real OS.  The controller edits the same text.
    """

    def __init__(self, keyboard: Keyboard, layout: str = EN, app: str = "notes"):
        self.kb = keyboard
        self.text = ""
        self.layout = layout
        self.app = app
        self.caps = False
        self.selection = ""
        self.notes: list[str] = []
        self.clock = 100.0
        self.held: set[str] = set()  # modifiers held as the OS sees them
        self.os_knows_mods = True  # like Windows; False: like an OS that cannot tell
        self.save_dialog = False  # a "Save As" / "Export" dialog is in front
        self.controller: Controller | None = None

    # -- Backend API -------------------------------------------------------
    def backspace(self, count):
        self.text = self.text[: max(0, len(self.text) - count)]

    def type_text(self, text):
        self.text += text

    def set_layout(self, lang):
        self.layout = lang
        return True

    def caps_lock_off(self):
        self.caps = False

    def copy_selection(self):
        return self.selection or None

    def restore_clipboard(self):
        pass

    def paste_text(self, text):
        if self.selection and self.text.endswith(self.selection):
            self.text = self.text[: -len(self.selection)]
        self.text += text
        self.selection = ""

    def notify(self, message):
        self.notes.append(message)

    def select_back(self, count):
        self.selection = self.text[-count:] if count else ""

    def in_save_dialog(self):
        return self.save_dialog

    # -- the user ------------------------------------------------------------
    def _event(self, kind, key, **kw):
        self.clock += 0.12
        if key in ("shift", "ctrl", "alt", "cmd"):
            (self.held.add if kind == "press" else self.held.discard)(key)
        if self.os_knows_mods:
            kw.setdefault("mods", frozenset(self.held))
        ev = KeyEvent(kind, key, layout=self.layout, app=self.app, time=self.clock, **kw)
        self.controller.handle(ev)

    def lose_releases(self):
        """The lock screen took the key releases: the OS holds nothing, Switcher was not told."""
        self.held.clear()

    def press(self, stroke: Stroke):
        char = self.kb.layouts[self.layout].char(stroke)
        if self.caps and char.isalpha():
            char = char.swapcase()
        self.text += char
        self._event("press", "char", char=char)

    def keys(self, physical: str):
        """Press keys named by their US QWERTY characters; ' ', '\\n', '\\b' are special."""
        for ch in physical:
            if ch == " ":
                self.space()
            elif ch == "\n":
                self.enter()
            elif ch == "\b":
                self.backspace_key()
            else:
                self.press(self.kb.layouts[EN].stroke(ch))

    def write(self, intended: str, lang: str | None = None):
        """Press the keys that would type ``intended`` in ``lang`` — whatever the current layout is."""
        for ch in intended:
            if ch == " ":
                self.space()
            elif ch == "\n":
                self.enter()
            else:
                src = lang or text_lang(ch) or self.layout
                stroke = self.kb.layouts[src].stroke(ch)
                assert stroke is not None, ch
                self.press(stroke)

    def space(self):
        self.text += " "
        self._event("press", "space")

    def enter(self):
        self.text += "\n"
        self._event("press", "enter")

    def backspace_key(self, times: int = 1):
        for _ in range(times):
            self.text = self.text[:-1]
            self._event("press", "backspace")

    def double_shift(self):
        for _ in range(2):
            self._event("press", "shift")
            self._event("release", "shift")

    def shift_letter(self, code: str):
        self._event("press", "shift")
        self.press(Stroke(code, True))
        self._event("release", "shift")

    def switch_layout(self, lang: str):
        """The user switches layout with the OS shortcut."""
        self.layout = lang
        self._event("press", "alt")
        self._event("press", "shift")
        self._event("release", "shift")
        self._event("release", "alt")

    def click(self):
        self._event("press", "mouse")


def _run_now(work, done):
    """Like Controller._thread_async, minus the thread: a failing job hands its exception to done."""
    try:
        result = work()
    except Exception as exc:
        result = exc
    done(result)


@pytest.fixture
def make_screen(models, keyboard, profile):
    def factory(config: Config | None = None, layout: str = EN, app: str = "notes", ai=None):
        config = config or Config()
        screen = FakeScreen(keyboard, layout, app)
        engine = Engine(models, keyboard, profile)
        engine.tuning.threshold = config.threshold
        learner = Learner(profile, models, keyboard, config.learning)
        controller = Controller(screen, engine, learner, config, keyboard, ai=ai,
                                run_async=_run_now)
        screen.controller = controller
        return screen

    return factory


def pytest_runtest_logreport(report):
    """On GitHub Actions a failed test is also an error annotation: readable without the full log."""
    if report.failed and os.environ.get("GITHUB_ACTIONS") == "true":
        text = str(report.longrepr)[-3500:].replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        sys.__stdout__.write(f"\n::error title={report.nodeid}::{text}\n")
