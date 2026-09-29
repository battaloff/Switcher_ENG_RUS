"""Measure the early (Punto-style) layout switch: false triggers vs how early it catches.

    python tools/evaluate_early.py [--sweep]

Uses the same samples as tools/evaluate.py: frequency-weighted dictionary words
(what people mostly type) and held-out words the models never saw (slang, new
jargon).  Typed in the right layout a word must never switch mid-way; typed in
the wrong one it should switch after as few letters as possible.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from evaluate import sample_sets  # noqa: E402

from switcher.engine import Engine, Tuning, decide_prefix  # noqa: E402
from switcher.langmodel import Models  # noqa: E402
from switcher.layouts import DEFAULT_KEYBOARD, other  # noqa: E402


def first_switch(engine: Engine, strokes, typed_lang: str) -> int | None:
    for n in range(engine.tuning.early_min_letters, len(strokes) + 1):
        if decide_prefix(engine, strokes[:n], typed_lang).switch:
            return n
    return None


def run(engine: Engine, sets, verbose: bool = False) -> dict:
    kb = engine.keyboard
    report = {}
    for set_name, by_lang in sets.items():
        stats = {"words": 0, "false": 0, "hit3": 0, "hit4": 0, "hit5": 0, "hit": 0, "long": 0}
        examples = []
        for lang, words in by_lang.items():
            for word in words:
                strokes = kb.strokes(word, lang)
                if strokes is None:
                    continue
                stats["words"] += 1
                if first_switch(engine, strokes, lang) is not None:
                    stats["false"] += 1
                    examples.append(word)
                if len(strokes) < 3:
                    continue
                stats["long"] += 1
                n = first_switch(engine, strokes, other(lang))
                if n is not None:
                    stats["hit"] += 1
                    stats["hit3"] += n <= 3
                    stats["hit4"] += n <= 4
                    stats["hit5"] += n <= 5
        words, long_ = stats["words"], max(1, stats["long"])
        report[set_name] = stats
        print(f"{set_name:>9}: false switches {stats['false']}/{words} = {100 * stats['false'] / words:.2f}%   "
              f"wrong layout caught by 3 letters {100 * stats['hit3'] / long_:.0f}%, by 4 "
              f"{100 * stats['hit4'] / long_:.0f}%, by 5 {100 * stats['hit5'] / long_:.0f}%, "
              f"before the end {100 * stats['hit'] / long_:.0f}%")
        if verbose and examples:
            print("           e.g.", ", ".join(sorted(set(examples))[:30]))
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sweep", action="store_true")
    args = parser.parse_args()
    holdout, weighted = sample_sets()
    held = {w for words in holdout.values() for w in words}
    print("building models without the held-out words…", flush=True)
    models = Models.build(holdout=held)
    sets = {"frequent": weighted, "unseen": holdout}
    engine = Engine(models, DEFAULT_KEYBOARD)
    print("default tuning:")
    run(engine, sets, verbose=True)
    if args.sweep:
        for alt_min, typed_max, margin, plaus in [
            ((5.0, 4.0, 3.5), (1.0, 2.0, 2.0), (4.5, 3.0, 2.5), -0.5),
            ((5.0, 4.0, 3.5), (1.0, 2.0, 2.0), (4.5, 3.0, 2.5), -0.6),
            ((5.0, 4.2, 3.8), (1.0, 1.8, 1.8), (4.5, 3.2, 2.8), -0.5),
        ]:
            engine.tuning = replace(Tuning(), early_alt_min=alt_min, early_typed_max=typed_max,
                                    early_margin=margin, early_plaus_max=plaus)
            print(f"alt≥{alt_min} typed≤{typed_max} margin≥{margin} plaus≤{plaus}")
            run(engine, sets)


if __name__ == "__main__":
    main()
