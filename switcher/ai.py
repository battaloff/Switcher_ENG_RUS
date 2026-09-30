"""Claude as the switcher's second brain.

Two jobs, both optional and off the typing path:

* :meth:`Assistant.review` — every N corrections (and on ``switcher learn``)
  Claude reads the journal of the user's corrections, the learned rules and
  per-app statistics, and returns new rules, rules to drop, per-app
  preferences and a short description of the user's writing style.  Every
  proposal is validated locally before it touches the profile, and AI rules
  never override rules the user made by hand.
* :meth:`Assistant.fix_phrase` — on a hotkey, Claude restores a whole phrase
  that mixes layouts ("z pfgeibk d main" → "я запушил в main"), following the
  learned style.

What is sent: the corrected words from the journal and the phrase being
fixed — never a raw keystroke log.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from .config import AI
from .engine import split_core
from .langmodel import is_word
from .layouts import EN, RU, Keyboard, Stroke, canonical_keys, text_lang
from .learner import levenshtein
from .profile import Profile

log = logging.getLogger(__name__)

# Models that accept server-side refusal fallbacks (`fallbacks: "default"`).
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}
_FALLBACK_BETA = "server-side-fallback-2026-07-01"
_NO_EFFORT_MODELS = ("claude-haiku-4-5", "claude-sonnet-4-5")  # these reject output_config.effort

REVIEW_KINDS = ("auto", "undo", "manual", "prefix_fix", "typo_fix", "ambiguous", "replace", "replace_undo",
                "selection")


class AIError(RuntimeError):
    pass


WORKSPACE_HINT = ("ключ не привязан к рабочему пространству: создайте новый ключ и в поле Scope "
                  "выберите пространство (например, Default Workspace)")


def _status_message(exc) -> str:
    if "anthropic-workspace-id" in str(exc):
        return WORKSPACE_HINT
    return f"Claude API ответил ошибкой {exc.status_code}"


FIX_SYSTEM = """\
You restore text typed on a keyboard with two layouts: English QWERTY and Russian ЙЦУКЕН. \
The user sometimes forgets to switch layouts, so some words come out as gibberish in the wrong \
alphabet ("ghbdtn" is "привет" typed on the English layout, "руддщ" is "hello" typed on the Russian one).

You get the phrase word by word. For every word you see what is on screen and the same keys \
rendered on the English and on the Russian layout. Return, for every word in the same order, the \
word the user meant — almost always exactly one of the three variants. Words legitimately written \
in English inside Russian text (names, code, brands, terms) stay English.{languages}

Change nothing else: keep punctuation, capitalization, slang, abbreviations and the user's own \
spelling{typos}. The user's style, learned from their corrections:
{style}"""

REVIEW_SYSTEM = """\
You tune a smart keyboard-layout switcher (like Punto Switcher) for one person who types in \
Russian and English. The switcher converts a word when it looks typed in the wrong layout; the \
person corrects it when it is wrong. You receive their recent correction journal, the rules \
learned so far, per-app language statistics and their personal vocabulary.

Journal kinds: auto = switcher converted a word and the person kept it; undo = the person reverted \
a conversion (the switcher was wrong); manual = the person converted a word the switcher left \
alone (the switcher missed it); prefix_fix = the person erased the first letters and retyped them \
in the other layout; typo_fix = the person retyped a word with a small spelling change; \
ambiguous = a close call; replace/replace_undo = a learned typo correction applied / reverted.

Propose only changes the evidence supports:
- layout_rules: words that must always end up in a given language ("word" written the way the \
person wants to see it). Good candidates: repeated undo/manual on the same word, jargon, names.
- replace_rules: the person's recurring typos (wrong → right), only when seen repeatedly.
- drop_rules: learned or AI rules that the journal shows are wrong.
- app_preferences: an app's usual language and a threshold_delta in [-0.5, 0.5] (positive = \
switch less eagerly there, e.g. many undos; negative = the person keeps fixing misses there).
- style_summary: up to 600 characters in Russian, addressed to the person: how they write \
(languages, mixing, slang, casing, typical mistakes and in which apps). Update the previous summary \
rather than starting over.
Return empty lists when nothing is warranted."""

_FIX_SCHEMA = {
    "type": "object",
    "properties": {"words": {"type": "array", "items": {"type": "string"}}},
    "required": ["words"],
    "additionalProperties": False,
}

_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "layout_rules": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "word": {"type": "string"}, "lang": {"type": "string", "enum": ["en", "ru"]},
                "app": {"type": "string"}, "reason": {"type": "string"},
            },
            "required": ["word", "lang", "app", "reason"], "additionalProperties": False}},
        "replace_rules": {"type": "array", "items": {
            "type": "object",
            "properties": {"wrong": {"type": "string"}, "right": {"type": "string"}, "reason": {"type": "string"}},
            "required": ["wrong", "right", "reason"], "additionalProperties": False}},
        "drop_rules": {"type": "array", "items": {
            "type": "object",
            "properties": {"kind": {"type": "string", "enum": ["layout", "replace"]}, "word": {"type": "string"},
                           "reason": {"type": "string"}},
            "required": ["kind", "word", "reason"], "additionalProperties": False}},
        "app_preferences": {"type": "array", "items": {
            "type": "object",
            "properties": {"app": {"type": "string"}, "lang": {"type": "string", "enum": ["en", "ru", "none"]},
                           "threshold_delta": {"type": "number"}, "reason": {"type": "string"}},
            "required": ["app", "lang", "threshold_delta", "reason"], "additionalProperties": False}},
        "style_summary": {"type": "string"},
    },
    "required": ["layout_rules", "replace_rules", "drop_rules", "app_preferences", "style_summary"],
    "additionalProperties": False,
}


@dataclass
class ReviewOutcome:
    added: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    apps: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    style: str = ""

    def summary(self) -> str:
        parts = []
        if self.added:
            parts.append(f"новых правил: {len(self.added)}")
        if self.dropped:
            parts.append(f"удалено: {len(self.dropped)}")
        if self.apps:
            parts.append(f"настроек приложений: {len(self.apps)}")
        return ", ".join(parts) or "без изменений"


class Assistant:
    def __init__(self, config: AI, client: Any = None):
        self.config = config
        self._client = client

    @property
    def client(self):
        if self._client is None:
            import anthropic

            from .secrets import reveal

            key = reveal(self.config.api_key)
            kwargs = {"api_key": key} if key else {}
            self._client = anthropic.Anthropic(timeout=self.config.timeout, max_retries=2, **kwargs)
        return self._client

    def has_credentials(self) -> bool:
        import os

        return bool(self.config.api_key or os.environ.get("ANTHROPIC_API_KEY")
                    or os.environ.get("ANTHROPIC_AUTH_TOKEN"))

    def check_key(self) -> str:
        """Validate the credentials without spending tokens (Models API)."""
        import anthropic

        try:
            model = self.client.models.retrieve(self.config.model)
        except anthropic.AuthenticationError:
            raise AIError("ключ не подходит") from None
        except anthropic.NotFoundError:
            raise AIError(f"модель {self.config.model} недоступна для этого ключа") from None
        except anthropic.APIConnectionError:
            raise AIError("нет связи с Claude API") from None
        except anthropic.APIStatusError as exc:
            raise AIError(_status_message(exc)) from None
        return getattr(model, "display_name", None) or self.config.model

    @staticmethod
    def available() -> bool:
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False
        return True

    # -- plumbing ------------------------------------------------------------

    def _ask(self, system: str, payload: dict, schema: dict, *, effort: str, max_tokens: int) -> dict:
        import anthropic

        kwargs: dict[str, Any] = {}
        if self.config.model in _FALLBACK_MODELS:
            kwargs.update(betas=[_FALLBACK_BETA], fallbacks="default")
        output_config: dict[str, Any] = {"format": {"type": "json_schema", "schema": schema}}
        if not self.config.model.startswith(_NO_EFFORT_MODELS):
            output_config["effort"] = effort
        try:
            response = self.client.beta.messages.create(
                model=self.config.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                output_config=output_config,
                **kwargs,
            )
        except anthropic.AuthenticationError as exc:
            raise AIError("нет доступа к Claude API: проверьте ключ в настройках") from exc
        except anthropic.RateLimitError as exc:
            raise AIError("Claude API: превышен лимит запросов, попробуйте позже") from exc
        except anthropic.APIStatusError as exc:
            raise AIError(_status_message(exc)) from exc
        except anthropic.APIConnectionError as exc:
            raise AIError("нет связи с Claude API") from exc
        if response.stop_reason == "refusal":
            raise AIError("Claude отказался обрабатывать этот текст")
        if response.stop_reason == "max_tokens":
            raise AIError("ответ Claude оборвался")
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise AIError("Claude вернул не JSON") from exc

    # -- phrase fix ----------------------------------------------------------

    def fix_phrase(self, pieces: list[dict], style: str = "", app: str = "", typos: bool | None = None,
                   uzbek: bool = False) -> str:
        """``pieces``: [{"screen", "en", "ru", "delim"}] → the corrected phrase.

        ``typos``: also fix obvious typos (default: the ``fix_typos`` setting; fixing a
        selection the user explicitly asked for always allows it).
        """
        allow = self.config.fix_typos if typos is None else typos
        system = FIX_SYSTEM.format(
            typos=(" — except obvious typos, which you do fix: a stray, missing or doubled key, swapped "
                   "letters, a Shift held one letter too long (\"RE;liable\" → \"Reliable\")") if allow else "",
            style=style or "(nothing learned yet)",
            languages=(" The user also writes Uzbek, in Latin (\"oldin\", \"yo'q\") and in Cyrillic (\"олдин\", "
                       "\"йўқ\", or \"йук\" typed on a Russian keyboard): Uzbek words stay exactly as typed.")
            if uzbek else "",
        )
        payload = {"app": app, "words": [{k: p[k] for k in ("screen", "en", "ru")} for p in pieces]}
        data = self._ask(system, payload, _FIX_SCHEMA, effort="low", max_tokens=4000)
        words = data.get("words")
        if not isinstance(words, list) or len(words) != len(pieces) or not all(isinstance(w, str) for w in words):
            raise AIError("Claude вернул другое число слов")
        return "".join(w + p["delim"] for w, p in zip(words, pieces))

    # -- profile review ------------------------------------------------------

    def review(self, profile: Profile, keyboard: Keyboard) -> tuple[dict, dict]:
        """Ask Claude for profile changes. Returns (digest sent, raw proposal)."""
        digest = build_digest(profile, keyboard)
        proposal = self._ask(REVIEW_SYSTEM, digest, _REVIEW_SCHEMA, effort="medium", max_tokens=16000)
        return digest, proposal


def rule_word(rule, keyboard: Keyboard) -> str:
    if rule.kind == "replace":
        return rule.pattern
    if rule.kind == "case":
        return rule.value
    return keyboard.text([Stroke(code) for code in rule.pattern], rule.value)


def build_digest(profile: Profile, keyboard: Keyboard, limit: int = 300) -> dict:
    events = []
    for e in profile.events(REVIEW_KINDS, limit=limit):
        item = {"kind": e["kind"], "app": e["app"], "typed": e["typed_text"], "result": e["final_text"]}
        if e["margin"] is not None:
            item["margin"] = round(e["margin"], 1)
        if e["detail"].get("was"):
            item["switcher_made"] = e["detail"]["was"]
        if e["detail"].get("via"):
            item["via"] = e["detail"]["via"]
        events.append(item)
    stats = sorted(profile.app_stats().items(), key=lambda kv: -sum(kv[1].values()))[:15]
    return {
        "journal": events,
        "rules": [
            {"kind": r.kind, "word": rule_word(r, keyboard), "result": r.value, "app": r.app, "source": r.source,
             "hits": r.hits, "misses": r.misses}
            for r in profile.rules()
        ][:400],
        "apps": [{"app": app or "(unknown)", **counts} for app, counts in stats],
        "app_settings": {app: {"threshold_offset": round(o, 2), "lang": p} for app, (o, p) in profile.tuning().items()},
        "personal_words": [{"lang": l, "word": w, "uses": c} for l, w, c in profile.vocab(limit=120)],
        "previous_style_summary": profile.get_meta("style_summary"),
    }


def apply_review(proposal: dict, profile: Profile, keyboard: Keyboard, known_apps: set[str] | None = None
                 ) -> ReviewOutcome:
    """Validate Claude's proposal and apply the sound parts."""
    out = ReviewOutcome()
    known_apps = known_apps if known_apps is not None else set(profile.app_stats()) | set(profile.tuning())

    for item in proposal.get("layout_rules", []):
        word, lang, app = str(item.get("word", "")).strip(), item.get("lang"), str(item.get("app", "")).strip()
        if app and app not in known_apps:
            app = ""
        start, end = split_core(word, lang) if lang in (EN, RU) else (0, 0)
        core = word[start:end].lower()
        strokes = keyboard.strokes(core, lang) if core else None
        if not core or " " in word or text_lang(core) != lang or strokes is None:
            out.rejected.append(f"layout {word!r}")
            continue
        keys = canonical_keys(strokes)
        existed = profile.get_rule("layout", keys, app)
        rule = profile.add_rule("layout", keys, lang, app=app, source="ai", note=str(item.get("reason", ""))[:200])
        if rule is not None and (existed is None or existed.value != lang):
            out.added.append(f"{core} → {lang}" + (f" в {app}" if app else ""))

    for item in proposal.get("replace_rules", []):
        wrong, right = str(item.get("wrong", "")).strip().lower(), str(item.get("right", "")).strip().lower()
        lang = text_lang(right)
        ok = (lang in (EN, RU) and text_lang(wrong) == lang and wrong != right and is_word(wrong, lang)
              and is_word(right, lang) and levenshtein(wrong, right) <= max(2, len(right) // 3))
        if not ok:
            out.rejected.append(f"replace {wrong!r}→{right!r}")
            continue
        existed = profile.get_rule("replace", wrong)
        rule = profile.add_rule("replace", wrong, right, source="ai", note=str(item.get("reason", ""))[:200])
        if rule is not None and (existed is None or existed.value != right):
            out.added.append(f"{wrong} ⇒ {right}")

    for item in proposal.get("drop_rules", []):
        kind, word = item.get("kind"), str(item.get("word", "")).strip().lower()
        for rule in profile.rules(kind):
            if rule.source != "user" and rule_word(rule, keyboard).lower() == word:
                profile.remove_rule(rule.kind, rule.pattern, rule.app)
                out.dropped.append(f"{kind}: {word}")

    for item in proposal.get("app_preferences", []):
        app = str(item.get("app", "")).strip()
        if not app or app not in known_apps:
            continue
        lang = item.get("lang")
        profile.set_app_preference(app, lang if lang in (EN, RU) else "", str(item.get("reason", ""))[:200])
        try:
            delta = max(-0.5, min(0.5, float(item.get("threshold_delta", 0.0))))
        except (TypeError, ValueError):
            delta = 0.0
        if delta:
            profile.adjust_threshold(app, delta)
        out.apps.append(app)

    style = str(proposal.get("style_summary", "")).strip()
    if style:
        profile.set_meta("style_summary", style[:1500])
        profile.set_meta("style_updated", str(time.time()))
        out.style = style
    profile.set_meta("last_review_event", str(profile.last_event_id()))
    profile.log_event("ai_review", detail={"added": out.added, "dropped": out.dropped, "apps": out.apps,
                                           "rejected": out.rejected})
    return out
