"""User settings (``config.json`` next to the profile). Unknown keys are ignored."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any


@dataclass
class Hotkeys:
    # "double_shift" = tap Shift twice; otherwise pynput syntax, e.g. "<ctrl>+<alt>+x".
    convert_last: str = "double_shift"      # convert the last word / undo the last auto-switch
    convert_selection: str = "<ctrl>+<alt>+c"
    ai_fix: str = "<ctrl>+<alt>+<space>"    # let Claude fix the current phrase or the selection
    toggle: str = "<ctrl>+<alt>+s"          # pause / resume auto-switching


@dataclass
class Learning:
    enabled: bool = True
    min_vocab_count: int = 2           # uses before a new word counts as "yours"
    typo_rules: bool = True            # learn "wrong word → right word" from your retypes
    typo_min_repeats: int = 2          # same fix seen this many times → rule
    journal_size: int = 5000


@dataclass
class AI:
    enabled: bool = True               # needs ANTHROPIC_API_KEY (or `ant auth login`)
    model: str = "claude-opus-5-5"
    review_every: int = 20             # corrections between automatic profile reviews (0 = only manual)
    fix_typos: bool = False            # let the AI phrase fix also correct spelling
    timeout: float = 30.0


@dataclass
class Config:
    enabled: bool = True
    auto_switch: bool = True
    threshold: float = 2.0
    look_back: bool = True             # also fix the previous short word ("e vtyz" → "у меня")
    convert_on_enter: bool = False     # Enter may already have sent the message in chats
    fix_caps_lock: bool = True         # "пРИВЕТ" → "Привет"
    ru_variant: str = "auto"           # "pc" | "mac" | "auto"
    typing_delay_ms: float = 2.0
    excluded_apps: list[str] = field(default_factory=lambda: [
        "keepass", "keepassxc", "1password", "bitwarden", "lastpass", "enpass", "dashlane",
    ])
    # Code-heavy apps: switching needs a larger margin there by default.
    careful_apps: list[str] = field(default_factory=lambda: [
        "code", "idea", "pycharm", "webstorm", "clion", "goland", "rider", "sublime_text", "vim", "nvim",
        "terminal", "iterm2", "windowsterminal", "cmd", "powershell", "wezterm", "alacritty", "kitty",
        "gnome-terminal", "konsole", "xterm",
    ])
    careful_extra: float = 1.0
    hotkeys: Hotkeys = field(default_factory=Hotkeys)
    learning: Learning = field(default_factory=Learning)
    ai: AI = field(default_factory=AI)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def _merge(cls, data: dict[str, Any]):
    kwargs = {}
    for f in fields(cls):
        if f.name not in data:
            continue
        value = data[f.name]
        default = f.default_factory() if callable(f.default_factory) else f.default  # type: ignore[misc]
        if is_dataclass(default) and isinstance(value, dict):
            value = _merge(type(default), value)
        kwargs[f.name] = value
    return cls(**kwargs)


def load_config(path: Path | None = None) -> Config:
    from .paths import config_path

    path = path or config_path()
    if not path.exists():
        config = Config()
        try:
            config.save(path)
        except OSError:
            pass
        return config
    data = json.loads(path.read_text(encoding="utf-8") or "{}")
    return _merge(Config, data)
