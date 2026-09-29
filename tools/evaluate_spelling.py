"""Measure typo autocorrect: wrong changes to correct words vs typos fixed.

    python tools/evaluate_spelling.py [--sweep]

Correct words: frequency-weighted dictionary words (what people mostly type) and
held-out words the models never saw (slang, names, new jargon) — none of them
may change.  Typos: frequent words with one realistic slip (neighbouring key,
swapped letters, sound-alike letter, missing or doubled letter); counted as
fixed only when corrected back to exactly the intended word.
"""

from __future__ import annotations

import argparse
import random
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from evaluate import sample_sets  # noqa: E402

from switcher.langmodel import Models  # noqa: E402
from switcher.layouts import DEFAULT_KEYBOARD, LANGS  # noqa: E402
from switcher.speller import SpellTuning, Speller  # noqa: E402


def make_typo(speller: Speller, word: str, lang: str, rng: random.Random) -> str | None:
    i = rng.randrange(len(word))
    kind = rng.choices(["near", "swap", "sound", "drop", "double"], weights=[35, 20, 20, 15, 10])[0]
    if kind == "near":
        options = sorted(speller.near[lang].get(word[i], ()))
        return word[:i] + rng.choice(options) + word[i + 1:] if options else None
    if kind == "swap":
        return word[:i] + word[i + 1] + word[i] + word[i + 2:] if i + 1 < len(word) and word[i] != word[i + 1] \
            else None
    if kind == "sound":
        options = sorted(speller.sound[lang].get(word[i], ()))
        return word[:i] + rng.choice(options) + word[i + 1:] if options else None
    if kind == "drop":
        return word[:i] + word[i + 1:] if i > 0 else None
    return word[:i] + word[i] + word[i:]


def run(speller: Speller, sets, typos, verbose=False):
    for name, by_lang in sets.items():
        changed, total, examples = 0, 0, []
        for lang, words in by_lang.items():
            for word in words:
                if not word.isalpha():
                    continue
                total += 1
                fix = speller.suggest(word, lang)
                if fix:
                    changed += 1
                    examples.append(f"{word}→{fix.word}")
        print(f"{name:>9}: correct words changed {changed}/{total} = {100 * changed / total:.2f}%")
        if verbose and examples:
            print("           e.g.", ", ".join(sorted(set(examples))[:25]))
    fixed = wrong = total = 0
    wrong_examples, missed = [], []
    for lang, pairs in typos.items():
        for typo, word in pairs:
            total += 1
            fix = speller.suggest(typo, lang)
            if fix and fix.word == word:
                fixed += 1
            elif fix:
                wrong += 1
                wrong_examples.append(f"{typo}→{fix.word} (not {word})")
            else:
                missed.append(f"{typo}({word})")
    print(f"    typos: fixed {100 * fixed / total:.0f}%, changed into another word {100 * wrong / total:.1f}%, "
          f"left {100 * (total - fixed - wrong) / total:.0f}%")
    if verbose:
        print("           wrong:", ", ".join(wrong_examples[:20]))
        print("           left:", ", ".join(missed[:20]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sweep", action="store_true")
    args = parser.parse_args()
    holdout, weighted = sample_sets()
    held = {w for words in holdout.values() for w in words}
    print("building models without the held-out words…", flush=True)
    models = Models.build(holdout=held)
    speller = Speller(models, DEFAULT_KEYBOARD)
    rng = random.Random(11)
    typos = {}
    for lang in LANGS:
        pairs = []
        for word in weighted[lang][:6000]:
            if len(word) < 4 or not word.isalpha():
                continue
            typo = make_typo(speller, word, lang, rng)
            known = speller.zipf(typo, lang) if typo else None
            if typo and typo != word and (known is None or known < 3.0):  # real-word slips are out of scope
                pairs.append((typo, word))
        typos[lang] = pairs
    sets = {"frequent": {l: w[:10000] for l, w in weighted.items()}, "unseen": holdout}
    print("default tuning:")
    run(speller, sets, typos, verbose=True)
    if args.sweep:
        for known_extra, first_change, lead in [(0.3, 0.4, 1.0), (0.3, 0.2, 0.8), (0.45, 0.2, 0.8), (0.0, 0.4, 1.0)]:
            speller.tuning = replace(SpellTuning(), known_extra=known_extra, cost_first_change=first_change,
                                     lead=lead)
            print(f"known_extra={known_extra} first_change={first_change} lead={lead}")
            run(speller, sets, typos)


if __name__ == "__main__":
    main()
