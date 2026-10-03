"""Keys must keep being handled: a dead or hung engine thread is replaced, a crash in it is survived."""

import queue
import threading
import time

from switcher import app as app_module
from switcher.app import App


class FakeBackend:
    def __init__(self):
        self.heals = 0

    def heal(self):
        self.heals += 1
        return None


class FakeController:
    def __init__(self):
        self.handled, self.resets = [], []

    def handle(self, event):
        self.handled.append(event)

    def reset(self, why=""):
        self.resets.append(why)


class FakeProfile:
    def __init__(self, fail=False):
        self.fail, self.flushes = fail, 0

    def flush(self):
        self.flushes += 1
        if self.fail:
            raise OSError("disk I/O error")


def make_app(profile=None):
    app = App.__new__(App)
    app.queue, app.stop_event = queue.Queue(), threading.Event()
    app.controller, app.backend, app.profile = FakeController(), FakeBackend(), profile or FakeProfile()
    app._worker_gen, app._worker_thread, app._busy_since, app.recoveries = 0, None, 0.0, 0
    app.recovery_log = []
    return app


def wait_for(condition, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


def test_a_crash_in_handling_or_saving_does_not_stop_the_engine(monkeypatch):
    monkeypatch.setattr(app_module, "FLUSH_EVERY", 0.0)  # save (and fail) after every item
    app = make_app(FakeProfile(fail=True))
    app._start_worker()
    app.post(lambda: 1 / 0)
    assert wait_for(lambda: app.profile.flushes >= 1)
    app.submit("key")
    assert wait_for(lambda: app.controller.handled == ["key"])
    assert app._worker_thread.is_alive() and app.profile.flushes >= 2
    app.stop_event.set()


def test_a_dead_engine_thread_is_replaced():
    app = make_app()
    app._start_worker()
    old = app._worker_thread
    app._worker_gen += 1  # makes the running thread quit, as if it had died
    assert wait_for(lambda: not old.is_alive())
    assert app.check_health() == ["engine"]
    assert app._worker_thread is not old and app._worker_thread.is_alive()
    app.submit("key")
    assert wait_for(lambda: app.controller.handled == ["key"])
    assert app.controller.resets == ["engine-restart"] and app.recoveries == 1
    app.stop_event.set()


def test_a_hung_engine_thread_is_replaced(monkeypatch):
    monkeypatch.setattr(app_module, "STUCK_AFTER", 0.2)
    app = make_app()
    app._start_worker()
    hung = app._worker_thread
    release = threading.Event()
    app.post(release.wait)  # an item that never finishes
    assert wait_for(lambda: app._busy_since)
    time.sleep(0.3)
    assert app.check_health() == ["engine"]
    app.submit("key")
    assert wait_for(lambda: app.controller.handled == ["key"])  # the new thread answers meanwhile
    release.set()
    assert wait_for(lambda: not hung.is_alive())  # and the old one quits once unstuck
    app.stop_event.set()


def test_a_healthy_app_is_left_alone():
    app = make_app()
    app._start_worker()
    worker = app._worker_thread
    assert app.check_health() == [] and app._worker_thread is worker and app.backend.heals == 1
    app.stop_event.set()


def test_a_dead_keyboard_listener_is_restarted():
    from switcher.platform.base import BaseBackend

    class Listener:
        def __init__(self, alive):
            self.alive = alive

        def is_alive(self):
            return self.alive

        def stop(self):
            pass

    backend = BaseBackend.__new__(BaseBackend)
    backend._lock, backend._listeners, backend._sink = threading.RLock(), [Listener(False)], None
    restarted = []
    backend._restart_keyboard_hook = lambda: restarted.append(1)
    assert backend.heal() == "keyboard" and restarted == [1]
    backend._listeners = [Listener(True)]
    assert backend.heal() is None and restarted == [1]


def test_the_self_check_says_what_works_and_what_does_not(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from switcher.config import Config

    monkeypatch.setenv("SWITCHER_HOME", str(tmp_path))
    app = make_app()
    app.config, app.started_at = Config(), time.time()
    app.controller.enabled = True
    app.profile.threshold_offset = lambda name: 0.0
    app.ai_ready = lambda: False

    class Listener:
        alive = True

        def is_alive(self):
            return self.alive

    listener = Listener()
    app.backend = SimpleNamespace(_listeners=[listener], keys_seen=12, last_key_at=time.monotonic() - 3,
                                  injected_dropped=0, accept_injected=False, _hkls={"en": 1, "ru": 2},
                                  current_layout=lambda: "ru", active_app=lambda: "telegram", heal=lambda: None,
                                  elevated_apps=["Acrobat"])
    app.config.snippets = {"015": "015-510-400_4_"}
    app._start_worker()
    report = app.diagnostics()
    assert "Обработка клавиш: работает" in report and "Перехват клавиатуры: работает, нажатий 12" in report
    assert "Дописывание: шаблонов 1" in report
    assert "Запущены от имени администратора (Windows не пускает туда Switcher): Acrobat" in report
    assert "Раскладки Windows: en, ru; сейчас ru в telegram" in report and "Ошибки в журнале: нет" in report
    listener.alive = False
    app._worker_gen += 1  # the engine thread quits
    assert wait_for(lambda: not app._worker_thread.is_alive())
    report = app.diagnostics()
    assert "Обработка клавиш: ОСТАНОВЛЕНА" in report and "Перехват клавиатуры: ОСТАНОВЛЕН" in report
    app.stop_event.set()


def test_injected_keys_are_trusted_when_they_are_all_there_is():
    from switcher.platform.base import BaseBackend

    backend = BaseBackend.__new__(BaseBackend)
    backend._lock, backend._busy_until, backend.accept_injected = threading.RLock(), 0.0, False
    backend.keys_seen = backend.injected_dropped = backend._injected_run = 0
    backend.last_key_at, backend.ECHO_GRACE = 0.0, 0.05
    emitted, notes = [], []
    backend._describe = lambda key: ("char", key, key)
    backend._emit = lambda kind, name, char, code, mods=None: emitted.append(char)
    backend.notify = notes.append
    backend.TRUST_INJECTED_AFTER = 3
    backend._on_press("a", injected=True)
    backend._on_press("b", injected=False)  # a real key in between: still not trusted
    backend._on_press("c", injected=True)
    backend._on_press("d", injected=True)
    assert emitted == ["b"] and not backend.accept_injected
    backend._on_press("e", injected=True)  # the third in a row
    backend._on_press("f", injected=True)
    assert backend.accept_injected and emitted == ["b", "f"] and len(notes) == 1
    assert backend.injected_dropped == 4 and backend.keys_seen == 2


def test_the_hook_watchdog_does_not_reinstall_in_a_burst():
    from switcher.platform.hook_health import MissDetector

    now = [100.0]
    d = MissDetector(clock=lambda: now[0])

    def raw(n, step=0.05):
        fired = 0
        for _ in range(n):
            now[0] += step
            fired += d.raw_event()
        return fired

    d.hook_event()
    assert raw(8) == 0  # only 0.4 s since the hook last saw a key: maybe it is just slow
    assert raw(20) == 1  # silent for over a second: reinstall, once
    assert raw(20) == 0  # the new hook gets two seconds before it is judged
    now[0] += 2
    assert raw(10) == 1
    now[0] += 2
    assert raw(10) == 1
    now[0] += 2
    assert raw(10) == 0  # a fourth time within a minute: stop fighting, pause instead
    now[0] += 290
    assert raw(10) == 0
    now[0] += 20
    assert raw(10) == 1  # five minutes later it may try again
    d.hook_event()
    assert raw(5) == 0  # a working hook resets everything


def test_keys_typed_in_an_administrators_program_do_not_make_the_watchdog_reinstall():
    from switcher.platform.hook_health import MissDetector

    now = [100.0]
    d = MissDetector(clock=lambda: now[0])
    asked = []

    def elevated():
        asked.append(1)
        return True

    fired = 0
    for _ in range(200):  # a long file name typed in Acrobat run as administrator
        now[0] += 0.1
        fired += d.raw_event(elevated)
    assert fired == 0 and asked  # the hook is fine: Windows just keeps it out of there
    assert len(asked) < 40  # asked now and then, not on every key
    fired = sum(d.raw_event(lambda: False) for _ in range(10))
    assert fired == 1  # back in an ordinary program and still unseen: the hook did die
