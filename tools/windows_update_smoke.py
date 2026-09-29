"""End-to-end check of "Обновить / Откатить" on a real Windows machine (CI).

Installs the freshly built SwitcherSetup-*.exe, starts Switcher, then does what
the button does: runs the update helper for the running app and lets the app
quit.  Passes when the program files were reinstalled, Switcher is running
again, and the user's "no autostart" choice survived the reinstall.

    python tools/windows_update_smoke.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from switcher import autostart, updater  # noqa: E402

APP_DIR = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Switcher"
EXE = APP_DIR / "Switcher.exe"
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


def main() -> int:
    setup = max((ROOT / "dist").glob("SwitcherSetup-*.exe"), key=lambda p: p.stat().st_mtime)
    print(f"installing {setup.name}")
    subprocess.run([str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], check=True, timeout=300)
    os.utime(EXE, (OLD, OLD))
    autostart.disable()  # the user turned autostart off in the settings window

    app = subprocess.Popen([str(EXE)])
    time.sleep(8)
    if app.poll() is not None:
        print(f"FAIL: Switcher exited on its own (code {app.returncode})")
        return 1

    log = updater.install_log()
    log.unlink(missing_ok=True)
    started = time.time()
    updater.launch_installer(setup, autostart=autostart.is_enabled(), wait_pid=app.pid, relaunch=EXE)
    time.sleep(2)
    app.terminate()  # the app quits right after starting the helper
    app.wait(timeout=30)

    ok = False
    while time.time() - started < 180:
        reinstalled = EXE.exists() and EXE.stat().st_mtime > OLD + 1
        again = switcher_pids() - {app.pid}
        if reinstalled and again:
            print(f"reinstalled and running again (pid {sorted(again)}) after {time.time() - started:.0f} s")
            ok = True
            break
        time.sleep(2)
    if log.exists():
        tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-8:]
        print("install log:\n  " + "\n  ".join(tail))
    if not ok:
        print("FAIL: Switcher was not reinstalled and started again")
    elif autostart.is_enabled():
        print("FAIL: the update turned autostart back on")
        ok = False

    for pid in switcher_pids():
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
    time.sleep(2)
    subprocess.run([str(APP_DIR / "unins000.exe"), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], timeout=120)
    print("UPDATE PASSED" if ok else "UPDATE FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
