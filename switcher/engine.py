"""Decides whether a finished word was typed in the wrong layout.

Every word is scored twice — as typed and as it would read on the other
layout — and converted only when the other reading wins by a clear margin.

Score of a reading (roughly "log10 frequency per billion words"):

* dictionary word: its Zipf frequency (``привет`` 5.1, ``hello`` 4.7);
* word from the user's personal vocabulary: grows with how often they use it;
* unknown word: a low base value adjusted by how natural its letters look,
  so ``пушнуть`` stays plausible while ``ghbdtn`` sinks far below zero.

On top of that come the user's layout rules (learned from their corrections),
the language of the neighbouring words and the app's usual language.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Protocol, Sequence

from .langmodel import Models
from .layouts import EN, RU, Keyboard, Stroke, canonical_keys, harmonize_case, is_letter, other


@dataclass
class Tuning:
    threshold: float = 2.0          # margin needed to convert
    short_extra: tuple[float, ...] = (2.7, 0.3, 0.3)  # extra margin for 1-, 2- and 3-letter words
    caps_extra: float = 1.0         # short ALL-CAPS tokens are often abbreviations
    oov_base: float = 1.0           # score of an unknown word with average-looking letters
    oov_slope: float = 3.0          # score lost per unit of letter implausibility...
    oov_length_power: float = 0.5   # ...scaled by len(word) ** power
    oov_ceiling: float = 1.4        # unknown words never beat real dictionary words
    short_oov_floor: float = 0.0    # unknown 1-3 letter tokens may be abbreviations
    lone_letter_cap: float = 3.0    # single letters that are not real words ("c", "ф")
    broken_score: float = -6.0      # reading with punctuation inside the word ("k,k")
    context_weight: float = 1.0     # pull towards the language of the phrase so far
    lookback_weight: float = 2.0    # pull from the next word, which was just converted
    app_weight: float = 0.8         # pull towards the app's usual language at phrase start
    manual_switch_extra: float = 1.5  # the user just switched layout by hand: trust them
    ambiguity_band: float = 1.5


# Single letters that are words on their own; corpora are full of the others
# ("c", "f", "ы") as list markers and variable names.
LONE_LETTER_WORDS = {EN: set("ai"), RU: set("авиксуоя")}

# How odd a punctuation char glued to a word is, per side.  Only matters for
# the English reading: those keys (; ' [ ] , . `) are letters in Russian, so
# ";t" or "b[" are really "же" and "их".
_AFFIX_PENALTY = {
    EN: (
        {",": 2.5, ";": 2.0, ".": 1.5, "`": 3.0, "~": 3.0, "]": 2.0, "}": 2.0, ">": 1.5, ":": 1.5, "'": 0.5, '"': 0.3},
        {"[": 2.0, "{": 2.0, "`": 3.0, "~": 3.0, "<": 1.5, "/": 1.5, "'": 0.3, '"': 0.3},
    ),
    RU: ({}, {}),
}


class ProfileView(Protocol):
    """What the engine needs from the learned user profile."""

    def layout_rule(self, keys: str, app: str) -> tuple[str, str] | None:
        """(lang, source) for a key sequence the user taught us, if any."""

    def personal_zipf(self, word: str, lang: str) -> float | None: ...

    def app_bias(self, app: str) -> float:
        """> 0 when the user mostly writes Russian in ``app``, < 0 for English."""

    def threshold_offset(self, app: str) -> float: ...


class NullProfile:
    def layout_rule(self, keys, app):
        return None

    def personal_zipf(self, word, lang):
        return None

    def app_bias(self, app):
        return 0.0

    def threshold_offset(self, app):
        return 0.0


@dataclass
class Context:
    app: str = ""
    prev_langs: Sequence[str] = ()
    next_lang: str | None = None     # set when re-checking a word after its neighbour was converted
    manual_switch: bool = False      # the user switched layout by hand right before this word
    extra_threshold: float = 0.0     # e.g. code editors and terminals


@dataclass
class Reading:
    lang: str
    text: str
    core: str
    core_start: int
    core_end: int
    score: float = 0.0
    source: str = ""  # "lexicon" | "personal" | "letters" | "broken" | "empty"

    @property
    def core_strokes_slice(self) -> slice:
        return slice(self.core_start, self.core_end)


@dataclass
class Decision:
    action: str  # "keep" | "convert" | "fix_case"
    typed_lang: str
    target_lang: str
    original: str
    text: str
    margin: float = 0.0
    threshold: float = 0.0
    reason: str = ""
    typed: Reading | None = None
    alt: Reading | None = None
    keys: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def changes_text(self) -> bool:
        return self.action != "keep"

    @property
    def ambiguous(self) -> bool:
        return self.reason == "model" and abs(self.margin - self.threshold) < 1.5


_INNER_OK = {EN: "-'", RU: "-"}
_CAMEL = re.compile(r"[a-z][A-Z]+[a-z]")


def split_core(text: str, lang: str) -> tuple[int, int]:
    start, end = 0, len(text)
    while start < end and not is_letter(text[start], lang):
        start += 1
    while end > start and not is_letter(text[end - 1], lang):
        end -= 1
    return start, end


def technical_reason(text: str, lang: str) -> str | None:
    """Why the token looks like code, a URL, a number... (never auto-convert those)."""
    if any(ch.isdigit() for ch in text):
        return "digits"
    if "@" in text or "://" in text or "_" in text:
        return "technical"
    inner = text[1:-1]
    if "/" in inner or "\\" in inner:
        return "path"
    if lang == EN and _CAMEL.search(text) and not re.fullmatch(r"[a-z][A-Z][a-z]+", text):
        return "camelCase"
    return None


class Engine:
    def __init__(self, models: Models, keyboard: Keyboard, profile: ProfileView | None = None,
                 tuning: Tuning | None = None):
        self.models = models
        self.keyboard = keyboard
        self.profile = profile or NullProfile()
        self.tuning = tuning or Tuning()

    # -- scoring -----------------------------------------------------------

    def read(self, text: str, lang: str) -> Reading:
        start, end = split_core(text, lang)
        core = text[start:end]
        reading = Reading(lang, text, core, start, end)
        if not core:
            reading.source = "empty"
            reading.score = float("-inf")
            return reading
        word = core.lower()
        inner_bad = [ch for ch in word if not is_letter(ch, lang) and ch not in _INNER_OK[lang]]
        if inner_bad:
            parts = [p for p in re.split(r"[^\w'-]+", word) if p]
            if set(inner_bad) == {"."} and parts:
                # abbreviations: "т.е.", "e.g."
                reading.score = min(self.word_score(p, lang)[0] for p in parts) - 1.0
                reading.source = "abbrev"
            else:
                reading.score = self.tuning.broken_score
                reading.source = "broken"
            return reading
        reading.score, reading.source = self.word_score(word, lang)
        reading.score -= self._affix_penalty(text[:start], text[end:], lang)
        return reading

    def word_score(self, word: str, lang: str) -> tuple[float, str]:
        t = self.tuning
        model = self.models[lang]
        zipf = model.zipf(word)
        if lang == RU and "ё" in word:
            plain = model.zipf(word.replace("ё", "е"))
            if plain is not None and (zipf is None or plain > zipf):
                zipf = plain
        personal = self.profile.personal_zipf(word, lang)
        if zipf is not None:
            score, source = zipf, "lexicon"
            if len(word) == 1 and word not in LONE_LETTER_WORDS[lang]:
                score = min(score, t.lone_letter_cap)
        else:
            letters = model.plausibility(word)
            length = max(1, len(word)) ** t.oov_length_power
            score = min(t.oov_ceiling, t.oov_base + t.oov_slope * letters * length)
            if len(word) <= 3:
                score = max(score, t.short_oov_floor)
            source = "letters"
        if personal is not None and personal > score:
            score, source = personal, "personal"
        return score, source

    @staticmethod
    def _affix_penalty(prefix: str, suffix: str, lang: str) -> float:
        before, after = _AFFIX_PENALTY[lang]
        penalty = before.get(prefix[-1:], 0.0) + after.get(suffix[:1], 0.0)
        return penalty

    # -- decision ----------------------------------------------------------

    def decide(self, strokes: Sequence[Stroke], typed_lang: str, ctx: Context | None = None,
               typed_text: str | None = None) -> Decision:
        ctx = ctx or Context()
        t = self.tuning
        alt_lang = other(typed_lang)
        original = typed_text if typed_text is not None else self.keyboard.text(strokes, typed_lang)
        alt_text = self.keyboard.text(strokes, alt_lang)
        alt_text = harmonize_case(original, alt_text)
        keys = canonical_keys(strokes)
        decision = Decision("keep", typed_lang, typed_lang, original, original, keys=keys)

        caps = self._caps_lock_fix(original, typed_lang)
        typed = self.read(original, typed_lang)
        alt = self.read(alt_text, alt_lang)
        decision.typed, decision.alt = typed, alt

        if typed.source == "empty" or alt.source == "empty":
            decision.reason = "guard:punctuation"
            return self._maybe_caps(decision, caps)

        # Rules taught by the user win over everything else.
        for reading in (typed, alt):
            rule = self.profile.layout_rule(canonical_keys(strokes[reading.core_strokes_slice]), ctx.app)
            if rule:
                lang, source = rule
                decision.reason = "rule"
                decision.notes.append(f"rule:{source}")
                if lang == alt_lang:
                    decision.action = "convert"
                    decision.target_lang = alt_lang
                    decision.text = alt_text
                return decision if decision.action == "convert" else self._maybe_caps(decision, caps)

        why = technical_reason(original, typed_lang)
        if why:
            decision.reason = f"guard:{why}"
            return decision

        threshold = t.threshold + self.profile.threshold_offset(ctx.app) + ctx.extra_threshold
        n = len(alt.core)
        if n <= len(t.short_extra):
            threshold += t.short_extra[n - 1]
        letters = [ch for ch in typed.core if ch.isalpha()]
        if 2 <= len(letters) <= 5 and all(ch.isupper() for ch in letters):
            threshold += t.caps_extra
        if ctx.manual_switch:
            threshold += t.manual_switch_extra

        margin = alt.score - typed.score
        margin += self._context_pull(ctx, alt_lang) - self._context_pull(ctx, typed_lang)

        decision.margin = round(margin, 3)
        decision.threshold = round(threshold, 3)
        decision.reason = "model"
        if margin > threshold:
            decision.action = "convert"
            decision.target_lang = alt_lang
            decision.text = alt_text
            return decision
        return self._maybe_caps(decision, caps)

    def _context_pull(self, ctx: Context, lang: str) -> float:
        t = self.tuning
        pull = 0.0
        previous = list(ctx.prev_langs[-3:])
        if previous:
            pull += t.context_weight * sum(1 for l in previous if l == lang) / len(previous)
        if ctx.next_lang == lang:
            pull += t.lookback_weight
        if not previous and not ctx.next_lang:
            bias = self.profile.app_bias(ctx.app)  # >0 → Russian
            pull += t.app_weight * (bias if lang == RU else -bias) / 2
        return pull

    # -- cAPS lOCK ---------------------------------------------------------

    @staticmethod
    def _caps_lock_fix(text: str, lang: str) -> str | None:
        letters = [ch for ch in text if ch.isalpha()]
        if len(letters) < 3 or not letters[0].islower():
            return None
        if all(ch.isupper() for ch in letters[1:]):
            return text.swapcase()
        return None

    @staticmethod
    def _maybe_caps(decision: Decision, caps: str | None) -> Decision:
        if caps and decision.action == "keep":
            decision.action = "fix_case"
            decision.text = caps
            decision.notes.append("caps-lock")
        return decision


def explain(decision: Decision) -> str:
    """Human-readable breakdown (used by ``switcher explain``)."""
    lines = []
    for label, reading in (("как набрано", decision.typed), ("в другой раскладке", decision.alt)):
        if reading is None:
            continue
        score = "−∞" if math.isinf(reading.score) else f"{reading.score:+.2f}"
        lines.append(f"  {label:>18}: {reading.text!r:<16} [{reading.lang}] оценка {score} ({reading.source})")
    verdict = {"keep": "оставить", "convert": "переключить", "fix_case": "исправить регистр"}[decision.action]
    lines.append(f"  решение: {verdict} → {decision.text!r}; причина: {decision.reason}"
                 + (f"; перевес {decision.margin:+.2f} при пороге {decision.threshold:.2f}"
                    if decision.reason == "model" else ""))
    return "\n".join(lines)
