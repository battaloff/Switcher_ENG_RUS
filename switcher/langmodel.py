"""Base language knowledge: how English-like or Russian-like a word is.

Two sources, both built once from the ``wordfreq`` corpus statistics and
cached on disk:

* :class:`Lexicon` — every word seen in the corpus with its Zipf frequency
  (log10 of occurrences per billion words), stored as a sorted byte blob so
  ~700k Russian word forms take a few MB instead of a dict's ~60 MB.
* :class:`CharNgram` — a character 4-gram model (Witten-Bell smoothing) that
  scores words the lexicon has never seen: slang, names, new jargon.
  A word typed in the wrong layout ("ghbdtn") gets an extremely low score.
"""

from __future__ import annotations

import math
import pickle
import re
from array import array
from itertools import accumulate
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .layouts import EN, LANGS, RU

MODEL_VERSION = 3

ALPHABETS = {
    EN: "abcdefghijklmnopqrstuvwxyz'-",
    RU: "абвгдеёжзийклмнопрстуфхцчшщъыьэюя-",
}
_WORD_RE = {
    EN: re.compile(r"^[a-z]+(?:['-][a-z]+)*$"),
    RU: re.compile(r"^[а-яё]+(?:-[а-яё]+)*$"),
}
_ENCODING = {EN: "ascii", RU: "cp1251"}
_ZIPF_SCALE = 30  # zipf is stored as one byte: round(zipf * 30)


def is_word(word: str, lang: str) -> bool:
    return bool(_WORD_RE[lang].match(word))


class Lexicon:
    """Sorted, compact word -> Zipf frequency table."""

    def __init__(self, lang: str, items: Iterable[tuple[str, float]]):
        self.lang = lang
        enc = _ENCODING[lang]
        encoded = sorted((w.encode(enc), z) for w, z in items)
        self._blob = b"\n".join(w for w, _ in encoded)
        self._offsets = array("I")
        pos = 0
        for w, _ in encoded:
            self._offsets.append(pos)
            pos += len(w) + 1
        self._zipf = bytes(min(255, max(0, round(z * _ZIPF_SCALE))) for _, z in encoded)
        self._enc = enc

    def __len__(self) -> int:
        return len(self._offsets)

    def _word_at(self, i: int) -> bytes:
        start = self._offsets[i]
        end = self._offsets[i + 1] - 1 if i + 1 < len(self._offsets) else len(self._blob)
        return self._blob[start:end]

    def zipf(self, word: str) -> float | None:
        try:
            key = word.encode(self._enc)
        except UnicodeEncodeError:
            return None
        lo, hi = 0, len(self._offsets)
        while lo < hi:
            mid = (lo + hi) // 2
            if self._word_at(mid) < key:
                lo = mid + 1
            else:
                hi = mid
        if lo < len(self._offsets) and self._word_at(lo) == key:
            return self._zipf[lo] / _ZIPF_SCALE
        return None

    def __contains__(self, word: str) -> bool:
        return self.zipf(word) is not None

    def __getstate__(self):
        state = dict(self.__dict__)
        state.pop("_cum", None)  # rebuilt on first use
        return state

    def _bound(self, key: bytes, upper: bool) -> int:
        """First index whose word is >= key (upper: whose first len(key) bytes are > key)."""
        n = len(key)
        lo, hi = 0, len(self._offsets)
        while lo < hi:
            mid = (lo + hi) // 2
            word = self._word_at(mid)
            if (word[:n] <= key) if upper else (word < key):
                lo = mid + 1
            else:
                hi = mid
        return lo

    def prefix_zipf(self, prefix: str) -> float | None:
        """Zipf frequency of all words starting with ``prefix`` together ("при" ≈ 7, "ghb" → None)."""
        try:
            key = prefix.encode(self._enc)
        except UnicodeEncodeError:
            return None
        lo, hi = self._bound(key, False), self._bound(key, True)
        if lo >= hi:
            return None
        cum = self.__dict__.get("_cum")
        if cum is None:
            per_byte = [10 ** (b / _ZIPF_SCALE) for b in range(256)]
            cum = self._cum = array("d", accumulate((per_byte[b] for b in self._zipf), initial=0.0))
        mass = cum[hi] - cum[lo]
        return math.log10(mass) if mass > 0 else None


class CharNgram:
    """Character n-gram model with precomputed Witten-Bell log10 tables.

    For every context seen in training the full interpolated distribution is
    stored; an unseen context backs off to its longest seen suffix, which is
    exactly what Witten-Bell interpolation yields for zero counts.
    """

    BOS = "^"
    EOS = "$"
    OTHER = "?"

    def __init__(self, alphabet: str, order: int):
        self.alphabet = alphabet
        self.order = order
        self.symbols = alphabet + self.EOS + self.OTHER
        self._sym = {ch: i for i, ch in enumerate(self.symbols)}
        self._ctx: dict[str, int] = {"": 0}
        self._table = array("f")
        self.mean_logprob = -1.0  # average log10 P per symbol on training words

    @classmethod
    def train(cls, weighted_words: Iterable[tuple[str, float]], alphabet: str, order: int = 4) -> "CharNgram":
        model = cls(alphabet, order)
        V = len(model.symbols)
        counts: dict[str, dict[int, float]] = {}
        words = list(weighted_words)
        for word, weight in words:
            padded = cls.BOS * (order - 1) + word
            for i, ch in enumerate(word + cls.EOS):
                sym = model._sym.get(ch, V - 1)
                history = padded[i : i + order - 1]
                for k in range(order):
                    ctx = history[len(history) - k :] if k else ""
                    bucket = counts.setdefault(ctx, {})
                    bucket[sym] = bucket.get(sym, 0.0) + weight

        rows: dict[str, list[float]] = {}
        unigram = counts[""]
        total = sum(unigram.values())
        rows[""] = [(unigram.get(s, 0.0) + 1.0) / (total + V) for s in range(V)]
        for ctx in sorted(counts, key=len):
            if not ctx:
                continue
            bucket = counts[ctx]
            c_total = sum(bucket.values())
            types = len(bucket)
            lower = rows[ctx[1:]]
            denom = c_total + types
            rows[ctx] = [(bucket.get(s, 0.0) + types * lower[s]) / denom for s in range(V)]

        table = array("f")
        index: dict[str, int] = {}
        for i, (ctx, row) in enumerate(rows.items()):
            index[ctx] = i
            table.extend(math.log10(p) for p in row)
        model._ctx = index
        model._table = table

        sample = words[:: max(1, len(words) // 20000)]
        per_symbol = [model.logprob(w) / (len(w) + 1) for w, _ in sample]
        model.mean_logprob = sum(per_symbol) / len(per_symbol)
        return model

    def logprob(self, word: str, complete: bool = True) -> float:
        """log10 P(word), including the end-of-word symbol unless ``complete`` is False (a prefix)."""
        V = len(self.symbols)
        history = self.BOS * (self.order - 1)
        total = 0.0
        for ch in (word + self.EOS if complete else word):
            ctx = history
            while ctx not in self._ctx:
                ctx = ctx[1:]
            row = self._ctx[ctx]
            total += self._table[row * V + self._sym.get(ch, V - 1)]
            history = (history + ch)[1:]
        return total


@dataclass
class LanguageModel:
    lang: str
    lexicon: Lexicon
    ngram: CharNgram

    def zipf(self, word: str) -> float | None:
        return self.lexicon.zipf(word)

    def plausibility(self, word: str) -> float:
        """How typical the word's letters are for this language.

        0 is an average dictionary word; each unit below zero is roughly one
        order of magnitude less likely per letter.  Gibberish like "ghbdtn"
        lands around -1, real but unknown words stay near 0.
        """
        return self.ngram.logprob(word) / (len(word) + 1) - self.ngram.mean_logprob

    def prefix_plausibility(self, prefix: str) -> float:
        """Like :meth:`plausibility` for the first letters of a word still being typed."""
        return self.ngram.logprob(prefix, complete=False) / max(1, len(prefix)) - self.ngram.mean_logprob


class Models:
    """Both languages together, loaded from the cache or built from wordfreq."""

    def __init__(self, models: dict[str, LanguageModel]):
        self.by_lang = models

    def __getitem__(self, lang: str) -> LanguageModel:
        return self.by_lang[lang]

    @classmethod
    def build(cls, holdout: set[str] | None = None, min_zipf: float = 1.0) -> "Models":
        """Build from wordfreq. ``holdout`` words are left out (for evaluation)."""
        from wordfreq import get_frequency_dict

        models = {}
        for lang in LANGS:
            freqs = get_frequency_dict(lang, "large")
            items = []
            for word, freq in freqs.items():
                if not is_word(word, lang) or (holdout and word in holdout):
                    continue
                zipf = math.log10(freq * 1e9)
                if zipf >= min_zipf:
                    items.append((word, zipf))
            lexicon = Lexicon(lang, items)
            # Train letters on word types, mildly favouring frequent ones, so
            # rare morphology still counts but typos in the tail do not dominate.
            items.sort(key=lambda it: -it[1])
            weighted = [(w, 1.0 + max(0.0, z - 1.0)) for w, z in items[:250_000]]
            ngram = CharNgram.train(weighted, ALPHABETS[lang], order=4)
            models[lang] = LanguageModel(lang, lexicon, ngram)
        return cls(models)

    def save(self, path: Path) -> None:
        tmp = path.with_suffix(".tmp")
        with open(tmp, "wb") as f:
            pickle.dump({"version": MODEL_VERSION, "models": self.by_lang}, f, protocol=pickle.HIGHEST_PROTOCOL)
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> "Models | None":
        try:
            with open(path, "rb") as f:
                data = pickle.load(f)
        except (OSError, pickle.UnpicklingError, EOFError, AttributeError, ImportError):
            return None
        if not isinstance(data, dict) or data.get("version") != MODEL_VERSION:
            return None
        return cls(data["models"])


BUNDLED_NAME = "langmodel.pickle"


def default_models_path() -> Path:
    from .paths import cache_dir

    return cache_dir() / f"langmodel-v{MODEL_VERSION}.pickle"


def bundled_models_path() -> Path | None:
    """The prebuilt model shipped inside the Windows installer, if any."""
    import os
    import sys

    override = os.environ.get("SWITCHER_MODELS")
    if override and Path(override).exists():
        return Path(override)
    roots = [getattr(sys, "_MEIPASS", None)]
    if getattr(sys, "frozen", False):
        roots.append(str(Path(sys.executable).parent))
    for root in roots:
        if root and (Path(root) / BUNDLED_NAME).exists():
            return Path(root) / BUNDLED_NAME
    return None


_cached: Models | None = None


def load_models(path: Path | None = None, *, build_if_missing: bool = True) -> Models:
    """Load the models: the bundled copy, else the cache, else build them (~20 s, needs wordfreq)."""
    global _cached
    if _cached is not None and path is None:
        return _cached
    models = None
    if path is None:
        bundled = bundled_models_path()
        if bundled is not None:
            models = Models.load(bundled)
    target = path or default_models_path()
    if models is None:
        models = Models.load(target)
    if models is None:
        if not build_if_missing:
            raise FileNotFoundError(target)
        models = Models.build()
        models.save(target)
    if path is None:
        _cached = models
    return models
