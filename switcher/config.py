"""User settings (``config.json`` next to the profile). Unknown keys are ignored."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any


@dataclass
class Hotkeys:
    # "double_shift" / "double_ctrl" = tap the key twice; otherwise pynput syntax, e.g.
    # "<ctrl>+<alt>+x" or "<pause>"; "" = off.  The settings window records them.
    convert_last: str = "double_shift"      # convert the last word / undo the last auto-switch
    convert_selection: str = "<shift>+<pause>"  # fix the selected text (Claude if connected), like Punto
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
    enabled: bool = True
    api_key: str = ""                  # set from the settings window (encrypted); else ANTHROPIC_API_KEY
    model: str = "claude-opus-5-5"
    review_every: int = 20             # corrections between automatic profile reviews (0 = only manual)
    fix_typos: bool = False            # let the AI phrase fix also correct spelling
    timeout: float = 30.0


@dataclass
class Updates:
    check_automatically: bool = True   # look for a new version on GitHub twice a day


@dataclass
class Config:
    enabled: bool = True
    auto_switch: bool = True
    threshold: float = 2.0
    look_back: bool = True             # also fix the previous short word ("e vtyz" → "у меня")
    convert_on_enter: bool = False     # Enter may already have sent the message in chats
    fix_caps_lock: bool = True         # "пРИВЕТ" → "Привет"
    fix_two_capitals: bool = True      # "ЗДравствуйте" → "Здравствуйте"
    writes_uzbek: bool = False         # leave Uzbek words alone: "олдин" is not a typo of "один"
    english_in_save_dialogs: bool = True  # "Save As": the file name is typed on the English layout
    # what the user starts typing → what Switcher completes it to: {"015": "015-510-400_4_"}
    snippets: dict[str, str] = field(default_factory=dict)
    snippets_enabled: bool = True      # off: the snippets stay in the list, nothing is completed
    # only in "Save As" / "Export" file dialogs: "745" typed as a size in CorelDRAW stays "745"
    snippets_only_in_save_dialogs: bool = True
    # AutoHotkey scripts in the manager: path → start it together with Switcher
    ahk_scripts: dict[str, bool] = field(default_factory=dict)
    early_switch: bool = True          # like Punto: switch after the first 3-4 letters, not at the end
    autocorrect: bool = True           # fix typos at the end of a word: "превет" → "привет"
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
    updates: Updates = field(default_factory=Updates)
    config_version: int = 1            # bumped when an update has to adjust saved settings

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
    config = _merge(Config, data)
    if _migrate(config, data):
        try:
            config.save(path)
        except OSError:
            pass
    return config


OLD_SELECTION_HOTKEY = "<ctrl>+<alt>+c"


def _migrate(config: Config, data: dict[str, Any]) -> bool:
    """Bring a config file from an older version up to date; True if something changed."""
    if data.get("config_version", 0) >= 1:
        return False
    # 0.3.1: fixing the selection moved to Punto's Shift+Pause, unless the user chose their own key
    hotkeys = vars(config.hotkeys)
    if hotkeys["convert_selection"] == OLD_SELECTION_HOTKEY and "<shift>+<pause>" not in hotkeys.values():
        config.hotkeys.convert_selection = "<shift>+<pause>"
    config.config_version = 1
    return True
