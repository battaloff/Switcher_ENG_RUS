"""Where the switcher keeps its config, learned profile and model cache."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP = "Switcher"


def _home_override() -> Path | None:
    value = os.environ.get("SWITCHER_HOME")
    return Path(value) if value else None


def data_dir() -> Path:
    override = _home_override()
    if override:
        path = override
    elif sys.platform == "win32":
        path = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / APP
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / APP
    else:
        path = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP.lower()
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_dir() -> Path:
    override = _home_override()
    if override:
        path = override / "cache"
    elif sys.platform == "win32":
        path = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / APP / "cache"
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Caches" / APP
    else:
        path = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / APP.lower()
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return data_dir() / "config.json"


def profile_path() -> Path:
    return data_dir() / "profile.sqlite3"


def log_path() -> Path:
    return data_dir() / "switcher.log"
