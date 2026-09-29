"""Follows the keystrokes, fixes words, and notices how the user corrects us.

The controller keeps a model of the text right before the cursor: the word
being typed and the finished words of the current phrase.  Anything that may
move the cursor (mouse click, arrows, shortcuts, another window) resets it,
so we only ever edit text we are sure about.

All methods run on one thread (see :mod:`switcher.runtime`); the backend's
listener just queues events.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

from .config import Config
from .engine import Context, Decision, Engine
from .layouts import EN, RU, Keyboard, Stroke, canonical_keys, letter_lang, other, text_lang
from .learner import Learner, core_of, levenshtein

log = logging.getLogger(__name__)

MODIFIERS = {"shift", "ctrl", "alt", "cmd"}
DELIMITERS = {"space": " ", "tab": "\t", "enter": "\n"}


@dataclass
class KeyEvent:
    kind: str                  # "press" | "release"
    key: str                   # "char", a special key name, or "mouse"
    char: str | None = None    # what the key typed
    code: str | None = None    # physical key (US QWERTY unshifted char) when the OS tells us
    shift: bool = False
    layout: str | None = None  # the OS layout at the moment of the event, if known
    app: str = ""
    time: float = 0.0


class Backend(Protocol):
    def backspace(self, count: int) -> None: ...
    def type_text(self, text: str) -> None: ...
    def set_layout(self, lang: str) -> bool: ...
    def caps_lock_off(self) -> None: ...
    def copy_selection(self) -> str | None: ...
    def paste_text(self, text: str) -> None: ...
    def notify(self, message: str) -> None: ...


@dataclass
class Token:
    strokes: list[Stroke]
    chars: list[str]
    typed_lang: str
    mixed: bool = False
    has_letters: bool = False
    reopened_from: "Token | None" = None
    prefix_hint: tuple[list[Stroke], str, str] | None = None  # (strokes, lang, text) erased just before
    manual_from: str | None = None       # converted mid-word by the hotkey, from this language
    # filled in on commit
    text: str = ""
    lang: str = ""
    delim: str = ""
    decision: Decision | None = None
    original_text: str = ""
    change: str = ""                     # "convert" | "replace" | "fix_case" | "manual" | ""
    group: list["Token"] = field(default_factory=list)  # earlier words converted together (look-back)
    retyped: bool = False

    @property
    def typed_text(self) -> str:
        return "".join(self.chars)


class Controller:
    def __init__(self, backend: Backend, engine: Engine, learner: Learner, config: Config, keyboard: Keyboard,
                 ai=None, run_async: Callable[[Callable[[], object], Callable[[object], None]], None] | None = None):
        self.backend = backend
        self.engine = engine
        self.learner = learner
        self.config = config
        self.keyboard = keyboard
        self.ai = ai
        self._run_async = run_async or self._thread_async
        self.post: Callable[[Callable[[], None]], None] = lambda fn: fn()  # replaced by the runtime
        self.enabled = config.enabled
        self.layout: str | None = None
        self.app = ""
        self.cur: Token | None = None
        self.history: list[Token] = []
        self.erased: Token | None = None
        self.erased_prefix: tuple[list[Stroke], str, str] | None = None
        self.undo_target: Token | None = None
        self.manual_target: tuple[Token, str, tuple[str, str] | None, float] | None = None
        self.mods: set[str] = set()
        self._shift_down_at = 0.0
        self._shift_clean = False
        self._last_shift_tap = 0.0
        self._we_switched_at = 0.0
        self._manual_switch_at = 0.0
        self._generation = 0  # bumps on every change of the tracked text (for async AI results)
        self._now = 0.0
        self.hotkeys = {name: parse_hotkey(spec) for name, spec in vars(config.hotkeys).items() if spec}

    # -- entry point -------------------------------------------------------

    def handle(self, ev: KeyEvent) -> None:
        now = self._now = ev.time or time.monotonic()
        if ev.app != self.app:
            if self.app:
                self.reset("focus")
            self.app = ev.app
        if ev.layout and ev.layout != self.layout:
            if self.layout and now - self._we_switched_at > 1.0:
                self._manual_switch_at = now
            self.layout = ev.layout

        if ev.kind == "release":
            self._on_release(ev, now)
            return
        if ev.key == "mouse":
            self.reset("mouse")
            return
        if ev.key in MODIFIERS:
            self.mods.add(ev.key)
            if ev.key == "shift" and self.mods == {"shift"}:
                self._shift_clean = True
                self._shift_down_at = now
            else:
                self._shift_clean = False
            return
        self._shift_clean = False

        name = self._match_hotkey(ev)
        if name:
            self.run_hotkey(name)
            return
        if ev.key == "space" and self.mods & {"ctrl", "cmd"}:
            return  # the usual layout-switch shortcut on macOS: the cursor does not move
        if self.mods & {"ctrl", "cmd"} or ("alt" in self.mods and not ev.char):
            self.reset("shortcut")
            return
        if ev.key == "char":
            self._on_char(ev, now)
        elif ev.key in DELIMITERS:
            self._on_delimiter(ev.key)
        elif ev.key == "backspace":
            self._on_backspace()
        elif ev.key in ("caps_lock", "num_lock", "insert", "fn"):
            pass
        else:
            self.reset(ev.key)

    def _on_release(self, ev: KeyEvent, now: float) -> None:
        if ev.key in MODIFIERS:
            self.mods.discard(ev.key)
        if ev.key == "shift" and self._shift_clean and now - self._shift_down_at < 0.35:
            self._shift_clean = False
            if now - self._last_shift_tap < 0.45:
                self._last_shift_tap = 0.0
                if self.hotkeys.get("convert_last") == "double_shift":
                    self.run_hotkey("convert_last")
            else:
                self._last_shift_tap = now
        elif ev.key == "shift":
            self._shift_clean = False

    def reset(self, reason: str = "") -> None:
        self.cur = None
        self.history.clear()
        self.erased = None
        self.erased_prefix = None
        self.undo_target = None
        self.manual_target = None
        self._generation += 1

    # -- typing --------------------------------------------------------------

    def _stroke_for(self, ev: KeyEvent) -> tuple[Stroke, str] | None:
        if ev.code:
            lang = letter_lang(ev.char or "") or self.layout or EN
            return Stroke(ev.code, ev.shift), lang
        if ev.char:
            return self.keyboard.stroke_for_char(ev.char, self.layout)
        return None

    def _on_char(self, ev: KeyEvent, now: float) -> None:
        self._generation += 1
        self.undo_target = None
        found = self._stroke_for(ev)
        if found is None:
            # a character neither layout types (emoji, §...): end the word untouched
            if self.cur:
                self._commit("", allow_change=False)
            self.history.clear()
            return
        stroke, lang = found
        char = ev.char or self.keyboard.layouts[lang].char(stroke)
        letter = letter_lang(char)
        if letter:
            self.layout = letter
        if self.cur is None:
            self.cur = Token([], [], lang)
            if self.erased_prefix and self.erased_prefix[1] != lang:
                self.cur.prefix_hint = self.erased_prefix
            self.erased_prefix = None
        cur = self.cur
        if letter:
            if not cur.has_letters and letter != cur.typed_lang:
                # the word started with punctuation read in the wrong layout
                cur.typed_lang = letter
                cur.chars = [self.keyboard.layouts[letter].char(s) for s in cur.strokes]
            elif letter != cur.typed_lang:
                cur.mixed = True
            cur.has_letters = True
        cur.strokes.append(stroke)
        cur.chars.append(char)

    def _on_backspace(self) -> None:
        self._generation += 1
        self.undo_target = None
        self.manual_target = None
        cur = self.cur
        if cur and cur.strokes:
            snapshot = (list(cur.strokes), cur.typed_lang, cur.typed_text)
            cur.strokes.pop()
            cur.chars.pop()
            if not cur.strokes:
                if cur.reopened_from is not None:
                    self.erased = cur.reopened_from
                else:
                    self.erased_prefix = snapshot
                self.cur = None
            return
        if self.history:
            last = self.history[-1]
            if last.delim:
                last.delim = last.delim[:-1]
                return
            strokes = self.keyboard.strokes(last.text, last.lang) if last.lang in (EN, RU) else None
            if strokes is None:
                self.reset("backspace")
                return
            self.history.pop()
            tok = Token(strokes, list(last.text), last.lang, has_letters=True, reopened_from=last)
            self.cur = tok
            self._on_backspace()
            return
        self.reset("backspace")

    def _on_delimiter(self, key: str) -> None:
        self._generation += 1
        ch = DELIMITERS[key]
        if self.cur and self.cur.strokes:
            self._commit(ch, allow_change=key != "enter" or self.config.convert_on_enter)
        elif self.history:
            self.history[-1].delim += ch
            self.undo_target = None
        if key == "enter":
            last = self.history[-1] if self.history else None
            self.history.clear()
            self.erased = None
            self.erased_prefix = None
            if last is not None and self.undo_target is last and self.config.convert_on_enter:
                self.history.append(last)

    # -- finishing a word ----------------------------------------------------

    def _context(self, prev: list[Token]) -> Context:
        app = self.app.lower()
        careful = any(name in app for name in self.config.careful_apps)
        return Context(
            app=self.app,
            prev_langs=[t.lang for t in prev[-3:] if t.lang in (EN, RU)],
            manual_switch=self._now - self._manual_switch_at < 3.0,
            extra_threshold=self.config.careful_extra if careful else 0.0,
        )

    def _excluded(self) -> bool:
        app = self.app.lower()
        return any(name in app for name in self.config.excluded_apps)

    def _commit(self, delim: str, allow_change: bool = True) -> Token:
        tok = self.cur
        assert tok is not None
        self.cur = None
        tok.text = tok.typed_text
        tok.lang = tok.typed_lang if not tok.mixed else (text_lang(tok.text) or tok.typed_lang)
        tok.delim = delim
        if self._excluded():
            # password managers & co: do not keep, judge or learn anything typed there
            self.history.clear()
            self.erased = None
            return tok

        self._detect_retype(tok)
        if tok.prefix_hint:
            self._detect_prefix_fix(tok)

        can_change = (allow_change and self.enabled and self.config.auto_switch
                      and not tok.mixed and not tok.retyped and tok.manual_from is None)

        if tok.manual_from is not None:
            rule = self.learner.manual(app=self.app, strokes=tok.strokes, typed_lang=tok.manual_from,
                                       typed_text=self.keyboard.text(tok.strokes, tok.manual_from),
                                       target_lang=tok.typed_lang, target_text=tok.text, via="hotkey")
            self.manual_target = (tok, tok.manual_from, rule, self._now)
            tok.change = "manual"
        elif not tok.mixed and tok.has_letters:
            tok.decision = self.engine.decide(tok.strokes, tok.typed_lang, self._context(self.history),
                                              typed_text=tok.text)
            if can_change:
                self._apply_decision(tok)
            if tok.change == "" and can_change and self.config.learning.typo_rules:
                self._apply_replace_rule(tok)

        self.learner.committed(app=self.app, lang=tok.lang, text=tok.text, decision=tok.decision,
                               changed_from=tok.original_text if tok.change == "convert" else None)
        self.history.append(tok)
        del self.history[:-8]
        self.erased = None
        return tok

    def _apply_decision(self, tok: Token) -> None:
        d = tok.decision
        if d is None or not d.changes_text:
            return
        if d.action == "fix_case" and not self.config.fix_caps_lock:
            return
        group: list[tuple[Token, Decision]] = []
        if d.action == "convert" and self.config.look_back:
            group = self._look_back(tok, d.target_lang)
        rewritten = [t for t, _ in group] + [tok]
        old = "".join(t.text + t.delim for t in rewritten)
        for t, td in group:
            t.original_text, t.text, t.lang, t.change, t.decision = t.text, td.text, td.target_lang, "convert", td
        tok.original_text, tok.text, tok.lang, tok.change = tok.text, d.text, d.target_lang, d.action
        tok.group = [t for t, _ in group]
        new = "".join(t.text + t.delim for t in rewritten)
        if d.action == "convert":
            self._switch_layout(d.target_lang)
        else:
            self.backend.caps_lock_off()
        self._rewrite(len(old), new)
        self.undo_target = tok
        if d.reason == "rule":
            self.learner.profile.rule_used("layout", self.learner.core_keys(tok.strokes, tok.text, tok.lang),
                                           self.app)
        for t, td in group:
            self.learner.committed(app=self.app, lang=t.lang, text=t.text, decision=td, changed_from=t.original_text)

    def _look_back(self, tok: Token, target: str) -> list[tuple[Token, Decision]]:
        group: list[tuple[Token, Decision]] = []
        for index in range(len(self.history) - 1, max(-1, len(self.history) - 3), -1):
            prev = self.history[index]
            if (prev.change or prev.mixed or prev.retyped or prev.lang == target or prev.lang != tok.typed_lang
                    or "\n" in prev.delim or not prev.strokes or prev.decision is None):
                break
            ctx = self._context(self.history[:index])
            ctx.next_lang = target
            ctx.manual_switch = False  # the next word already proved the layout wrong
            d = self.engine.decide(prev.strokes, prev.typed_lang, ctx, typed_text=prev.text)
            if d.action != "convert" or d.target_lang != target:
                break
            group.insert(0, (prev, d))
        return group

    def _apply_replace_rule(self, tok: Token) -> None:
        start, end, core = core_of(tok.text, tok.lang)
        if not core:
            return
        rule = self.learner.profile.replace_rule(core.lower(), self.app)
        if rule is None:
            return
        right = match_case(core, rule.value)
        new_text = tok.text[:start] + right + tok.text[end:]
        self._rewrite(len(tok.text) + len(tok.delim), new_text + tok.delim)
        tok.original_text, tok.text, tok.change = tok.text, new_text, "replace"
        self.undo_target = tok
        self.learner.replace_applied(app=self.app, wrong=core, right=right)

    def _detect_retype(self, tok: Token) -> None:
        old = self.erased
        if old is None or tok.mixed:
            return
        old_strokes = self.keyboard.strokes(old.text, old.lang) if old.lang in (EN, RU) else None
        if old_strokes is None:
            return
        same_keys = canonical_keys(old_strokes) == canonical_keys(tok.strokes)
        if same_keys and tok.typed_lang != old.lang:
            tok.retyped = True
            if old.change == "convert":
                self.learner.undo(app=self.app, strokes=tok.strokes, typed_lang=tok.typed_lang,
                                  typed_text=tok.text, converted_lang=old.lang, converted_text=old.text,
                                  reason=old.decision.reason if old.decision else "model", via="retype")
            else:
                self.learner.manual(app=self.app, strokes=tok.strokes, typed_lang=old.lang, typed_text=old.text,
                                    target_lang=tok.typed_lang, target_text=tok.text,
                                    margin=old.decision.margin if old.decision else None, via="retype")
        elif tok.typed_lang == old.lang and old.change == "replace":
            if tok.text == old.original_text:
                tok.retyped = True
                self.learner.replace_undone(app=self.app, wrong=old.original_text, right=old.text)
        elif tok.typed_lang == old.lang and not same_keys:
            _, _, wrong = core_of(old.text, old.lang)
            _, _, right = core_of(tok.text, tok.lang)
            if wrong and right and levenshtein(wrong.lower(), right.lower()) <= 2:
                self.learner.typo_fix(app=self.app, wrong=wrong, right=right, lang=tok.lang)

    def _detect_prefix_fix(self, tok: Token) -> None:
        strokes, lang, text = tok.prefix_hint  # type: ignore[misc]
        n = len(strokes)
        if n and canonical_keys(tok.strokes[:n]) == canonical_keys(strokes) and tok.typed_lang != lang:
            self.learner.prefix_fix(app=self.app, typed_text=text, typed_lang=lang, target_lang=tok.typed_lang)

    # -- editing the screen --------------------------------------------------

    def _rewrite(self, delete: int, text: str) -> None:
        self._generation += 1
        if delete:
            self.backend.backspace(delete)
        if text:
            self.backend.type_text(text)

    def _switch_layout(self, lang: str) -> None:
        self._we_switched_at = self._now
        self.layout = lang
        self.backend.set_layout(lang)

    # -- hotkeys -------------------------------------------------------------

    def _match_hotkey(self, ev: KeyEvent) -> str | None:
        key = ev.code
        if key is None and ev.char:
            found = self.keyboard.stroke_for_char(ev.char, self.layout)
            key = found[0].code if found else ev.char.lower()
        key = key or ev.key
        for name, spec in self.hotkeys.items():
            if spec == "double_shift" or spec is None:
                continue
            mods, target = spec
            if mods == self.mods and target in (key, ev.key):
                return name
        return None

    def run_hotkey(self, name: str) -> None:
        log.debug("hotkey %s", name)
        if name == "toggle":
            self.enabled = not self.enabled
            self.backend.notify("Автопереключение " + ("включено" if self.enabled else "на паузе"))
            return
        if name == "convert_last":
            self.convert_last()
        elif name == "convert_selection":
            self.convert_selection()
        elif name == "ai_fix":
            self.ai_fix()

    def convert_last(self) -> None:
        """Undo our last change, or convert the word the user is on / just finished."""
        now = self._now
        if self.undo_target is not None and self.cur is None and self.history and self.history[-1] is self.undo_target:
            self._undo(self.undo_target)
            return
        if self.manual_target and self.cur is None and self.history and self.history[-1] is self.manual_target[0] \
                and now - self.manual_target[3] < 5.0:
            tok, back_to, rule_key, _ = self.manual_target
            self._convert_committed(tok, back_to, learn=False)
            self.learner.retract(rule_key)
            self.manual_target = None
            return
        if self.cur and self.cur.strokes:
            cur = self.cur
            if cur.mixed:
                return
            target = other(cur.typed_lang)
            new = self.keyboard.text(cur.strokes, target)
            self._switch_layout(target)
            self._rewrite(len(cur.chars), new)
            if cur.manual_from is None:
                cur.manual_from = cur.typed_lang
            elif cur.manual_from == target:
                cur.manual_from = None
            cur.chars, cur.typed_lang = list(new), target
            return
        if self.cur is None and self.history:
            tok = self.history[-1]
            if tok.mixed or tok.lang not in (EN, RU):
                return
            source = tok.lang
            rule = self._convert_committed(tok, other(source), learn=True)
            self.manual_target = (tok, source, rule, now)

    def _convert_committed(self, tok: Token, target: str, learn: bool) -> tuple[str, str] | None:
        source = tok.lang
        new = self.keyboard.convert(tok.text, source, target)
        if new is None:
            return None
        before = tok.text
        self._switch_layout(target)
        self._rewrite(len(tok.text) + len(tok.delim), new + tok.delim)
        tok.text, tok.lang, tok.change = new, target, "manual"
        if not learn:
            return None
        strokes = self.keyboard.strokes(new, target) or []
        margin = tok.decision.margin if tok.decision and tok.decision.reason == "model" else None
        if tok.decision and tok.decision.reason == "rule":
            self.learner.rule_overridden(app=self.app, keys=canonical_keys(strokes))
        return self.learner.manual(app=self.app, strokes=strokes, typed_lang=source, typed_text=before,
                                   target_lang=target, target_text=new, margin=margin)

    def _undo(self, tok: Token) -> None:
        group = tok.group + [tok]
        current = "".join(t.text + t.delim for t in group)
        restored = "".join(t.original_text + t.delim for t in group)
        change = tok.change
        if change == "convert":
            self._switch_layout(tok.typed_lang)
        self._rewrite(len(current), restored)
        converted_text, converted_lang = tok.text, tok.lang
        for t in group:
            t.text, t.lang, t.change = t.original_text, t.typed_lang, ""
        tok.group = []
        self.undo_target = None
        if change == "convert":
            d = tok.decision
            self.learner.undo(app=self.app, strokes=tok.strokes, typed_lang=tok.typed_lang, typed_text=tok.text,
                              converted_lang=converted_lang, converted_text=converted_text,
                              reason=d.reason if d else "model")
        elif change == "replace":
            self.learner.replace_undone(app=self.app, wrong=tok.text, right=converted_text)

    def convert_selection(self) -> None:
        text = self.backend.copy_selection()
        if not text:
            return
        lang = text_lang(text)
        target = RU if lang in (EN, None) else EN
        if lang == "mixed":
            latin = sum(1 for ch in text if letter_lang(ch) == EN)
            cyr = sum(1 for ch in text if letter_lang(ch) == RU)
            target = RU if latin >= cyr else EN
        converted = self.keyboard.convert_mixed(text, target)
        self._switch_layout(target)
        self.backend.paste_text(converted)
        self.learner.profile.log_event("selection", app=self.app, typed_text=text[:200],
                                       final_text=converted[:200], final_lang=target)
        self.reset("selection")

    # -- AI ------------------------------------------------------------------

    def phrase_tokens(self) -> list[Token]:
        tokens = list(self.history)
        if self.cur and self.cur.strokes:
            tokens.append(self.cur)
        return tokens

    def ai_fix(self) -> None:
        if self.ai is None:
            self.backend.notify("ИИ не настроен: задайте ANTHROPIC_API_KEY")
            return
        tokens = self.phrase_tokens()
        if tokens:
            self._ai_fix_phrase(tokens)
            return
        text = self.backend.copy_selection()
        if text:
            self._ai_fix_selection(text)

    def _ai_fix_phrase(self, tokens: list[Token]) -> None:
        pieces = []
        for t in tokens:
            text = t.text if t is not self.cur else t.typed_text
            lang = t.lang if t is not self.cur else t.typed_lang
            strokes = self.keyboard.strokes(text, lang) if lang in (EN, RU) else None
            en = self.keyboard.text(strokes, EN) if strokes else text
            ru = self.keyboard.text(strokes, RU) if strokes else text
            pieces.append({"screen": text, "en": en, "ru": ru, "delim": t.delim if t is not self.cur else ""})
        screen = "".join(p["screen"] + p["delim"] for p in pieces)
        generation = self._generation
        app = self.app
        self.backend.notify("ИИ исправляет фразу…")

        def work():
            return self.ai.fix_phrase(pieces, style=self.learner.profile.get_meta("style_summary"), app=app)

        def done(result):
            if isinstance(result, Exception):
                self.backend.notify(f"ИИ: {result}")
                return
            if generation != self._generation:
                self.backend.notify("Текст изменился, пока ИИ думал — исправление отменено")
                return
            fixed: str = result  # type: ignore[assignment]
            if fixed == screen:
                return
            target = text_lang(fixed.split()[-1]) if fixed.split() else None
            if target in (EN, RU):
                self._switch_layout(target)
            self._rewrite(len(screen), fixed)
            self._learn_from_ai(pieces, fixed)
            self.reset("ai_fix")

        self._run_async(work, lambda result: self.post(lambda: done(result)))

    def _ai_fix_selection(self, text: str) -> None:
        pieces = []
        for word in text.split(" "):
            lang = text_lang(word)
            en = self.keyboard.convert_mixed(word, EN) if lang else word
            ru = self.keyboard.convert_mixed(word, RU) if lang else word
            pieces.append({"screen": word, "en": en, "ru": ru, "delim": " "})
        pieces[-1]["delim"] = ""
        app = self.app

        def work():
            return self.ai.fix_phrase(pieces, style=self.learner.profile.get_meta("style_summary"), app=app)

        def done(result):
            if isinstance(result, Exception):
                self.backend.notify(f"ИИ: {result}")
                return
            if result != text:
                self.backend.paste_text(result)  # type: ignore[arg-type]
                self._learn_from_ai(pieces, result)  # type: ignore[arg-type]
            self.reset("ai_fix")

        self._run_async(work, lambda result: self.post(lambda: done(result)))

    def _learn_from_ai(self, pieces: list[dict], fixed: str) -> None:
        words = fixed.split()
        originals = [p for p in pieces if p["screen"].strip()]
        if len(words) != len(originals):
            return
        for piece, word in zip(originals, words):
            screen = piece["screen"]
            if word == screen:
                continue
            for lang in (EN, RU):
                if word == piece[lang] and text_lang(screen) in (EN, RU) and text_lang(screen) != lang:
                    src = text_lang(screen)
                    strokes = self.keyboard.strokes(word, lang) or []
                    self.learner.manual(app=self.app, strokes=strokes, typed_lang=src, typed_text=screen,
                                        target_lang=lang, target_text=word, via="ai_fix")

    @staticmethod
    def _thread_async(work: Callable[[], object], done: Callable[[object], None]) -> None:
        def runner():
            try:
                result = work()
            except Exception as exc:  # reported to the user via notify
                log.exception("async job failed")
                result = exc
            done(result)

        threading.Thread(target=runner, daemon=True).start()


def match_case(template: str, word: str) -> str:
    if template.isupper() and len(template) > 1:
        return word.upper()
    if template[:1].isupper():
        return word[:1].upper() + word[1:]
    return word


def parse_hotkey(spec: str):
    """"<ctrl>+<alt>+x" -> ({"ctrl", "alt"}, "x"); "double_shift" stays as is."""
    spec = spec.strip().lower()
    if spec == "double_shift":
        return spec
    mods: set[str] = set()
    target = None
    for part in spec.split("+"):
        part = part.strip()
        name = part[1:-1] if part.startswith("<") and part.endswith(">") else part
        name = {"control": "ctrl", "option": "alt", "command": "cmd", "win": "cmd", "super": "cmd"}.get(name, name)
        if name in MODIFIERS:
            mods.add(name)
        else:
            target = name
    if not target:
        return None
    return frozenset(mods), target
