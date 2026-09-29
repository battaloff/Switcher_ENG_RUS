"""Command line: ``switcher run``, ``switcher explain привет`` …"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from .config import load_config
from .layouts import EN, RU, Keyboard, canonical_keys, text_lang
from .paths import config_path, data_dir, log_path, profile_path


def _keyboard(config) -> Keyboard:
    return Keyboard(config.ru_variant if config.ru_variant in ("pc", "mac") else "pc")


def _profile(config):
    from .profile import Profile

    return Profile(profile_path(), config.learning.journal_size, config.learning.min_vocab_count)


def cmd_run(args, config) -> int:
    handlers: list[logging.Handler] = [logging.FileHandler(log_path(), encoding="utf-8")]
    if args.debug or (sys.stderr is not None and sys.stderr.isatty()):
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from .app import App

    print("Загружаю языковые модели (первый запуск строит их ~20 секунд)…")
    app = App(config)
    hint = app.backend.permissions_hint()
    if hint:
        print(hint)
    print("Switcher работает. Двойной Shift — исправить/отменить последнее слово. Ctrl+C — выход.")
    app.run(tray=not args.no_tray)
    return 0


def cmd_prepare(args, config) -> int:
    from .langmodel import default_models_path, load_models

    load_models()
    print(f"Языковые модели готовы: {default_models_path()}")
    return 0


def _engine(config):
    from .engine import Engine
    from .langmodel import load_models

    profile = _profile(config)
    engine = Engine(load_models(), _keyboard(config), profile)
    engine.tuning.threshold = config.threshold
    return engine, profile


def cmd_explain(args, config) -> int:
    from .engine import Context, explain

    engine, _ = _engine(config)
    for word in args.words:
        lang = args.layout or (text_lang(word) if text_lang(word) in (EN, RU) else EN)
        strokes = engine.keyboard.strokes(word, lang)
        if strokes is None:
            print(f"{word}: не набирается на раскладке {lang}")
            continue
        print(f"{word}  (набрано на {lang.upper()}, клавиши {canonical_keys(strokes)!r})")
        print(explain(engine.decide(strokes, lang, Context(app=args.app or ""))))
    return 0


def cmd_convert(args, config) -> int:
    kb = _keyboard(config)
    text = " ".join(args.text)
    lang = text_lang(text)
    target = args.to or (RU if lang in (EN, None) else EN)
    print(kb.convert_mixed(text, target))
    return 0


def cmd_stats(args, config) -> int:
    from .report import stats_text

    print(f"Профиль: {profile_path()}")
    print(stats_text(_profile(config), verbose=args.verbose, keyboard=_keyboard(config)))
    return 0


def cmd_rules(args, config) -> int:
    from .ai import rule_word
    from .engine import split_core

    profile = _profile(config)
    kb = _keyboard(config)
    if args.action == "list":
        for r in profile.rules():
            what = rule_word(r, kb)
            target = {"en": "всегда EN", "ru": "всегда RU"}.get(r.value, r.value) if r.kind == "layout" else r.value
            print(f"{r.kind:<8} {what!r:<22} → {target:<14} приложение: {r.app or 'все':<12} "
                  f"источник: {r.source:<8} срабатываний: {r.hits}  {r.note}")
        return 0
    if args.action == "add":
        word = args.word
        lang = args.lang or text_lang(word)
        if lang not in (EN, RU):
            print("Не понял язык слова, укажите --lang en или --lang ru")
            return 1
        start, end = split_core(word, lang)
        strokes = kb.strokes(word[start:end].lower(), lang)
        if strokes is None:
            print("Слово нельзя набрать на этой раскладке")
            return 1
        profile.add_rule("layout", canonical_keys(strokes), lang, app=args.app or "", source="user",
                         note="добавлено вручную")
        print(f"Готово: {word!r} всегда будет на {lang.upper()}")
        return 0
    if args.action == "typo":
        profile.add_rule("replace", args.word.lower(), args.right.lower(), app=args.app or "", source="user",
                         note="добавлено вручную")
        print(f"Готово: {args.word!r} будет заменяться на {args.right!r}")
        return 0
    if args.action == "remove":
        removed = 0
        for r in profile.rules():
            if rule_word(r, kb).lower() == args.word.lower() and (args.app is None or r.app == args.app):
                removed += profile.remove_rule(r.kind, r.pattern, r.app)
        print(f"Удалено правил: {removed}")
        return 0 if removed else 1
    return 1


def cmd_learn(args, config) -> int:
    from .ai import Assistant, apply_review

    if not Assistant.available():
        print("Установите пакет anthropic: pip install anthropic")
        return 1
    profile = _profile(config)
    kb = _keyboard(config)
    assistant = Assistant(config.ai)
    print("Claude изучает ваши исправления…")
    digest, proposal = assistant.review(profile, kb)
    if args.dry_run:
        print(json.dumps(proposal, ensure_ascii=False, indent=2))
        return 0
    outcome = apply_review(proposal, profile, kb)
    for line in outcome.added:
        print(f"  + {line}")
    for line in outcome.dropped:
        print(f"  − {line}")
    for app in outcome.apps:
        print(f"  ⚙ {app}")
    if outcome.rejected:
        print(f"  (отклонено как некорректное: {len(outcome.rejected)})")
    if outcome.style:
        print(f"\nВаш стиль:\n  {outcome.style}")
    profile.close()
    return 0


def cmd_forget(args, config) -> int:
    if not args.yes:
        print("Это удалит все выученные правила, слова и журнал. Повторите с --yes.")
        return 1
    profile = _profile(config)
    profile.forget()
    profile.close()
    print("Профиль очищен.")
    return 0


def cmd_export(args, config) -> int:
    print(json.dumps(_profile(config).export(), ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_doctor(args, config) -> int:
    ok = True
    print(f"Python {sys.version.split()[0]} на {sys.platform}")
    print(f"Настройки: {config_path()}")
    print(f"Данные:    {data_dir()}")
    try:
        import wordfreq  # noqa: F401

        print("✓ wordfreq")
    except ImportError:
        ok = False
        print("✗ wordfreq не установлен (pip install wordfreq)")
    try:
        from .platform import create_backend

        backend = create_backend(_keyboard(config))
        print(f"✓ перехват клавиатуры ({type(backend).__name__}); текущая раскладка: "
              f"{backend.current_layout() or 'не определена'}; активное окно: {backend.active_app() or '?'}")
        hint = backend.permissions_hint()
        if hint:
            print(f"  {hint}")
    except Exception as exc:
        ok = False
        print(f"✗ перехват клавиатуры недоступен: {exc}")
    from .ai import Assistant

    if not config.ai.enabled:
        print("• ИИ выключен в настройках")
    elif not Assistant.available():
        print("• пакет anthropic не установлен — ИИ-функции недоступны")
    else:
        has_key = Assistant(config.ai).has_credentials()
        print(f"{'✓' if has_key else '•'} Claude ({config.ai.model}): "
              f"{'ключ найден' if has_key else 'вставьте ключ в настройках или задайте ANTHROPIC_API_KEY'}")
    return 0 if ok else 1


def cmd_gui(args, config) -> int:
    from .gui_main import main as gui_main

    return gui_main([])


def cmd_autostart(args, config) -> int:
    from . import autostart

    if args.state == "on":
        print(f"Switcher будет запускаться при входе в систему: {autostart.enable()}")
    elif autostart.disable():
        print("Автозапуск выключен.")
    else:
        print("Автозапуск и так не был включён.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="switcher", description="Умный переключатель раскладки RU/EN с ИИ")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("run", help="запустить переключатель")
    p.add_argument("--debug", action="store_true", help="подробный лог")
    p.add_argument("--no-tray", action="store_true", help="без значка в трее")
    p.set_defaults(func=cmd_run)

    sub.add_parser("gui", help="запустить с окном настроек и значком в трее (как Switcher.exe)").set_defaults(
        func=cmd_gui)

    sub.add_parser("prepare", help="построить языковые модели заранее").set_defaults(func=cmd_prepare)

    p = sub.add_parser("explain", help="показать, как движок оценивает слово")
    p.add_argument("words", nargs="+")
    p.add_argument("--layout", choices=[EN, RU], help="на какой раскладке набрано (по умолчанию — по буквам)")
    p.add_argument("--app", help="учесть настройки приложения")
    p.set_defaults(func=cmd_explain)

    p = sub.add_parser("convert", help="перевести текст в другую раскладку")
    p.add_argument("text", nargs="+")
    p.add_argument("--to", choices=[EN, RU])
    p.set_defaults(func=cmd_convert)

    p = sub.add_parser("stats", help="что программа о вас узнала")
    p.add_argument("-v", "--verbose", action="store_true", help="показать все правила")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("rules", help="правила: list | add СЛОВО | typo ОШИБКА ВЕРНО | remove СЛОВО")
    p.add_argument("action", choices=["list", "add", "typo", "remove"])
    p.add_argument("word", nargs="?")
    p.add_argument("right", nargs="?")
    p.add_argument("--lang", choices=[EN, RU])
    p.add_argument("--app")
    p.set_defaults(func=cmd_rules)

    p = sub.add_parser("learn", help="попросить Claude разобрать ваши исправления сейчас")
    p.add_argument("--dry-run", action="store_true", help="только показать предложения")
    p.set_defaults(func=cmd_learn)

    p = sub.add_parser("forget", help="стереть всё выученное")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_forget)

    sub.add_parser("export", help="выгрузить профиль в JSON").set_defaults(func=cmd_export)

    p = sub.add_parser("autostart", help="запуск при входе в систему: on | off")
    p.add_argument("state", choices=["on", "off"])
    p.set_defaults(func=cmd_autostart)
    sub.add_parser("doctor", help="проверить окружение").set_defaults(func=cmd_doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    if args.command == "rules" and args.action in ("add", "remove", "typo") and not args.word:
        parser.error("укажите слово")
    if args.command == "rules" and args.action == "typo" and not args.right:
        parser.error("укажите правильное написание")
    config = load_config()
    try:
        return args.func(args, config)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        from .ai import AIError

        if isinstance(exc, AIError):
            print(f"Ошибка ИИ: {exc}")
            return 1
        raise


if __name__ == "__main__":
    sys.exit(main())
