"""Human-readable summaries of the profile (for the settings window and the CLI)."""

from __future__ import annotations

from datetime import datetime

from .layouts import RU, Keyboard
from .profile import Profile

EVENT_LABELS = {
    "auto": "автоисправлений",
    "undo": "отмен",
    "manual": "ручных исправлений",
    "prefix_fix": "перенаборов в начале слова",
    "typo_fix": "исправленных опечаток",
    "replace": "заменено опечаток",
    "ai_review": "разборов ИИ",
}


def rule_rows(profile: Profile, keyboard: Keyboard) -> list[dict]:
    from .ai import rule_word

    rows = []
    for r in profile.rules():
        if r.kind == "layout":
            result = "всегда по-английски" if r.value == "en" else "всегда по-русски"
        else:
            result = f"заменять на «{r.value}»"
        rows.append({
            "rule": r, "word": rule_word(r, keyboard), "result": result, "app": r.app or "везде",
            "source": {"user": "вы", "learned": "по вашим правкам", "ai": "Claude"}.get(r.source, r.source),
            "hits": r.hits, "note": r.note,
        })
    return rows


def stats_text(profile: Profile, verbose: bool = False, keyboard: Keyboard | None = None) -> str:
    profile.flush()
    lines = []
    rules = profile.rules()
    counts: dict[str, int] = {}
    for event in profile.events(limit=100_000):
        counts[event["kind"]] = counts.get(event["kind"], 0) + 1
    lines.append(f"Правил: {len(rules)} (раскладка: {sum(r.kind == 'layout' for r in rules)}, "
                 f"опечатки: {sum(r.kind == 'replace' for r in rules)})")
    lines.append("События: " + ", ".join(f"{label} {counts.get(k, 0)}" for k, label in EVENT_LABELS.items()))
    stats = profile.app_stats()
    if stats:
        lines.append("")
        lines.append("Язык по приложениям:")
        for app, langs in sorted(stats.items(), key=lambda kv: -sum(kv[1].values()))[:12]:
            total = sum(langs.values())
            share = langs.get(RU, 0) / total if total else 0
            lines.append(f"  {app or '(неизвестно)':<24} слов {total:>6}   RU {share:>4.0%}   EN {1 - share:>4.0%}")
    vocab = profile.vocab(limit=20)
    if vocab:
        lines.append("")
        lines.append("Ваши слова: " + ", ".join(f"{w} ({c})" for _, w, c in vocab))
    mistakes = profile.events(["undo", "manual"], limit=15)
    if mistakes:
        lines.append("")
        lines.append("Последние исправления:")
        for e in mistakes:
            when = datetime.fromtimestamp(e["ts"]).strftime("%d.%m %H:%M")
            arrow = "оставить" if e["kind"] == "undo" else "переключить"
            lines.append(f"  {when} [{e['app'] or '?'}] {e['typed_text']!r} → {arrow} {e['final_text']!r}")
    style = profile.get_meta("style_summary")
    if style:
        lines.append("")
        lines.append("Ваш стиль (по мнению Claude):")
        lines.append(f"  {style}")
    if verbose and keyboard is not None and rules:
        lines.append("")
        for row in rule_rows(profile, keyboard):
            lines.append(f"  {row['word']!r:<20} → {row['result']:<24} {row['app']:<12} {row['source']}")
    return "\n".join(lines)
