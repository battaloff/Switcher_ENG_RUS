"""Start the switcher at login.

Windows uses the ``HKCU\\...\\Run`` registry value — the same one the installer
writes, so the checkbox in the settings and the installer option agree.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE = "Switcher"


def _python(windowless: bool = False) -> str:
    exe = Path(sys.executable)
    if windowless and sys.platform == "win32":
        pythonw = exe.with_name("pythonw.exe")
        if pythonw.exists():
            return str(pythonw)
    return str(exe)


def command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    return f'"{_python(windowless=True)}" -m switcher run'


def entry_path() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "LaunchAgents" / "com.switcher.agent.plist"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "autostart" / "switcher.desktop"


def is_enabled() -> bool:
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                winreg.QueryValueEx(key, VALUE)
            return True
        except OSError:
            return False
    return entry_path().exists()


def enable() -> str:
    if sys.platform == "win32":
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.SetValueEx(key, VALUE, 0, winreg.REG_SZ, command())
        return f"HKCU\\{RUN_KEY}\\{VALUE}"
    path = entry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        path.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.switcher.agent</string>
  <key>ProgramArguments</key>
  <array><string>{_python()}</string><string>-m</string><string>switcher</string><string>run</string></array>
  <key>RunAtLoad</key><true/>
</dict>
</plist>
""", encoding="utf-8")
    else:
        path.write_text(f"""[Desktop Entry]
Type=Application
Name=Switcher
Comment=Умный переключатель раскладки RU/EN
Exec={_python()} -m switcher run
X-GNOME-Autostart-enabled=true
""", encoding="utf-8")
    return str(path)


def disable() -> bool:
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, VALUE)
            return True
        except OSError:
            return False
    path = entry_path()
    if path.exists():
        path.unlink()
        return True
    return False
