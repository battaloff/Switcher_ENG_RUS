"""End-to-end check of "Обновить / Откатить" on a real Windows machine (CI).

1. Reinstall over itself: install the fresh build, start it, run the update
   helper for the running app and let the app quit; the program files must be
   reinstalled, Switcher must be running again, and the user's "no autostart"
   choice must survive.
2. Real versions, when another version is published on GitHub: install that
   version, update the running app to the fresh build, then roll back to the
   published one.  Each step must end with the right version installed and
   running, and the settings untouched.

    python tools/windows_update_smoke.py
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import time
import winreg
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from switcher import __version__, autostart, updater  # noqa: E402

APP_DIR = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Switcher"
EXE = APP_DIR / "Switcher.exe"
CONFIG = Path(os.environ["APPDATA"]) / "Switcher" / "config.json"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{8F1E6A43-3C4B-4C39-9E0B-5B8C2F6D7A10}_is1"
OLD = 1_577_836_800  # 2020-01-01: marks the files from the first install


def switcher_pids() -> set[int]:
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Switcher.exe", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True).stdout
    pids = set()
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) > 1 and parts[0].lower().startswith("switcher"):
            pids.add(int(parts[1]))
    return pids


def installed_version() -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            return winreg.QueryValueEx(key, "DisplayVersion")[0]
    except OSError:
        return None


def install(setup: Path) -> None:
    subprocess.run([str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], check=True, timeout=300)


def start_app() -> int:
    subprocess.Popen([str(EXE)])
    for _ in range(30):
        time.sleep(1)
        pids = switcher_pids()
        if pids:
            time.sleep(5)  # let it settle, like a user who opens the settings later
            if switcher_pids():
                return min(pids)
    raise SystemExit("FAIL: Switcher did not start")


def update_running_app(setup: Path, pid: int, done) -> float:
    """What the button does: start the helper, then the app quits."""
    log = updater.install_log()
    log.unlink(missing_ok=True)
    started = time.time()
    updater.launch_installer(setup, autostart=autostart.is_enabled(), wait_pid=pid, relaunch=EXE)
    time.sleep(2)
    subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)  # the app quitting
    while time.time() - started < 180:
        if done() and switcher_pids() - {pid}:
            return time.time() - started
        time.sleep(2)
    if log.exists():
        print("install log:\n  " + "\n  ".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-8:]))
    raise SystemExit(f"FAIL: no running Switcher after installing {setup.name} (installed: {installed_version()})")


def stop_all() -> None:
    for pid in switcher_pids():
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
    time.sleep(2)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "missing"


def reinstall_over_itself(setup: Path) -> None:
    install(setup)
    os.utime(EXE, (OLD, OLD))
    autostart.disable()  # the user turned autostart off in the settings window
    pid = start_app()
    took = update_running_app(setup, pid, lambda: EXE.exists() and EXE.stat().st_mtime > OLD + 1)
    print(f"OK   reinstall over itself: running again after {took:.0f} s")
    if autostart.is_enabled():
        raise SystemExit("FAIL: the update turned autostart back on")
    print("OK   autostart stayed off")
    stop_all()


def published_other_version() -> updater.Release | None:
    releases = updater.fetch_releases()
    return next((r for r in releases if r.version != __version__ and r.key >= (0, 2, 0)), None)


def between_versions(setup: Path, other: updater.Release) -> None:
    folder = Path(tempfile.mkdtemp(prefix="switcher-published-"))
    published = updater.download(other, folder=folder)
    print(f"downloaded published {other.version} ({published.stat().st_size / 1_048_576:.0f} MB, checksum ok)")
    install(published)
    if installed_version() != other.version:
        raise SystemExit(f"FAIL: installed {installed_version()}, expected {other.version}")
    pid = start_app()
    settings = digest(CONFIG)
    forward = "update" if updater.parse_version(__version__) > other.key else "rollback"
    backward = "rollback" if forward == "update" else "update"

    took = update_running_app(setup, pid, lambda: installed_version() == __version__)
    print(f"OK   {forward} {other.version} → {__version__}: running again after {took:.0f} s")
    pid = min(switcher_pids())
    took = update_running_app(published, pid, lambda: installed_version() == other.version)
    print(f"OK   {backward} {__version__} → {other.version}: running again after {took:.0f} s")
    if digest(CONFIG) != settings:
        raise SystemExit("FAIL: the settings file changed during update and rollback")
    print("OK   settings untouched")
    stop_all()


def main() -> int:
    setup = max((ROOT / "dist").glob("SwitcherSetup-*.exe"), key=lambda p: p.stat().st_mtime)
    print(f"fresh build: {setup.name}")
    try:
        reinstall_over_itself(setup)
        other = published_other_version()
        if other is None:
            print("SKIP update/rollback between versions: no other version is published yet")
        else:
            between_versions(setup, other)
    finally:
        stop_all()
        uninstaller = APP_DIR / "unins000.exe"
        if uninstaller.exists():
            subprocess.run([str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], timeout=120)
    print("UPDATE PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
