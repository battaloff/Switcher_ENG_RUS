"""Typo autocorrect: a word much rarer than a word one slip away gets replaced.

Candidates are the words one edit away: a letter replaced, added or dropped, or
two neighbours swapped.  Each edit costs what that slip is worth: a neighbouring
key, a sound-alike letter (а/о, е/и, з/с…) or swapped letters are cheap, an
arbitrary letter is expensive.  The typed word is replaced only when the best
candidate is common, much more frequent than what was typed after paying for
the edit, and clearly ahead of the runner-up.

Frequencies are Zipf values (log10 per billion words) from the same lexicon the
layout engine uses: "превет" 1.3 → "привет" 5.1, "teh" 3.0 → "the" 7.7, while
slang such as "щас" or "ваще" is either common enough or too far from any word.

Measured with tools/evaluate_spelling.py: 0.03% of frequent words typed right
get changed (rare real words; an undo protects them for good), 72% of typical
single-slip typos get fixed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .langmodel import ALPHABETS, Models
from .layouts import EN, RU, Keyboard, Stroke

_ROWS = ("`1234567890-=", "qwertyuiop[]", "asdfghjkl;'", "zxcvbnm,./")

# Letters people confuse by sound or spelling rules, not by finger slips.
_SOUND_ALIKE = {
    RU: ["ао", "еи", "ея", "иы", "еэ", "зс", "дт", "бп", "вф", "гк", "жш", "шщ", "ьъ", "цс", "юу"],
    EN: ["ae", "ai", "ei", "iy", "ou", "ao", "ck", "sc", "sz"],
}
_WORD = {EN: re.compile(r"[a-z]+"), RU: re.compile(r"[а-яё]+")}


@dataclass
class SpellTuning:
    min_length: int = 3
    short_extra: float = 1.0        # 3-letter words need a bigger lead
    min_candidate: float = 3.0      # never correct into a rare word
    known_max: float = 3.5          # a typed word this common is never touched
    unknown_score: float = 1.5      # what an unknown typed word is worth
    gap: float = 1.5                # candidate (minus edit cost) must beat the typed word by this much
    long_bonus: float = 0.15        # ...a little less per letter beyond 6: long words rarely have twins
    long_bonus_max: float = 0.6
    lead: float = 1.0               # ...and the runner-up by this much
    cost_swap: float = 0.5          # "teh" → "the"
    cost_near: float = 0.8          # neighbouring key
    cost_sound: float = 0.8         # "превет" → "привет"
    cost_double: float = 0.6        # "untill" → "until", "расчитать" → "рассчитать"
    cost_drop: float = 1.0          # "спсибо" → "спасибо"
    cost_extra: float = 1.0         # "приветт" → "привет"
    cost_other: float = 2.2         # any other letter
    cost_first: float = 1.0         # extra for adding or dropping the first letter: people rarely slip there
    cost_first_change: float = 0.4  # extra for changing the first letter
    cost_ending: float = 1.2        # extra for changing the last two letters: that is grammar, not a slip
    known_extra: float = 0.3        # more proof needed per Zipf unit the typed word has above 2


@dataclass
class Correction:
    word: str
    typed: str
    zipf: float
    gap: float
    edit: str


class Speller:
    def __init__(self, models: Models, keyboard: Keyboard, tuning: SpellTuning | None = None):
        self.models = models
        self.tuning = tuning or SpellTuning()
        self.letters = {lang: ALPHABETS[lang].replace("-", "").replace("'", "") for lang in (EN, RU)}
        self.near = {lang: self._neighbours(keyboard, lang) for lang in (EN, RU)}
        self.sound = {lang: {} for lang in (EN, RU)}
        for lang, pairs in _SOUND_ALIKE.items():
            for a, b in pairs:
                self.sound[lang].setdefault(a, set()).add(b)
                self.sound[lang].setdefault(b, set()).add(a)

    def _neighbours(self, keyboard: Keyboard, lang: str) -> dict[str, set[str]]:
        position = {ch: (r, c) for r, row in enumerate(_ROWS) for c, ch in enumerate(row)}
        near: dict[str, set[str]] = {}
        layout = keyboard.layouts[lang]
        for code, (r, c) in position.items():
            letter = layout.char(Stroke(code, False))
            if not letter.isalpha():
                continue
            for dr, dc in ((0, -1), (0, 1), (-1, 0), (-1, 1), (1, -1), (1, 0)):
                rr, cc = r + dr, c + dc
                if 0 <= rr < len(_ROWS) and 0 <= cc < len(_ROWS[rr]):
                    other = layout.char(Stroke(_ROWS[rr][cc], False))
                    if other.isalpha():
                        near.setdefault(letter, set()).add(other)
        return near

    def zipf(self, word: str, lang: str) -> float | None:
        lexicon = self.models[lang].lexicon
        z = lexicon.zipf(word)
        if lang == RU and "ё" in word:
            plain = lexicon.zipf(word.replace("ё", "е"))
            if plain is not None and (z is None or plain > z):
                z = plain
        return z

    def candidates(self, word: str, lang: str) -> dict[str, tuple[float, str]]:
        """Every word one edit away, with the cheapest way to get there."""
        t = self.tuning
        letters = self.letters[lang]
        near, sound = self.near[lang], self.sound[lang]
        found: dict[str, tuple[float, str]] = {}

        def add(candidate: str, cost: float, kind: str) -> None:
            if candidate != word and (candidate not in found or cost < found[candidate][0]):
                found[candidate] = (cost, kind)

        n = len(word)

        def place(i: int) -> float:
            """Extra cost of an edit at position i: the first letter and the ending are rarely slips."""
            return t.cost_ending if i >= n - 2 and n > 3 else 0.0

        for i in range(n):
            ch = word[i]
            rest = word[:i] + word[i + 1:]
            # a letter too many: doubled, or a neighbour's key hit on the way
            doubled = (i > 0 and word[i - 1] == ch) or (i + 1 < n and word[i + 1] == ch)
            if doubled:
                add(rest, t.cost_double, "extra")
            else:
                add(rest, t.cost_extra + (t.cost_first if i == 0 else place(i)), "extra")
            if i + 1 < n and word[i + 1] != ch:
                add(word[:i] + word[i + 1] + ch + word[i + 2:], t.cost_swap, "swap")
            for other in letters:
                if other == ch or {ch, other} == {"е", "ё"}:
                    continue
                if other in near.get(ch, ()):
                    cost, kind = t.cost_near, "near"
                elif other in sound.get(ch, ()):
                    cost, kind = t.cost_sound, "sound"
                else:
                    cost, kind = t.cost_other, "other"
                cost += t.cost_first_change if i == 0 else place(i)
                add(word[:i] + other + word[i + 1:], cost, kind)
        for i in range(n + 1):
            for other in letters:
                doubled = (i > 0 and word[i - 1] == other) or (i < n and word[i] == other)
                if doubled:
                    add(word[:i] + other + word[i:], t.cost_double, "missing")
                else:
                    extra = t.cost_first if i == 0 else (t.cost_ending if i == n and n > 3 else 0.0)
                    add(word[:i] + other + word[i:], t.cost_drop + extra, "missing")
        return found

    def suggest(self, word: str, lang: str) -> Correction | None:
        """The fix for ``word`` (lower case, letters only), or None to leave it."""
        t = self.tuning
        if len(word) < t.min_length or not _WORD[lang].fullmatch(word):
            return None
        typed = self.zipf(word, lang)
        if typed is not None and typed >= t.known_max:
            return None
        typed_score = max(typed, t.unknown_score) if typed is not None else t.unknown_score
        scored = []
        lexicon = self.models[lang].lexicon
        for candidate, (cost, kind) in self.candidates(word, lang).items():
            z = lexicon.zipf(candidate)  # exact: "фиолетовыё" is not "фиолетовые"
            if z is not None and z >= t.min_candidate:
                scored.append((z - cost, z, candidate, kind))
        if not scored:
            return None
        scored.sort(reverse=True)
        best, z, candidate, kind = scored[0]
        runner_up = scored[1][0] if len(scored) > 1 else float("-inf")
        need = t.gap + (t.short_extra if len(word) <= 3 else 0.0) \
            - min(t.long_bonus_max, t.long_bonus * max(0, len(word) - 6)) \
            + t.known_extra * max(0.0, typed_score - 2.0)
        if best - typed_score < need or best - runner_up < t.lead:
            return None
        return Correction(candidate, word, z, round(best - typed_score, 2), kind)
