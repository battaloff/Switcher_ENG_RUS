from __future__ import annotations

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

    def paste_text(self, text):
        if self.selection and self.text.endswith(self.selection):
            self.text = self.text[: -len(self.selection)]
        self.text += text
        self.selection = ""

    def notify(self, message):
        self.notes.append(message)

    # -- the user ------------------------------------------------------------
    def _event(self, kind, key, **kw):
        self.clock += 0.12
        ev = KeyEvent(kind, key, layout=self.layout, app=self.app, time=self.clock, **kw)
        self.controller.handle(ev)

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


@pytest.fixture
def make_screen(models, keyboard, profile):
    def factory(config: Config | None = None, layout: str = EN, app: str = "notes", ai=None):
        config = config or Config()
        screen = FakeScreen(keyboard, layout, app)
        engine = Engine(models, keyboard, profile)
        engine.tuning.threshold = config.threshold
        learner = Learner(profile, models, keyboard, config.learning)
        controller = Controller(screen, engine, learner, config, keyboard, ai=ai,
                                run_async=lambda work, done: done(work()))
        screen.controller = controller
        return screen

    return factory
