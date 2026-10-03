"""When to reinstall a keyboard hook that seems gone: keys reach Raw Input, the hook sees none.

A reinstall makes Switcher forget the word being typed, so it must not happen in a burst: a new
hook gets a moment to start seeing keys, the hook must have been silent for a while, and after a
few reinstalls in a short time the watchdog pauses instead of fighting on.
"""

from __future__ import annotations

import logging
import time
from typing import Callable

log = logging.getLogger(__name__)


class MissDetector:
    MISSING = 8           # raw key events in a row the hook did not see (about four keystrokes)
    QUIET = 1.0           # ...while the hook saw nothing for this long, seconds
    GRACE = 2.0           # a new hook gets this long to start seeing keys
    MAX_RESTARTS = 3      # at most this many reinstalls…
    WINDOW = 60.0         # …within this many seconds
    BACKOFF = 300.0       # then no more for this long

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.missed = 0
        self.hook_seen_at = clock()
        self.restarted_at = float("-inf")
        self.recent: list[float] = []
        self.paused_until = 0.0

    def hook_event(self) -> None:
        """The hook saw a key."""
        self.missed = 0
        self.hook_seen_at = self.clock()

    def restarted(self) -> None:
        """The hook was put in afresh (for whatever reason): give it time."""
        self.missed = 0
        self.restarted_at = self.clock()

    def raw_event(self, elsewhere: Callable[[], bool] | None = None) -> bool:
        """Raw Input saw a key; True when the hook should be reinstalled now.

        ``elsewhere()`` tells whether the keys go where a hook gets none: a program run as
        administrator.  Then the hook is fine, and reinstalling it would only lose the word typed.
        """
        now = self.clock()
        self.missed += 1
        if (self.missed < self.MISSING or now - self.hook_seen_at < self.QUIET
                or now - self.restarted_at < self.GRACE or now < self.paused_until):
            return False
        if elsewhere is not None and elsewhere():
            self.missed = 0
            return False
        self.recent = [t for t in self.recent if now - t < self.WINDOW] + [now]
        if len(self.recent) > self.MAX_RESTARTS:
            self.paused_until = now + self.BACKOFF
            self.recent.clear()
            log.warning("the keyboard hook keeps missing keys: not reinstalling it for %.0f min", self.BACKOFF / 60)
            return False
        self.restarted()
        return True
