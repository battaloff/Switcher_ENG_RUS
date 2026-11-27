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
ruff check .                                 # has pre-existing findings (how many depends on the ruff version): add none
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

A version the user wants taken back goes into `packaging/windows/WITHDRAWN.txt` (0.7.5 and 0.7.6
are there): CI never publishes it again and `releases.json` leaves it out. This session can't delete
or edit GitHub releases (403); the user deletes them on github.com. Items of a withdrawn version
that stay in the code go into the next CHANGELOG section again, so the user sees them.

The repository must stay **public**: the app (and the update E2E) download `releases.json` and the
installer anonymously, so a private repository answers 404 and in-app updates stop working. If the
update E2E fails with «GitHub ответил ошибкой 404», check the repository's visibility first and ask
the user to make it public; never change it yourself.

Reading CI from a cloud session: job logs are not downloadable (blob redirect), so failures are
surfaced as annotations — `tests/conftest.py` writes one per failed test, `windows_smoke.py` turns
every `FAIL` line into an error and `note()` into a notice. Read them with
`gh api repos/battaloff/Switcher_ENG_RUS/check-runs/<job id>/annotations` (job id from
`actions/runs/<run id>/jobs`). Cancelling, re-running and manually starting (`workflow_dispatch`)
a workflow run from here are all refused (403): only a push starts CI.

The Notepad check runs through `tools/ci_notepad_check.ps1`, which also sends what the check has
written so far to check runs named «Notepad check: …» every few seconds. A job that hangs until its
45 minutes run out keeps no annotations at all, so read those instead:
`gh api repos/battaloff/Switcher_ENG_RUS/commits/<sha>/check-runs --jq '.check_runs[] | select(.name|test("Notepad check")) | .output.title, .output.text'`.

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
`convert_selection` (Shift+Pause) only swaps the layout of the selection, as Punto does, and keeps
it selected; `fix_selection` (own hotkey, unassigned by default) fixes it word by word and hands
what it cannot fix to Claude (`ai.py`).

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

**«Сохранить как» panel.** `savebar.py` (pure: the dialog's folder from its address bar text,
what a button copies, the "PDF" plan, placement), `platform/win_savebar.py` (the dialog's parts:
the "Адрес: …" toolbar, the row of «Новая папка» between the address band and the panes, the file
name box; opening a folder through the address bar — a click posted to its empty part after the
last folder (works in any language of Windows), else Ctrl+L, else Alt+D (English only: it did nothing
on the user's Russian Windows); never F4, which drops the recent-folders list over the dialog and
made the user withdraw 0.7.5–0.7.6 — and only failing that the file name box with WM_SETTEXT +
Enter, never typed (typing drops its suggestions down)), `savebar_ui.py` (a non-activating Tk strip on the Tk thread that polls the foreground
window; button work runs on its own thread). Its hotkeys are swallowed in
`WindowsBackend._hook_filter` only while its dialog is in front. Settings: `Config.save_bar`.

**CorelDRAW docker.** The user's docker «POLITEXT» was made by «Corel Docker Studio» (a .NET tool;
not on GitHub, not found on their PC) and lives in `C:\Program Files\Corel\CorelDRAW Graphics
Suite\26\Programs64\Addons\Politext`: `Politext.html` (the panel, an IE web view talking to
`window.external.Application`; its buttons are the JSON `var config = {…};`), `dockerstudio.json`
(the same list as Docker Studio's project), `Politext.gms` + `Macros.bas` (a VBA macro per button,
numbers compiled in). `coreldocker.py` reads and rewrites the two lists (`Docker.load/save/undo`,
copies in `%APPDATA%\Switcher\corel_docker`); a button whose numbers no longer match its macro
loses `generatedMacroName`, and the page's own JavaScript does the same work. The folder is in
Program Files: `platform/win_access.py` runs `icacls` elevated once (UAC) to give the user Modify on
that folder. Page «Докер Corel» in `gui.py`, dialogs in `coreldocker_dialogs.py`. «Сохранить докер в
файл…» zips the docker (no .gms/.bas/backups); «Установить из файла…» installs it into every chosen
CorelDRAW (`corel_targets`: …\Programs64\CorelDRW.exe) as a docker of its own — folder
`<Caption>_Switcher`, new GUIDs for its command, page and docker in AppUI/UserUI.xslt, the caption
«POLITEXT 2» when one is taken, no VBA (a .gms project name would clash), Windows' Zone.Identifier
mark removed (with it the docker shows empty), `switcher-install.json` so a reinstall updates in
place; `win_access.make_writable` creates the folders in Program Files with one UAC question.
«Вид и проверки…» (`Look`: buttons in a row, height, caption size) adds one `<style id="switcher-look">`
before `</head>` that overrides the page's `.preset` rule; its switches (`Docker.options`) turn off the page's
document check (`showDocumentAudit`), refreshing it on every CorelDRAW event (`enableLiveUpdates`), checking
when the docker opens (`auditOnOpen`) and each check (`auditChecks`: spot, rgb, fonts, ole, effects) — in
CorelDRAW 27 on the user's other PC the check hung Corel for 1–2 minutes (each call into Corel is slow there,
the check makes ~16–20 per object), while their myScripts docker (only `GMSManager.RunMacro`) did not. Every
write also patches the page (`improved`): `getApplication`/`getCorelMajor` remember their answer (the page
asked twice per button), and `lighter_check` — all or nothing, else the page is left and the dialog shows no
per-check switches — makes the check skip what is switched off and run a slice at a time (`auditInSteps`:
40 ms of work, then `setTimeout`, so Corel gets its window back; the page's own `refreshAudit` stays as
`refreshAuditAtOnce` for the preview and "no document"). A second step (0.8.5, on top of the first, so
pages patched by 0.8.4 get it too) takes PANTONE/Spot and OLE from the user's old docker «myScripts»: its
page makes one call, `GMSManager.RunMacro("myScripts", "Macros.getDocumentInfo")` (VBA inside Corel, fast;
returns "Spot colors: … / OLE objects: Found|Not found"), and that is why it never hung; the page asks it
when that project is loaded and walks the objects itself only for RGB/fonts/effects. The macro's source is
in the user's `TemplateWebDocker.gms` (olevba reads it once the bytes before the OLE header are cut off).
`test_the_lighter_check_finds_what_the_page_found` and `test_pantone_and_ole_come_from_the_old_dockers_macro`
run the cut-down check of `conftest.DOCKER_AUDIT` in node before and after the patch.
A docker installed into several CorelDRAWs (same GUIDs in `switcher-install.json`) is one docker: the page
writes every change into all copies (`copies_of`), and copies that differ are made the same as the one
changed last when the page opens (0.8.2 changed only the first, CorelDRAW 26, while the user worked in 27). CorelDRAW is not on
CI: check a changed page by opening it in Chromium with a fake `window.external` (Playwright) and
clicking buttons; never commit the user's own docker files (tests use `conftest.make_docker`).

**Packaging.** `packaging/windows/build.py`: prebuilt language model → PyInstaller one-dir
`Switcher.exe` (linux/macos backends excluded) → `--selftest` of the bundle → Inno Setup installer.
Modules imported only on demand must also be imported in `gui_main.selftest` so a missing one fails
the build, not the user.

## Scripts for the user's own work (not part of Switcher)

The user also asks for small scripts for their print shop (AutoHotkey, cmd). Hand them over as
files (`SendUserFile`), don't commit them. Their work files are on `\\192.168.1.41\f\` (the owner's
PC sees the same share as `F:\`); a cloud session can't reach that network, so ask for a listing.

- **.cmd/.bat: English letters only.** cmd misreads Russian letters in a batch file on the user's
  PCs (`chcp 65001` doesn't fix it reliably). Put Russian paths and messages in PowerShell: either
  an ASCII cmd header that runs the rest of its own file (`set "SELF=%~f0"`, then
  `[IO.File]::ReadAllText($env:SELF, [Text.Encoding]::UTF8)` → `Invoke-Expression` from a marker
  on, `exit /b` before the marker), or a separate `.ps1` saved as UTF-8 with BOM. Save the cmd as
  UTF-8 without BOM, CRLF; a lone `%` in the cmd part is eaten (write `%%`).
- AutoHotkey: UTF-8 with BOM (v1 misreads Russian without it); the user's scripts are v1.

## Testing conventions

`tests/conftest.py`'s `FakeScreen` is a text field plus a simulated user wired to a real
`Controller`: `s.keys("ghbdtn ")` presses physical QWERTY keys under the current layout,
`s.write("привет ", RU)` types intended text, `s.double_shift()`, `s.switch_layout(EN)`,
`s._event("press", "save-dialog")`; assert on `s.text`. Reproduce a user's report this way first,
then fix, then keep it as a test. Windows-only behaviour gets a check in `tools/windows_smoke.py`;
in that script, hold modifiers until an AutoHotkey hotkey has finished typing and avoid F10 or
bare Alt combos (they open Notepad's menu and derail every later check). Its keyboard
(`GuardedUser`) presses keys only while Notepad is in front and stops the check otherwise (`Lost`):
keys typed blind once reached the CI machine's own terminal and froze the whole job. Close a Yes/No
question by its button (Esc does nothing there), never leave one open.
