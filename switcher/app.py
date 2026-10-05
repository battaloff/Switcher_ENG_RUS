"""Wires everything together and runs the switcher."""

from __future__ import annotations

import logging
import os
import queue
import signal
import sys
import threading
import time
from pathlib import Path

from .ai import Assistant, ReviewOutcome, apply_review
from .config import Config
from .controller import Controller, parse_hotkey
from .engine import Engine
from .langmodel import load_models
from .layouts import Keyboard
from .learner import Learner
from .paths import config_path, profile_path
from .profile import Profile

log = logging.getLogger(__name__)

HEALTH_EVERY = 5.0  # seconds between checks that keys are still being handled
STUCK_AFTER = 20.0  # one key taking this long means the engine thread hangs
FLUSH_EVERY = 30.0  # seconds between saving the learned vocabulary and statistics


def make_assistant(config: Config) -> Assistant | None:
    if not config.ai.enabled or not Assistant.available():
        return None
    return Assistant(config.ai)


class App:
    def __init__(self, config: Config):
        from .platform import create_backend

        self.config = config
        self.backend = create_backend(Keyboard("pc"), config.typing_delay_ms)
        variant = config.ru_variant if config.ru_variant in ("pc", "mac") else (
            self.backend.detect_ru_variant() or "pc")
        self.keyboard = Keyboard(variant)
        self.backend.keyboard = self.keyboard
        self.models = load_models()
        self.profile = Profile(profile_path(), config.learning.journal_size, config.learning.min_vocab_count)
        self.engine = Engine(self.models, self.keyboard, self.profile)
        self.engine.tuning.threshold = config.threshold
        self.assistant = make_assistant(config)
        self.learner = Learner(self.profile, self.models, self.keyboard, config.learning,
                               on_feedback=self._on_feedback)
        self.controller = Controller(self.backend, self.engine, self.learner, config, self.keyboard,
                                     ai=self.assistant)
        self.controller.post = self.post
        self.queue: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self._worker_gen = 0
        self._worker_thread: threading.Thread | None = None
        self._busy_since = 0.0  # when the engine thread took the item it is on (0: idle)
        self.recoveries = 0
        self.recovery_log: list[tuple[float, str]] = []  # (time, what was restarted), for the self-check
        self.started_at = time.time()
        self._feedback = 0
        self._reviewing = threading.Lock()
        from .ahk import AhkManager

        self.ahk = AhkManager(config, notify=self.backend.notify)  # the user's AutoHotkey scripts
        self.releases: list | None = None  # the last list of versions fetched from GitHub
        self.release_listeners: list = []  # tray and settings window: called with the new list

    # -- event loop ----------------------------------------------------------

    def submit(self, event) -> None:
        self.queue.put(event)

    def post(self, fn) -> None:
        self.queue.put(fn)

    def _worker(self, gen: int = 0) -> None:
        last_flush = time.monotonic()
        while not self.stop_event.is_set() and gen == self._worker_gen:
            try:
                try:
                    item = self.queue.get(timeout=1.0)
                except queue.Empty:
                    item = None
                if item is not None:
                    self._busy_since = time.monotonic()
                    try:
                        if callable(item):
                            item()
                        else:
                            self.controller.handle(item)
                    except Exception:
                        log.exception("event handling failed")
                    finally:
                        self._busy_since = 0.0
                if time.monotonic() - last_flush > FLUSH_EVERY:
                    last_flush = time.monotonic()
                    self.profile.flush()
            except Exception:  # never let this thread end: the keys would go unanswered
                log.exception("engine loop failed")
                self.stop_event.wait(0.5)

    def _start_worker(self) -> threading.Thread:
        self._worker_gen += 1
        self._busy_since = 0.0
        worker = threading.Thread(target=self._worker, args=(self._worker_gen,),
                                  name=f"switcher-engine-{self._worker_gen}", daemon=True)
        worker.start()
        self._worker_thread = worker
        return worker

    def check_health(self) -> list[str]:
        """Restart whatever stopped handling keys; returns what was restarted."""
        fixed = []
        worker = self._worker_thread
        if worker is not None:
            hangs = self._busy_since and time.monotonic() - self._busy_since > STUCK_AFTER
            if hangs or not worker.is_alive():
                log.error("the engine thread %s: starting a new one", "hangs" if hangs else "has stopped")
                self._start_worker()  # a hung one quits once it gets unstuck
                self.post(lambda: self.controller.reset("engine-restart"))
                fixed.append("engine")
        try:
            what = self.backend.heal()
        except Exception:
            log.exception("keyboard health check failed")
            what = None
        if what:
            fixed.append(what)
        if fixed:
            self.recoveries += 1
            self.recovery_log = (self.recovery_log + [(time.time(), ", ".join(fixed))])[-10:]
            log.warning("recovered: %s", ", ".join(fixed))
        return fixed

    def diagnostics(self) -> str:
        """A plain-language self-check to read or to send: what works, what does not, recent errors."""
        from . import __version__
        from .paths import log_path

        b = self.backend
        now = time.monotonic()
        lines = [f"Switcher {__version__}, проверка {time.strftime('%d.%m.%Y %H:%M:%S')}",
                 f"Запущен: {time.strftime('%d.%m %H:%M', time.localtime(self.started_at))}"]
        worker = self._worker_thread
        busy = self._busy_since and now - self._busy_since
        if worker is None or not worker.is_alive():
            lines.append("Обработка клавиш: ОСТАНОВЛЕНА")
        elif busy and busy > 2:
            lines.append(f"Обработка клавиш: занята одним нажатием уже {busy:.0f} с")
        else:
            lines.append("Обработка клавиш: работает")
        listeners = getattr(b, "_listeners", [])
        hook_alive = bool(listeners) and listeners[0].is_alive()
        seen = getattr(b, "last_key_at", 0.0)
        ago = f"последнее {now - seen:.0f} с назад" if seen else "ещё ни одного"
        lines.append(f"Перехват клавиатуры: {'работает' if hook_alive else 'ОСТАНОВЛЕН'}, нажатий "
                     f"{getattr(b, 'keys_seen', 0)} ({ago})")
        watchdog = getattr(b, "_watchdog", None)
        if watchdog is not None:
            lines.append(f"Сторож перехвата: {'работает' if watchdog.running else 'НЕ РАБОТАЕТ'}, "
                         f"переустановок {watchdog.restarts}, после сна/разблокировки {watchdog.wakeups}")
        dropped = getattr(b, "injected_dropped", 0)
        lines.append(f"Программный ввод (удалённый доступ и т. п.): "
                     f"{'обрабатывается' if getattr(b, 'accept_injected', False) else 'не обрабатывается'}, "
                     f"пропущено нажатий {dropped}")
        layouts = sorted(getattr(b, "_hkls", {}) or [])
        try:
            current, app_name = b.current_layout(), b.active_app()
        except Exception as exc:
            current, app_name = f"ошибка: {exc}", "?"
        lines.append(f"Раскладки Windows: {', '.join(layouts) or 'не найдены'}; сейчас {current} в {app_name or '?'}")
        c = self.controller
        lines.append(f"Автопереключение: {'включено' if c.enabled else 'НА ПАУЗЕ'}; "
                     f"исправление опечаток {'вкл' if self.config.autocorrect else 'выкл'}; "
                     f"узбекский {'вкл' if self.config.writes_uzbek else 'выкл'}; "
                     f"Claude {'подключён' if self.ai_ready() else 'не подключён'}")
        lines.append(f"Дописывание: шаблонов {len(self.config.snippets)}; английская раскладка при сохранении "
                     f"файла {'вкл' if self.config.english_in_save_dialogs else 'выкл'}")
        if getattr(b, "_self_elevated", False):
            lines.append("Switcher запущен от имени администратора")
        elevated = getattr(b, "elevated_apps", [])
        if elevated:
            lines.append("Запущены от имени администратора (Windows не пускает туда Switcher): " + ", ".join(elevated))
        ahk = getattr(self, "ahk", None)
        if ahk is not None and ahk.supported:
            try:
                running = ahk.running(fresh=True)
                lines.append(f"AutoHotkey: скриптов в списке {len(ahk.configured())}, запущено {len(running)}"
                             + "".join(f"\n  {path}" for path in running))
            except Exception as exc:
                lines.append(f"AutoHotkey: ошибка {exc}")
        offset = self.profile.threshold_offset(app_name) if app_name and app_name != "?" else 0.0
        if offset > 0.5:
            lines.append(f"В «{app_name}» Switcher стал осторожнее (+{offset:.1f}) после ваших отмен")
        lines.append(f"Самовосстановлений: {self.recoveries}" + "".join(
            f"\n  {time.strftime('%d.%m %H:%M', time.localtime(t))} — {what}" for t, what in self.recovery_log))
        try:
            with open(log_path(), encoding="utf-8", errors="replace") as f:
                tail = f.readlines()[-400:]
            errors = [line.rstrip() for line in tail if " ERROR " in line or " WARNING " in line][-8:]
        except OSError:
            errors = []
        lines.append("Ошибки в журнале: " + ("нет" if not errors else "\n  " + "\n  ".join(errors)))
        return "\n".join(lines)

    def _watch_health(self) -> None:
        while not self.stop_event.wait(HEALTH_EVERY):
            try:
                self.check_health()
            except Exception:
                log.exception("health check failed")

    # -- settings --------------------------------------------------------------

    def update_config(self, new: Config, save: bool = True) -> None:
        """Apply settings edited in the window; takes effect immediately."""
        if save:
            new.save(config_path())
        self.post(lambda: self._apply_config(new))

    def _apply_config(self, new: Config) -> None:
        self.config.__dict__.update(new.__dict__)
        config = self.config
        self.engine.tuning.threshold = config.threshold
        self.learner.config = config.learning
        self.profile.min_vocab_count = config.learning.min_vocab_count
        self.controller.enabled = config.enabled
        self.controller.hotkeys = {name: parse_hotkey(spec) for name, spec in vars(config.hotkeys).items() if spec}
        self.assistant = make_assistant(config)
        self.controller.ai = self.assistant

    # -- AI review -----------------------------------------------------------

    def ai_ready(self) -> bool:
        return self.assistant is not None and self.assistant.has_credentials()

    def _on_feedback(self) -> None:
        self._feedback += 1
        every = self.config.ai.review_every
        if self.ai_ready() and every and self._feedback >= every and not self._reviewing.locked():
            self._feedback = 0
            threading.Thread(target=self.review_and_notify, daemon=True).start()

    def review(self) -> tuple[dict, dict, ReviewOutcome]:
        if self.assistant is None:
            raise RuntimeError("Claude выключен в настройках")
        with self._reviewing:
            self.profile.flush()
            digest, proposal = self.assistant.review(self.profile, self.keyboard)
            outcome = apply_review(proposal, self.profile, self.keyboard, models=self.models)
        return digest, proposal, outcome

    def review_and_notify(self) -> None:
        try:
            _, _, outcome = self.review()
            self.backend.notify(f"Claude изучил ваши исправления: {outcome.summary()}")
        except Exception as exc:
            log.warning("AI review failed: %s", exc)
            self.backend.notify(f"Claude не смог разобрать исправления: {exc}")

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> threading.Thread:
        self.backend.start(self.submit)
        worker = self._start_worker()
        threading.Thread(target=self._watch_health, name="switcher-health", daemon=True).start()
        if self.ahk.supported:
            threading.Thread(target=self._run_ahk, name="switcher-ahk", daemon=True).start()
        return worker

    def _run_ahk(self) -> None:
        """Start the AutoHotkey scripts marked to start with Switcher, then keep an eye on all of them."""
        if self.stop_event.wait(3.0):  # scripts Windows starts at sign-in may be on their way
            return
        try:
            self.ahk.start_with_switcher()
            self.ahk.watch(self.stop_event)
        except Exception:
            log.exception("AutoHotkey manager failed")

    def shutdown(self, worker: threading.Thread) -> None:
        self.stop_event.set()
        self.backend.stop()
        (self._worker_thread or worker).join(timeout=2)
        self.profile.close()

    # -- updates -------------------------------------------------------------

    def check_updates(self, force: bool = False) -> list:
        """Fetch the versions from GitHub (UpdateError on failure); remembers when it last did."""
        from . import updater

        if not force and time.time() - float(self.profile.get_meta("update_checked_at", "0") or 0) \
                < updater.CHECK_EVERY and self.releases is not None:
            return self.releases
        releases = updater.fetch_releases()
        self.profile.set_meta("update_checked_at", str(time.time()))
        self.releases = releases
        for listener in list(self.release_listeners):
            try:
                listener(releases)
            except Exception:
                log.exception("release listener failed")
        return releases

    def _auto_check_updates(self) -> None:
        """Soon after the start and then every few hours while running: announce a new version once."""
        from . import updater

        delay = 20.0
        while not self.stop_event.wait(delay):
            delay = updater.CHECK_TICK
            if not self.config.updates.check_automatically:
                continue
            last = float(self.profile.get_meta("update_checked_at", "0") or 0)
            if time.time() - last < updater.CHECK_EVERY:
                continue
            try:
                releases = self.check_updates(force=True)
            except updater.UpdateError as exc:
                log.info("update check failed: %s", exc)
                delay = updater.CHECK_RETRY
                continue
            newest = next((r for r in releases if r.relation == "newer" and not r.prerelease), None)
            if newest and self.profile.get_meta("update_announced") != newest.version:
                self.profile.set_meta("update_announced", newest.version)
                what = f": {newest.notes[0]}" if newest.notes else ""
                self.backend.notify(f"Вышла версия {newest.version}{what}. Обновить: Настройки → Обновления.")

    def install_update(self, release, setup) -> None:
        """Reinstall from ``setup`` (a newer or an older version) and quit; the helper starts us again."""
        from . import autostart, updater

        self.profile.set_meta("update_to", release.version)
        exe = Path(sys.executable) if getattr(sys, "frozen", False) else None
        updater.launch_installer(setup, autostart=autostart.is_enabled(), wait_pid=os.getpid(), relaunch=exe)
        self.stop_event.set()

    def _report_update(self) -> None:
        """After a reinstall: say whether the chosen version is the one running now."""
        from . import __version__, updater

        target = self.profile.get_meta("update_to")
        if not target:
            return
        self.profile.set_meta("update_to", "")
        if target == __version__:
            self.backend.notify(f"Готово: работает версия {__version__}.")
        else:
            self.backend.notify(f"Не получилось установить версию {target}, работает {__version__}. "
                                f"Подробности: {updater.install_log()}")

    def run_gui(self) -> None:
        """Windows desktop mode: settings window (Tk) on the main thread, tray icon beside it."""
        from .gui import Ui
        from .tray import Tray

        worker = self.start()
        ui = Ui(self)
        tray = Tray(self, ui)
        try:
            tray.start()
            if self.profile.get_meta("update_to"):
                self._report_update()
            else:
                self.backend.notify("Switcher работает. Двойной Shift — исправить или отменить слово.")
            threading.Thread(target=self._auto_check_updates, name="switcher-updates", daemon=True).start()
            if not self.profile.get_meta("welcomed"):
                self.profile.set_meta("welcomed", "1")
                ui.open_settings(welcome=True)
            ui.loop()
        finally:
            tray.stop()
            self.shutdown(worker)

    def run(self, tray: bool = True) -> None:
        worker = self.start()
        try:
            signal.signal(signal.SIGINT, lambda *_: self.stop_event.set())
            signal.signal(signal.SIGTERM, lambda *_: self.stop_event.set())
        except ValueError:
            pass
        ai_note = "ИИ подключён" if self.assistant else "ИИ выключен"
        self.backend.notify(f"Switcher запущен ({ai_note}). Двойной Shift — исправить/отменить.")
        try:
            used_tray = False
            if tray:
                from .tray import run_tray

                used_tray = run_tray(self)
            if not used_tray:
                self.backend.main_loop(self.stop_event)
        finally:
            self.shutdown(worker)
