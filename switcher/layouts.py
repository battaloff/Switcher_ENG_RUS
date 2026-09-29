"""Physical keys <-> characters for the English (QWERTY) and Russian (ЙЦУКЕН) layouts.

A key is identified by the character it produces on US QWERTY without Shift
(``"q"``, ``";"``, ``"/"`` ...).  A :class:`Stroke` is such a key plus the Shift
state, so the same stroke sequence can be rendered in either layout — that is
what "converting" a word means.
"""

from __future__ import annotations

import re
from typing import Iterable, NamedTuple

EN = "en"
RU = "ru"
LANGS = (EN, RU)


def other(lang: str) -> str:
    return RU if lang == EN else EN


class Stroke(NamedTuple):
    code: str
    shift: bool = False


_EN_LOWER = "`1234567890-=qwertyuiop[]\\asdfghjkl;'zxcvbnm,./"
_EN_UPPER = '~!@#$%^&*()_+QWERTYUIOP{}|ASDFGHJKL:"ZXCVBNM<>?'
# "Russian" on Windows/Linux and "Russian - PC" on macOS.
_RU_PC_LOWER = "ё1234567890-=йцукенгшщзхъ\\фывапролджэячсмитьбю."
_RU_PC_UPPER = 'Ё!"№;%:?*()_+ЙЦУКЕНГШЩЗХЪ/ФЫВАПРОЛДЖЭЯЧСМИТЬБЮ,'
# The default "Russian" layout on macOS.
_RU_MAC_LOWER = "]1234567890-=йцукенгшщзхъёфывапролджэячсмитьбю/"
_RU_MAC_UPPER = '[!"№%:,.;()_+ЙЦУКЕНГШЩЗХЪЁФЫВАПРОЛДЖЭЯЧСМИТЬБЮ?'

KEY_CODES = _EN_LOWER


def is_cyrillic(ch: str) -> bool:
    return "а" <= ch <= "я" or "А" <= ch <= "Я" or ch in "ёЁ"


def is_latin(ch: str) -> bool:
    return "a" <= ch <= "z" or "A" <= ch <= "Z"


def letter_lang(ch: str) -> str | None:
    if is_latin(ch):
        return EN
    if is_cyrillic(ch):
        return RU
    return None


def is_letter(ch: str, lang: str) -> bool:
    return is_latin(ch) if lang == EN else is_cyrillic(ch)


def text_lang(text: str) -> str | None:
    """Script of the letters in ``text``: ``"en"``, ``"ru"``, ``"mixed"`` or None."""
    langs = {letter_lang(ch) for ch in text} - {None}
    if not langs:
        return None
    return langs.pop() if len(langs) == 1 else "mixed"


class Layout:
    def __init__(self, lang: str, lower: str, upper: str):
        assert len(lower) == len(upper) == len(KEY_CODES)
        self.lang = lang
        self._chars: dict[Stroke, str] = {}
        self._strokes: dict[str, Stroke] = {}
        for code, lo, up in zip(KEY_CODES, lower, upper):
            for shift, ch in ((False, lo), (True, up)):
                stroke = Stroke(code, shift)
                self._chars[stroke] = ch
                self._strokes.setdefault(ch, stroke)

    def char(self, stroke: Stroke) -> str:
        return self._chars[stroke]

    def stroke(self, ch: str) -> Stroke | None:
        return self._strokes.get(ch)


class Keyboard:
    """The pair of layouts the switcher converts between."""

    def __init__(self, ru_variant: str = "pc"):
        if ru_variant not in ("pc", "mac"):
            raise ValueError(f"unknown Russian layout variant: {ru_variant!r}")
        self.ru_variant = ru_variant
        ru_lower, ru_upper = (
            (_RU_PC_LOWER, _RU_PC_UPPER) if ru_variant == "pc" else (_RU_MAC_LOWER, _RU_MAC_UPPER)
        )
        self.layouts = {
            EN: Layout(EN, _EN_LOWER, _EN_UPPER),
            RU: Layout(RU, ru_lower, ru_upper),
        }

    def text(self, strokes: Iterable[Stroke], lang: str) -> str:
        layout = self.layouts[lang]
        return "".join(layout.char(s) for s in strokes)

    def strokes(self, text: str, lang: str) -> list[Stroke] | None:
        """Keys that type ``text`` in ``lang``; None if some char is not on that layout."""
        layout = self.layouts[lang]
        result = []
        for ch in text:
            stroke = layout.stroke(ch)
            if stroke is None:
                return None
            result.append(stroke)
        return result

    def stroke_for_char(self, ch: str, layout_hint: str | None) -> tuple[Stroke, str] | None:
        """Which key produced ``ch`` and in which layout.

        Letters identify the layout by themselves; punctuation is resolved with
        the layout the OS reports (``layout_hint``).
        """
        lang = letter_lang(ch)
        order = [lang] if lang else ([layout_hint, other(layout_hint)] if layout_hint else [EN, RU])
        for candidate in order:
            stroke = self.layouts[candidate].stroke(ch)
            if stroke is not None:
                return stroke, candidate
        return None

    def convert(self, text: str, src: str, dst: str) -> str | None:
        """Re-type ``text`` (typed in ``src``) on ``dst``; whitespace is kept as is."""
        out = []
        for word in re.split(r"(\s+)", text):
            if not word or word.isspace():
                out.append(word)
                continue
            strokes = self.strokes(word, src)
            if strokes is None:
                return None
            out.append(harmonize_case(word, self.text(strokes, dst)))
        return "".join(out)

    def convert_mixed(self, text: str, dst: str) -> str:
        """Render every char of ``text`` in ``dst``, whatever layout it was typed in."""
        out = []
        for ch in text:
            found = self.stroke_for_char(ch, other(dst))
            out.append(self.layouts[dst].char(found[0]) if found else ch)
        return harmonize_case(text, "".join(out))


def harmonize_case(src: str, dst: str) -> str:
    """Keep CAPS words fully upper-case after conversion.

    Caps Lock does not affect punctuation keys on QWERTY, so "HELLO," typed
    with Caps Lock converts to "РУДДЩб"; the intended word is "РУДДЩБ".
    """
    letters = [ch for ch in src if ch.isalpha()]
    if len(letters) >= 2 and all(ch.isupper() for ch in letters):
        return dst.upper()
    return dst


def canonical_keys(strokes: Iterable[Stroke]) -> str:
    """Case-insensitive identity of a key sequence (used as a rule key)."""
    return "".join(s.code for s in strokes)


DEFAULT_KEYBOARD = Keyboard("pc")
