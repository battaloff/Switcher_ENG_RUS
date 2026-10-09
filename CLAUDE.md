# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Switcher: a Punto Switcher–like RU/EN keyboard-layout auto-switcher for Windows with typo
autocorrect, learning from the user's corrections, Claude (Anthropic API) integration, snippets,
an AutoHotkey scripts manager, an installer and a one-click updater. **What the user wants from
the program is in [REQUIREMENTS.md](REQUIREMENTS.md)** — read it before changing behaviour; its
quoted examples are real failures that must keep working. README.md documents features for users.

The user is a non-developer who speaks Russian: talk to them in Russian, be direct, say plainly
when something is impossible or not yet verified, and find causes from evidence (logs, tests,
the Windows E2E) instead of guessing. All UI text, notifications and CHANGELOG items are Russian;
code, comments and commit messages are English. Only Windows matters to the user (macOS/Linux
backends exist but are not shipped).

## Commands

```bash
pip install -e ".[gui,dev]"                  # gui = customtkinter, pystray, pillow
python -m pytest -q                          # all tests (GUI tests skip without tkinter/a display)
xvfb-run -a python -m pytest -q              # include the GUI tests on Linux
python -m pytest tests/test_controller.py -k two_capitals   # one test / a few
ruff check .                                 # 12 pre-existing findings (E741, F401 in tray.py): don't add more
python -m switcher explain ghbdtn            # how the engine scores a word and why it converts or not
xvfb-run -a -s "-screen 0 1280x900x24" python tools/gui_screenshots.py OUT_DIR   # every settings page, light+dark
```

Accuracy tools (need `wordfreq`; build models without held-out words, ~20 s). Any change to
thresholds or scoring must be measured with them, and the new numbers written into the docstring
of the tuning, README «Точность» and the CHANGELOG:

- `tools/evaluate.py` — end-of-word decisions (false switches / misses);
- `tools/evaluate_early.py` — switching after the first letters (`--sweep` for variants);
- `tools/evaluate_spelling.py` — typo autocorrect (`--sweep`).

Windows-only end-to-end checks run on CI: `tools/windows_smoke.py` (real typing into Notepad,
Save As, snippets, AutoHotkey, recovery after lock/sleep), `tools/windows_update_smoke.py`,
`packaging/windows/check_install.ps1`; build with `python packaging/windows/build.py`.

## Releasing (automatic)

Raise `__version__` in `switcher/__init__.py` and add a `## X.Y.Z — DD.MM.YYYY` section to
CHANGELOG.md whose items start with «Новое:» or «Исправлено:» (tests/test_updater.py enforces
it), then push to the default branch (`claude/remote-control-za56cu`). `.github/workflows/windows.yml`
builds the installer, runs every check on windows-latest and publishes the GitHub release
(installer + `releases.json`) only if all pass; the running app then offers the update. A pushed
version that never got published is folded into the next CHANGELOG section. Pushes without a new
version are only built and tested.

Reading CI from a cloud session: job logs are not downloadable (blob redirect), so failures are
surfaced as annotations — `tests/conftest.py` writes one per failed test, `windows_smoke.py` turns
every `FAIL` line into an error and `note()` into a notice. Read them with
`gh api repos/battaloff/Switcher_ENG_RUS/check-runs/<job id>/annotations` (job id from
`actions/runs/<run id>/jobs`). Cancelling a workflow run from here was refused (403): don't count on it.

## Architecture

**Event flow.** The OS backend (`switcher/platform/`, `create_backend`) runs a pynput keyboard
hook; each key becomes a `KeyEvent` put on `App.queue` (`app.py`). A single worker thread
(`App._worker`) feeds them to `Controller.handle` — the controller is single-threaded by design;
anything else that must touch it goes through `App.post(fn)`. A health thread restarts a dead or
hung worker and asks the backend to `heal()` its hook. Besides real keys, the backend emits
synthetic events the controller understands: `mouse`, `save-dialog`, `hook-restored` (keys were
missed: reset), `hook-reinstalled` (precaution: keep the word), `foreign-input` (AutoHotkey typed:
forget the word).

**Deciding.** `Controller` (`controller.py`) tracks the word being typed and the last few finished
words, and on each delimiter commits the word: `Engine.decide` (`engine.py`) scores the typed
reading against the other-layout reading (lexicon Zipf from `langmodel.py`, letter n-gram
plausibility for unknown words, user rules from `profile.py`, phrase context, per-app thresholds),
then two-capitals / Caps Lock fixes and `Speller.suggest` (`speller.py`, one-edit candidates with
keyboard-aware costs). `decide_prefix` does the Punto-style early switch while typing. Look-back
converts short previous words together with the current one. Every change the controller makes
records what was typed, so double Shift (`convert_last`) restores exactly the original.
`convert_selection` (Shift+Pause) fixes selected text word by word and hands what it cannot fix to
Claude (`ai.py`).

**Learning.** `learner.py` turns undos, manual conversions and retypes into layout rules, typo
rules, personal vocabulary and per-app threshold offsets, all in a local SQLite profile.
`engine.risky_rule` forbids learned/AI rules for a single key or for keys that read as a very
common word in the other language (they once turned every «я» into «z»); hand-added rules still
apply. Claude's periodic review (`Assistant.review` → `apply_review`) proposes rules that are
validated locally before they touch the profile.

**Windows backend** (`platform/windows.py` + helpers). Characters are derived from the physical
key and the *foreground window's* layout (pynput's own translation is wrong), numpad included.
Text is typed as Unicode `SendInput` tagged with `OWN_INPUT` so the hook drops our own keys;
AutoHotkey's tagged input (`AHK_INPUT`) is dropped too and resets the word; other injected input is
trusted only after a run of it (remote desktop). Modifiers are re-read from the OS
(`GetAsyncKeyState`) because the lock screen swallows releases. `win_watchdog.py` + `hook_health.py`
reinstall a hook Windows silently dropped (Raw Input sees keys the hook doesn't), and after
sleep/unlock/a long break; keys typed into an elevated program are not mistaken for a dead hook,
and such programs are reported to the user. `win_events.py` watches for Save As/Export dialogs
(`platform/dialogs.py`: by title or by the Save button) to switch to English and to allow snippets
there; slow dialogs are re-checked.

**GUI and app shell.** `gui_main.py` is the entry point of `Switcher.exe` (single instance,
logging to `%APPDATA%\Switcher\switcher.log`, `--selftest` used by the build). Tk runs on the main
thread (`gui.Ui`); other threads must hand UI work over with `Ui.call(fn)` or a value polled from
the Tk thread — never call Tk from another thread. `tray.py` (pystray) menus are rebuilt with
`update_menu()` when state changes. Settings live in `config.py` (dataclasses ↔ `config.json`;
unknown keys ignored, new fields get defaults; `_migrate` for real changes). `updater.py` reads
`releases.json` from GitHub Releases and reinstalls silently (update or rollback).

**AutoHotkey.** `ahk.py` (`AhkManager`) lists configured + running scripts (found by their hidden
`AutoHotkey` windows, paths compared via `realpath` because of 8.3 short names), starts them like a
double click, stops/reloads them with their tray-menu WM_COMMANDs, syntax-checks with
`/validate`/`/iLib`, and keeps the file's encoding. `ahk_editor.py` is the editor;
`ahk_conflicts.py` parses hotkeys/hotstrings and reports clashes with Switcher's keys and
snippets; `ahk_hotkeys.py` relabels and adds hotkeys for the «Клавиши AHK» page.

**Packaging.** `packaging/windows/build.py`: prebuilt language model → PyInstaller one-dir
`Switcher.exe` (linux/macos backends excluded) → `--selftest` of the bundle → Inno Setup installer.
Modules imported only on demand must also be imported in `gui_main.selftest` so a missing one fails
the build, not the user.

## Testing conventions

`tests/conftest.py`'s `FakeScreen` is a text field plus a simulated user wired to a real
`Controller`: `s.keys("ghbdtn ")` presses physical QWERTY keys under the current layout,
`s.write("привет ", RU)` types intended text, `s.double_shift()`, `s.switch_layout(EN)`,
`s._event("press", "save-dialog")`; assert on `s.text`. Reproduce a user's report this way first,
then fix, then keep it as a test. Windows-only behaviour gets a check in `tools/windows_smoke.py`;
in that script, hold modifiers until an AutoHotkey hotkey has finished typing and avoid F10 or
bare Alt combos (they open Notepad's menu and derail every later check).
