"""Turns what the user does into knowledge: rules, personal words, thresholds.

Signals, from strongest to weakest:

* **undo** — the user reverted an automatic switch (hotkey, or erased the word
  and retyped the same keys in the old layout).  The key sequence gets a rule
  "keep it as typed" and switching in that app becomes a bit more careful.
* **manual** — the user converted a word we left alone (hotkey, retype in the
  other layout, or the AI phrase fix).  Rule "always convert"; switching in
  that app becomes a bit bolder.
* **typo fix** — the user erased a word and typed a slightly different one in
  the same layout.  After the same fix repeats, it becomes a replace rule.
* **commit** — every finished word updates per-app language statistics and,
  for words the dictionary does not know well, the personal vocabulary.
"""

from __future__ import annotations

from typing import Callable

from .config import Learning
from .engine import split_core
from .langmodel import Models, is_word
from .layouts import Keyboard, Stroke, canonical_keys
from .profile import Profile


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def core_of(text: str, lang: str) -> tuple[int, int, str]:
    start, end = split_core(text, lang)
    return start, end, text[start:end]


class Learner:
    def __init__(self, profile: Profile, models: Models, keyboard: Keyboard, config: Learning | None = None,
                 on_feedback: Callable[[], None] | None = None):
        self.profile = profile
        self.models = models
        self.keyboard = keyboard
        self.config = config or Learning()
        self.on_feedback = on_feedback

    # -- helpers -----------------------------------------------------------

    def core_keys(self, strokes: list[Stroke], text: str, lang: str) -> str:
        start, end, _ = core_of(text, lang)
        if len(strokes) != len(text):
            derived = self.keyboard.strokes(text, lang)
            strokes = derived if derived is not None else strokes
        return canonical_keys(strokes[start:end])

    def _personal_candidate(self, word: str, lang: str) -> bool:
        if len(word) < 2 or not is_word(word, lang):
            return False
        model = self.models[lang]
        zipf = model.zipf(word)
        if zipf is not None:
            return zipf < 3.0
        return model.plausibility(word) > -0.6

    def _feedback(self) -> None:
        if self.on_feedback:
            self.on_feedback()

    # -- signals -----------------------------------------------------------

    def committed(self, *, app: str, lang: str, text: str, decision=None, changed_from: str | None = None) -> None:
        """A word was finished and (maybe after our change) left on screen."""
        if not self.config.enabled or lang not in ("en", "ru"):
            return
        self.profile.count_word(app, lang)
        _, _, core = core_of(text, lang)
        word = core.lower()
        if self._personal_candidate(word, lang):
            self.profile.bump_vocab(lang, word)
        if decision is None:
            return
        if decision.action == "convert" and changed_from is not None:
            self.profile.log_event("auto", app=app, keys=decision.keys, typed_lang=decision.typed_lang,
                                   final_lang=lang, typed_text=decision.original, final_text=text,
                                   margin=decision.margin, detail={"reason": decision.reason})
        elif decision.ambiguous:
            self.profile.log_event("ambiguous", app=app, keys=decision.keys, typed_lang=decision.typed_lang,
                                   final_lang=lang, typed_text=decision.original,
                                   final_text=decision.alt.text if decision.alt else "",
                                   margin=decision.margin, detail={"threshold": decision.threshold})

    def undo(self, *, app: str, strokes: list[Stroke], typed_lang: str, typed_text: str, converted_lang: str,
             converted_text: str, reason: str = "model", via: str = "hotkey") -> None:
        """We switched a word the user wanted as typed."""
        if not self.config.enabled:
            return
        keys = self.core_keys(strokes, typed_text, typed_lang)
        self.profile.log_event("undo", app=app, keys=keys, typed_lang=typed_lang, final_lang=typed_lang,
                               typed_text=typed_text, final_text=typed_text,
                               detail={"was": converted_text, "reason": reason, "via": via})
        if keys:
            self.profile.add_rule("layout", keys, typed_lang, source="learned",
                                  note=f"не переключать «{typed_text}» в «{converted_text}»")
        if reason == "model":
            self.profile.adjust_threshold(app, +0.15)
        _, _, wrong = core_of(converted_text, converted_lang)
        self.profile.drop_vocab(converted_lang, wrong.lower())
        _, _, right = core_of(typed_text, typed_lang)
        if self._personal_candidate(right.lower(), typed_lang):
            self.profile.bump_vocab(typed_lang, right.lower(), 2)
        self._feedback()

    def manual(self, *, app: str, strokes: list[Stroke], typed_lang: str, typed_text: str, target_lang: str,
               target_text: str, margin: float | None = None, via: str = "hotkey") -> tuple[str, str] | None:
        """The user converted a word we left alone. Returns the rule key it created."""
        if not self.config.enabled:
            return None
        keys = self.core_keys(strokes, target_text, target_lang)
        self.profile.log_event("manual", app=app, keys=keys, typed_lang=typed_lang, final_lang=target_lang,
                               typed_text=typed_text, final_text=target_text, margin=margin,
                               detail={"via": via})
        rule = None
        if keys:
            rule = self.profile.add_rule("layout", keys, target_lang, source="learned",
                                         note=f"«{typed_text}» → «{target_text}» ({via})")
        if margin is not None and margin > 0:
            # the model already leaned that way — it was too timid here
            self.profile.adjust_threshold(app, -0.1)
        _, _, wrong = core_of(typed_text, typed_lang)
        self.profile.drop_vocab(typed_lang, wrong.lower())
        _, _, right = core_of(target_text, target_lang)
        if self._personal_candidate(right.lower(), target_lang):
            self.profile.bump_vocab(target_lang, right.lower(), 2)
        self._feedback()
        return (keys, "") if rule else None

    def retract(self, rule_key: tuple[str, str] | None) -> None:
        """The user immediately reverted their own manual conversion."""
        if rule_key:
            rule = self.profile.get_rule("layout", rule_key[0], rule_key[1])
            if rule and rule.source == "learned" and rule.hits <= 1:
                self.profile.remove_rule("layout", rule_key[0], rule_key[1])

    def prefix_fix(self, *, app: str, typed_text: str, typed_lang: str, target_lang: str) -> None:
        """The user noticed the wrong layout after a few letters and retyped."""
        if not self.config.enabled:
            return
        self.profile.log_event("prefix_fix", app=app, typed_lang=typed_lang, final_lang=target_lang,
                               typed_text=typed_text)

    def typo_fix(self, *, app: str, wrong: str, right: str, lang: str) -> bool:
        """The user replaced a finished word with a close variant. Returns True if a rule was made."""
        if not (self.config.enabled and self.config.typo_rules):
            return False
        wrong, right = wrong.lower(), right.lower()
        if len(right) < 3 or not is_word(right, lang) or not is_word(wrong, lang):
            return False
        distance = levenshtein(wrong, right)
        if not 1 <= distance <= max(1, min(2, len(right) // 3)):
            return False
        if self.models[lang].zipf(wrong) is not None and (self.models[lang].zipf(wrong) or 0) >= 3.0:
            return False  # both are real words: a change of mind, not a typo
        self.profile.log_event("typo_fix", app=app, typed_lang=lang, final_lang=lang, typed_text=wrong,
                               final_text=right)
        made = False
        if self.profile.count_events("typo_fix", wrong, right) >= self.config.typo_min_repeats:
            made = self.profile.add_rule("replace", wrong, right, source="learned",
                                         note="вы несколько раз исправляли это вручную") is not None
        self._feedback()
        return made

    def replace_applied(self, *, app: str, wrong: str, right: str) -> None:
        self.profile.rule_used("replace", wrong.lower(), app)
        self.profile.log_event("replace", app=app, typed_text=wrong, final_text=right)

    def replace_undone(self, *, app: str, wrong: str, right: str) -> None:
        rule = self.profile.replace_rule(wrong.lower(), app)
        if rule:
            self.profile.remove_rule("replace", rule.pattern, rule.app)
        self.profile.log_event("replace_undo", app=app, typed_text=wrong, final_text=right)
        self._feedback()

    def rule_overridden(self, *, app: str, keys: str) -> None:
        self.profile.rule_used("layout", keys, app, ok=False)
