"""Pick the backend for the current OS."""

from __future__ import annotations

import sys

from ..layouts import Keyboard


def create_backend(keyboard: Keyboard, typing_delay_ms: float = 2.0):
    if sys.platform == "win32":
        from .windows import WindowsBackend as cls
    elif sys.platform == "darwin":
        from .macos import MacBackend as cls
    else:
        from .linux import LinuxBackend as cls
    return cls(keyboard, typing_delay_ms)
