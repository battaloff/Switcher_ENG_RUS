"""Start the switcher at login: ``switcher autostart on|off``."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _python(windowless: bool = False) -> str:
    exe = Path(sys.executable)
    if windowless and sys.platform == "win32":
        pythonw = exe.with_name("pythonw.exe")
        if pythonw.exists():
            return str(pythonw)
    return str(exe)


def entry_path() -> Path:
    if sys.platform == "win32":
        startup = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        return startup / "Switcher.vbs"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "LaunchAgents" / "com.switcher.agent.plist"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "autostart" / "switcher.desktop"


def enable() -> Path:
    path = entry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        command = f'""{_python(windowless=True)}"" -m switcher run'
        path.write_text(f'CreateObject("WScript.Shell").Run "{command}", 0, False\r\n', encoding="utf-8")
    elif sys.platform == "darwin":
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
    return path


def disable() -> bool:
    path = entry_path()
    if path.exists():
        path.unlink()
        return True
    return False
