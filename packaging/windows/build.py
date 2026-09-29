"""Build the Windows installer.

    python packaging/windows/build.py

language model → Switcher.exe (PyInstaller, no console) → self-test of the
bundle → SwitcherSetup-<version>.exe (Inno Setup, per-user, Russian UI).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
BUILD = ROOT / "build" / "windows"
DIST = ROOT / "dist"
sys.path.insert(0, str(ROOT))

from switcher import __version__  # noqa: E402


def step(message: str) -> None:
    print(f"\n=== {message}", flush=True)


def build_model() -> Path:
    from switcher.langmodel import BUNDLED_NAME, Models

    path = BUILD / BUNDLED_NAME
    Models.build().save(path)
    print(f"{path} ({path.stat().st_size / 1e6:.1f} MB)")
    return path


def build_icon() -> Path:
    from switcher.tray import draw_icon

    path = BUILD / "switcher.ico"
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    draw_icon(256).save(path, sizes=sizes)
    return path


def build_exe(model: Path, icon: Path) -> Path:
    import PyInstaller.__main__

    PyInstaller.__main__.run([
        str(HERE / "switcher_entry.py"),
        "--name", "Switcher",
        "--windowed", "--onedir", "--noconfirm", "--clean",
        "--paths", str(ROOT),
        "--icon", str(icon),
        "--add-data", f"{model}{os.pathsep}.",
        "--add-data", f"{icon}{os.pathsep}.",
        # loaded dynamically, invisible to PyInstaller's import scan
        "--hidden-import", "pynput.keyboard._win32",
        "--hidden-import", "pynput.mouse._win32",
        "--hidden-import", "pystray._win32",
        "--collect-submodules", "anthropic",
        # the model is prebuilt, so the 50 MB corpus is not needed at runtime
        "--exclude-module", "wordfreq",
        "--exclude-module", "pytest",
        "--exclude-module", "switcher.platform.linux",
        "--exclude-module", "switcher.platform.macos",
        "--distpath", str(BUILD / "dist"),
        "--workpath", str(BUILD / "work"),
        "--specpath", str(BUILD),
    ])
    return BUILD / "dist" / "Switcher"


def selftest(app_dir: Path) -> None:
    report = BUILD / "selftest.log"
    code = subprocess.run([str(app_dir / "Switcher.exe"), "--selftest", str(report)], timeout=600).returncode
    print(report.read_text(encoding="utf-8") if report.exists() else "(no report)")
    if code != 0:
        sys.exit(f"Switcher.exe --selftest failed with code {code}")


def find_iscc() -> str:
    candidates = [
        shutil.which("iscc"),
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    sys.exit("Inno Setup 6 not found (choco install innosetup)")


def build_installer(app_dir: Path, icon: Path) -> Path:
    DIST.mkdir(exist_ok=True)
    subprocess.run([
        find_iscc(),
        f"/DAppVersion={__version__}",
        f"/DSourceDir={app_dir}",
        f"/DOutputDir={DIST}",
        f"/DIconFile={icon}",
        str(HERE / "switcher.iss"),
    ], check=True)
    setup = DIST / f"SwitcherSetup-{__version__}.exe"
    print(f"{setup} ({setup.stat().st_size / 1e6:.1f} MB)")
    return setup


def main() -> None:
    if sys.platform != "win32":
        sys.exit("Build the Windows installer on Windows (see .github/workflows/windows.yml)")
    BUILD.mkdir(parents=True, exist_ok=True)
    step("language model")
    model = build_model()
    step("icon")
    icon = build_icon()
    step("Switcher.exe")
    app_dir = build_exe(model, icon)
    step("self-test of the bundle")
    selftest(app_dir)
    step("installer")
    build_installer(app_dir, icon)


if __name__ == "__main__":
    main()
