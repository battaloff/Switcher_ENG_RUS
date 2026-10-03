"""Entry point of the Windows app (Switcher.exe): tray + settings window, no console.

``Switcher.exe --selftest [log]`` checks the packaged build and exits (used by
the installer pipeline).
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import threading
import traceback

log = logging.getLogger("switcher")
MUTEX_NAME = "SwitcherSingleInstance"  # also AppMutex in the installer
_mutex = None


def _message(text: str, error: bool = False) -> None:
    if sys.platform == "win32":
        import ctypes

        flags = 0x10 if error else 0x40  # MB_ICONERROR / MB_ICONINFORMATION
        ctypes.windll.user32.MessageBoxW(None, text, "Switcher", flags)
    else:
        print(text, file=sys.stderr if error else sys.stdout)


def _single_instance() -> bool:
    global _mutex
    if sys.platform != "win32":
        return True
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    _mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS


def _dpi_aware() -> None:
    if sys.platform == "win32":
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass


def _setup_logging() -> None:
    from .paths import log_path

    handler = logging.FileHandler(log_path(), encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    # there is no console: an error in a background thread must not vanish silently
    def thread_failed(args) -> None:
        if args.exc_type is not SystemExit:
            name = args.thread.name if args.thread else "?"
            log.error("thread %s failed", name, exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    threading.excepthook = thread_failed


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--selftest"]:
        return selftest(argv[1] if len(argv) > 1 else None)
    _dpi_aware()
    _setup_logging()
    if not _single_instance():
        _message("Switcher уже запущен — его значок в трее у часов.")
        return 0
    from .app import App
    from .config import load_config
    from .paths import log_path

    try:
        app = App(load_config())
    except Exception as exc:
        log.exception("start failed")
        _message(f"Не удалось запустить Switcher:\n{exc}\n\nПодробности в {log_path()}", error=True)
        return 1
    from . import __version__

    log.info("Switcher started: version %s, layouts %s, auto switch %s, early %s, autocorrect %s, uzbek %s, "
             "Claude %s", __version__, sorted(getattr(app.backend, "_hkls", {}) or []), app.config.auto_switch,
             app.config.early_switch, app.config.autocorrect, app.config.writes_uzbek, bool(app.assistant))
    try:
        app.run_gui()
    except Exception as exc:
        log.exception("crashed")
        _message(f"Switcher остановился из-за ошибки:\n{exc}\n\nПодробности в {log_path()}", error=True)
        return 1
    return 0


def selftest(report: str | None = None) -> int:
    """Exercise every part the packaged app needs, without touching the user's data."""
    os.environ["SWITCHER_HOME"] = tempfile.mkdtemp(prefix="switcher-selftest-")
    os.environ["SWITCHER_UPDATE_REPO"] = ""  # the updates page must not go online during the test
    lines: list[str] = []
    ok = True

    def step(name, fn):
        nonlocal ok
        try:
            result = fn()
            lines.append(f"OK   {name}" + (f": {result}" if result else ""))
        except Exception:
            ok = False
            lines.append(f"FAIL {name}\n{traceback.format_exc()}")

    state: dict = {}

    def models():
        from .langmodel import bundled_models_path, load_models

        state["models"] = load_models(build_if_missing=False)
        return str(bundled_models_path() or "cache")

    def engine():
        from .engine import Engine
        from .layouts import DEFAULT_KEYBOARD, EN

        d = Engine(state["models"], DEFAULT_KEYBOARD).decide(DEFAULT_KEYBOARD.strokes("ghbdtn", EN), EN)
        assert d.text == "привет", d.text
        return "ghbdtn → привет"

    def secrets():
        from .secrets import protect, reveal

        stored = protect("sk-ant-test")
        assert reveal(stored) == "sk-ant-test"
        return stored.split(":")[0]

    def app():
        from .app import App
        from .config import load_config

        state["app"] = App(load_config())
        backend = state["app"].backend
        return f"{type(backend).__name__}, раскладка {backend.current_layout()}, окно {backend.active_app()!r}"

    def claude():
        from .ai import Assistant
        from .config import AI
        from .secrets import protect

        client = Assistant(AI(api_key=protect("sk-ant-test"))).client
        assert client.beta.messages is not None and client.models is not None
        return type(client).__name__

    def tray():
        from .tray import icon_image

        import pystray  # noqa: F401

        return f"иконка {icon_image().size}"

    def updates():
        import ssl

        from . import updater

        certificates = ssl.create_default_context().cert_store_stats()["x509_ca"]
        assert certificates, "no root certificates: HTTPS to GitHub would fail"
        assert updater.parse_releases([]) == []
        return f"корневых сертификатов: {certificates}, установка возможна: {updater.can_install()}"

    def gui():
        from .gui import Ui

        ui = Ui(state["app"])
        ui.open_settings(welcome=True)
        ui.root.update()
        for tab in ("main", "keys", "snippets", "ahk", "ai", "rules", "stats", "updates"):
            ui.window.show(tab)
            ui.root.update()
        ui.root.destroy()

    step("языковая модель", models)
    step("движок", engine)
    step("хранение ключа", secrets)
    step("клавиатура и раскладки", app)
    step("Claude SDK", claude)
    step("значок в трее", tray)
    step("обновления", updates)

    def autohotkey():
        from . import ahk_editor  # noqa: F401  (bundled: imported only when a script is opened)

        manager = state["app"].ahk
        assert manager.supported, "no AutoHotkey support in this build"
        return f"{manager.describe_install()}; запущено скриптов: {len(manager.running(fresh=True))}"

    if "app" in state:
        step("AutoHotkey", autohotkey)
        step("окно настроек", gui)
    text = "\n".join(lines) + f"\n{'SELFTEST OK' if ok else 'SELFTEST FAILED'}\n"
    path = report or os.path.join(tempfile.gettempdir(), "switcher-selftest.log")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    if sys.stdout is not None:
        print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
