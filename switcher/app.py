"""Wires everything together and runs the switcher."""

from __future__ import annotations

import logging
import queue
import signal
import threading
import time

from .ai import Assistant, ReviewOutcome, apply_review
from .config import Config
from .controller import Controller
from .engine import Engine
from .langmodel import load_models
from .layouts import Keyboard
from .learner import Learner
from .paths import profile_path
from .profile import Profile

log = logging.getLogger(__name__)


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
        self._feedback = 0
        self._reviewing = threading.Lock()

    # -- event loop ----------------------------------------------------------

    def submit(self, event) -> None:
        self.queue.put(event)

    def post(self, fn) -> None:
        self.queue.put(fn)

    def _worker(self) -> None:
        last_flush = time.monotonic()
        while not self.stop_event.is_set():
            try:
                item = self.queue.get(timeout=1.0)
            except queue.Empty:
                item = None
            if item is not None:
                try:
                    if callable(item):
                        item()
                    else:
                        self.controller.handle(item)
                except Exception:
                    log.exception("event handling failed")
            if time.monotonic() - last_flush > 30:
                self.profile.flush()
                last_flush = time.monotonic()

    # -- AI review -----------------------------------------------------------

    def _on_feedback(self) -> None:
        self._feedback += 1
        every = self.config.ai.review_every
        if self.assistant and every and self._feedback >= every and not self._reviewing.locked():
            self._feedback = 0
            threading.Thread(target=self.review_and_notify, daemon=True).start()

    def review(self) -> tuple[dict, dict, ReviewOutcome]:
        if self.assistant is None:
            raise RuntimeError("ИИ выключен или не установлен пакет anthropic")
        with self._reviewing:
            self.profile.flush()
            digest, proposal = self.assistant.review(self.profile, self.keyboard)
            outcome = apply_review(proposal, self.profile, self.keyboard)
        return digest, proposal, outcome

    def review_and_notify(self) -> None:
        try:
            _, _, outcome = self.review()
            self.backend.notify(f"Claude изучил ваши исправления: {outcome.summary()}")
        except Exception as exc:
            log.warning("AI review failed: %s", exc)

    # -- lifecycle -----------------------------------------------------------

    def run(self, tray: bool = True) -> None:
        self.backend.start(self.submit)
        worker = threading.Thread(target=self._worker, name="switcher-engine", daemon=True)
        worker.start()
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
            self.stop_event.set()
            self.backend.stop()
            worker.join(timeout=2)
            self.profile.close()
