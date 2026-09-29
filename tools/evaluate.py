"""Measure how often the engine converts correct words or misses wrong-layout ones.

    python tools/evaluate.py [--sweep]

Builds the language models with a random 3% of mid-frequency words held out,
then checks: frequency-weighted dictionary words (what people mostly type) and
the held-out words (never seen, like slang or new jargon), each typed in the
right layout (must be kept) and in the wrong one (must be converted).
"""

from __future__ import annotations

import argparse
import math
import random
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from switcher.engine import Engine, Tuning  # noqa: E402
from switcher.langmodel import Models, is_word  # noqa: E402
from switcher.layouts import DEFAULT_KEYBOARD, EN, LANGS, RU, other  # noqa: E402


def sample_sets(seed: int = 7):
    from wordfreq import get_frequency_dict

    rng = random.Random(seed)
    holdout, weighted = {}, {}
    for lang in LANGS:
        freqs = {w: f for w, f in get_frequency_dict(lang, "large").items() if is_word(w, lang)}
        mid = [w for w, f in freqs.items() if 1.5 <= math.log10(f * 1e9) <= 4.0]
        holdout[lang] = rng.sample(mid, 3000)
        top = sorted(freqs.items(), key=lambda kv: -kv[1])[:60000]
        words, probs = zip(*top)
        weighted[lang] = rng.choices(words, weights=probs, k=20000)
    return holdout, weighted


def run(engine: Engine, sets: dict[str, dict[str, list[str]]], verbose: bool = False):
    kb = engine.keyboard
    report = {}
    for set_name, by_lang in sets.items():
        fp = fn = total = 0
        fp_examples, fn_examples = [], []
        for lang, words in by_lang.items():
            for word in words:
                strokes = kb.strokes(word, lang)
                if strokes is None:
                    continue
                total += 1
                kept = engine.decide(strokes, lang)
                if kept.action == "convert":
                    fp += 1
                    fp_examples.append(f"{word}->{kept.text}")
                wrong = engine.decide(strokes, other(lang))
                if wrong.action != "convert":
                    fn += 1
                    fn_examples.append(f"{wrong.original}({word})")
        report[set_name] = (fp / total, fn / total)
        if verbose:
            print(f"{set_name}: n={total} ложные переключения={fp / total:.2%} пропуски={fn / total:.2%}")
            print("   FP:", ", ".join(fp_examples[:25]))
            print("   FN:", ", ".join(fn_examples[:25]))
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sweep", action="store_true")
    args = parser.parse_args()
    holdout, weighted = sample_sets()
    held = set(holdout[EN]) | set(holdout[RU])
    models = Models.build(holdout=held)
    sets = {"частые слова": weighted, "незнакомые слова": holdout}
    engine = Engine(models, DEFAULT_KEYBOARD)
    run(engine, sets, verbose=True)
    if args.sweep:
        base = Tuning()
        for threshold in (1.5, 2.0, 2.5):
            for slope in (2.0, 3.0, 4.0):
                for base_score in (0.5, 1.0):
                    engine.tuning = replace(base, threshold=threshold, oov_slope=slope, oov_base=base_score)
                    r = run(engine, sets)
                    cells = "  ".join(f"{k}: FP {v[0]:.2%} FN {v[1]:.2%}" for k, v in r.items())
                    print(f"thr={threshold} slope={slope} base={base_score}  {cells}")


if __name__ == "__main__":
    main()
