"""Updating and rolling back Switcher from its own settings window (Windows).

Every version is a GitHub Release of the project with the installer
(``SwitcherSetup-<version>.exe``) attached and a short "what's new" as its
description.  Installing any of them, newer or older, is the same silent
reinstall: Switcher quits, a hidden helper waits for it to exit, runs the
installer and starts Switcher again.  Settings and everything learned live in
%APPDATA%\\Switcher and are not touched.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import __version__

log = logging.getLogger(__name__)

DEFAULT_REPO = "battaloff/Switcher_ENG_RUS"
ASSET_RE = re.compile(r"^SwitcherSetup-(\d+(?:\.\d+){1,3})\.exe$", re.IGNORECASE)
CHECK_EVERY = 3 * 3600  # automatic checks, seconds: a new release is announced the same day
CHECK_TICK = 600  # how often the running app looks whether a check is due
CHECK_RETRY = 1800  # after a failed check (offline)
USER_AGENT = f"Switcher/{__version__} (+https://github.com/{DEFAULT_REPO})"


class UpdateError(Exception):
    """Something the user should read, in Russian."""


def parse_version(text: str) -> tuple[int, ...] | None:
    match = re.fullmatch(r"v?(\d+(?:\.\d+){0,3})", (text or "").strip())
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def current_version() -> tuple[int, ...]:
    return parse_version(__version__) or (0,)


def repo() -> str:
    """owner/name of the project on GitHub; SWITCHER_UPDATE_REPO="" turns checks off (tests, self-test)."""
    return os.environ.get("SWITCHER_UPDATE_REPO", DEFAULT_REPO)


@dataclass
class Release:
    version: str
    key: tuple[int, ...]
    title: str
    notes: list[str]
    date: str  # YYYY-MM-DD
    url: str  # the release page
    asset_url: str
    asset_name: str
    size: int
    sha256: str | None = None
    prerelease: bool = False

    @property
    def relation(self) -> str:
        """"newer", "current" or "older" than the running Switcher."""
        mine = current_version()
        if self.key > mine:
            return "newer"
        return "current" if self.key == mine else "older"


def short_notes(body: str, limit: int = 6) -> list[str]:
    """The release description as a few plain lines (everything after a "---" line is left out)."""
    lines: list[str] = []
    for raw in (body or "").splitlines():
        line = raw.strip()
        if re.fullmatch(r"-{3,}|\*{3,}|_{3,}", line):
            break
        if not line or line.startswith(("#", "<!--")):
            continue
        line = re.sub(r"^(?:[-*+•]|\d+[.)])\s+", "", line)
        line = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", line)  # [text](link) → text
        line = re.sub(r"\*\*|__|`", "", line)
        if line:
            lines.append(line)
    if len(lines) > limit:
        lines = lines[:limit - 1] + [f"…и ещё {len(lines) - limit + 1}"]
    return lines


def parse_releases(data: list[dict]) -> list[Release]:
    """Releases that carry a Windows installer, newest first."""
    found: dict[tuple[int, ...], Release] = {}
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict) or item.get("draft"):
            continue
        for asset in item.get("assets") or []:
            match = ASSET_RE.match(asset.get("name") or "")
            if not match or not asset.get("browser_download_url"):
                continue
            key = parse_version(match.group(1))
            digest = asset.get("digest") or ""
            release = Release(
                version=match.group(1),
                key=key,
                title=(item.get("name") or item.get("tag_name") or match.group(1)).strip(),
                notes=short_notes(item.get("body") or ""),
                date=(item.get("published_at") or item.get("created_at") or "")[:10],
                url=item.get("html_url") or f"https://github.com/{repo()}/releases",
                asset_url=asset["browser_download_url"],
                asset_name=asset["name"],
                size=int(asset.get("size") or 0),
                sha256=digest[len("sha256:"):].lower() if digest.startswith("sha256:") else None,
                prerelease=bool(item.get("prerelease")),
            )
            found.setdefault(key, release)
            break
    return sorted(found.values(), key=lambda r: r.key, reverse=True)


def _request(url: str, accept: str, token: str = "") -> urllib.request.Request:
    headers = {"Accept": accept, "User-Agent": USER_AGENT, "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.Request(url, headers=headers)


MANIFEST = "releases.json"  # attached to every release by CI: the whole version list, API-shaped


def fetch_releases(timeout: float = 15.0) -> list[Release]:
    """All published versions, newest first.

    First from the manifest on GitHub's download servers, which has no request
    quota; the REST API allows only 60 requests an hour per address, and behind
    a VPN or a shared connection other people use them up.
    """
    name = repo()
    if not name:
        raise UpdateError("проверка обновлений отключена")
    try:
        return _fetch_manifest(name, timeout)
    except UpdateError as exc:
        log.info("no release manifest (%s), asking the API", exc)
    return _fetch_api(name, timeout)


def _fetch_manifest(name: str, timeout: float) -> list[Release]:
    url = f"https://github.com/{name}/releases/latest/download/{MANIFEST}"
    try:
        with urllib.request.urlopen(_request(url, "application/octet-stream"), timeout=timeout) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        raise UpdateError(f"HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise UpdateError(str(getattr(exc, "reason", exc))) from None
    if not isinstance(data, dict) or not isinstance(data.get("releases"), list):
        raise UpdateError("unexpected manifest")
    return parse_releases(data["releases"])


def _fetch_api(name: str, timeout: float) -> list[Release]:
    url = f"https://api.github.com/repos/{name}/releases?per_page=50"
    # CI sets a token to avoid the shared quota; downloads never carry it (redirects keep headers)
    token = os.environ.get("SWITCHER_GITHUB_TOKEN", "")
    try:
        with urllib.request.urlopen(_request(url, "application/vnd.github+json", token), timeout=timeout) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        headers = exc.headers or {}
        if exc.code == 429 or (exc.code == 403 and headers.get("X-RateLimit-Remaining") == "0"):
            reset = headers.get("X-RateLimit-Reset") or ""
            when = f"после {time.strftime('%H:%M', time.localtime(int(reset)))}" if reset.isdigit() else "позже"
            raise UpdateError("GitHub ограничил число запросов с вашего адреса (так бывает с VPN или общим "
                              f"интернетом) — попробуйте {when}") from None
        if exc.code == 404:
            raise UpdateError("список версий не найден на GitHub") from None
        raise UpdateError(f"GitHub ответил ошибкой {exc.code}") from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        raise UpdateError(f"нет связи с GitHub ({reason})") from None
    return parse_releases(data)


def manifest(releases: list[dict]) -> dict:
    """The manifest CI attaches to a release: the API's release list, trimmed to what the app reads."""
    keep = ("tag_name", "name", "body", "draft", "prerelease", "published_at", "html_url")
    items = []
    for item in releases:
        if item.get("draft"):
            continue
        entry = {key: item.get(key) for key in keep}
        entry["assets"] = [{key: asset.get(key) for key in ("name", "size", "browser_download_url", "digest")}
                           for asset in item.get("assets") or [] if ASSET_RE.match(asset.get("name") or "")]
        if entry["assets"]:
            items.append(entry)
    return {"schema": 1, "releases": items}


def downloads_dir() -> Path:
    from .paths import cache_dir

    path = cache_dir() / "updates"
    path.mkdir(parents=True, exist_ok=True)
    return path


def install_log() -> Path:
    return downloads_dir() / "install.log"


def download(release: Release, progress: Callable[[int, int], None] | None = None,
             folder: Path | None = None, timeout: float = 30.0) -> Path:
    """Fetch the installer and check it is complete and exactly what GitHub published."""
    folder = folder or downloads_dir()
    target = folder / release.asset_name
    partial = folder / (release.asset_name + ".part")
    digest = hashlib.sha256()
    done = 0
    try:
        with urllib.request.urlopen(_request(release.asset_url, "application/octet-stream"),
                                    timeout=timeout) as response, open(partial, "wb") as out:
            total = int(response.headers.get("Content-Length") or release.size or 0)
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    except urllib.error.HTTPError as exc:
        partial.unlink(missing_ok=True)
        raise UpdateError(f"не удалось скачать установщик: GitHub ответил ошибкой {exc.code}") from None
    except (urllib.error.URLError, OSError) as exc:
        partial.unlink(missing_ok=True)
        raise UpdateError(f"не удалось скачать установщик ({getattr(exc, 'reason', exc)})") from None
    try:
        if release.size and done != release.size:
            raise UpdateError("установщик скачался не полностью — попробуйте ещё раз")
        if release.sha256 and digest.hexdigest() != release.sha256:
            raise UpdateError("контрольная сумма не совпала: файл повреждён или подменён")
        with open(partial, "rb") as f:
            if f.read(2) != b"MZ":
                raise UpdateError("скачанный файл — не программа Windows")
    except UpdateError:
        partial.unlink(missing_ok=True)
        raise
    for old in folder.glob("SwitcherSetup-*.exe"):  # keep only the installer in use
        if old.name != target.name:
            old.unlink(missing_ok=True)
    os.replace(partial, target)
    return target


def can_install() -> bool:
    """Only the installed Windows app can reinstall itself."""
    return sys.platform == "win32" and bool(getattr(sys, "frozen", False))


def _ps(text: str | Path) -> str:
    return "'" + str(text).replace("'", "''") + "'"


def installer_args(autostart: bool, log_file: Path) -> list[str]:
    return [
        "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/NOCANCEL",
        # keep the user's choice from the settings window, not the one made at first install
        "/MERGETASKS=autostart" if autostart else "/MERGETASKS=!autostart",
        f'/LOG="{log_file}"',
    ]


def helper_script(setup: Path, args: list[str], wait_pid: int | None, relaunch: Path | None) -> str:
    """PowerShell that waits for Switcher to exit, installs, and starts Switcher again."""
    lines = []
    if wait_pid:
        lines += [
            f"Wait-Process -Id {int(wait_pid)} -Timeout 60 -ErrorAction SilentlyContinue",
            f"Stop-Process -Id {int(wait_pid)} -Force -ErrorAction SilentlyContinue",  # hung on exit
            "Start-Sleep -Milliseconds 500",
        ]
    # Start-Process joins the list with spaces, so arguments with spaces carry their own quotes
    arg_list = ", ".join(_ps(a) for a in args)
    lines.append(f"$p = Start-Process -FilePath {_ps(setup)} -ArgumentList {arg_list} -Wait -PassThru")
    if relaunch:
        # whatever version ended up installed, the user is not left without Switcher
        lines.append(f"Start-Process -FilePath {_ps(relaunch)}")
    return "\n".join(lines)


def launch_installer(setup: Path, autostart: bool, wait_pid: int | None = None,
                     relaunch: Path | None = None) -> subprocess.Popen:
    """Start the detached helper; the caller should quit right after."""
    script = helper_script(Path(setup), installer_args(autostart, install_log()), wait_pid, relaunch)
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" \
        / "powershell.exe"
    flags = 0x08000000 | 0x00000200  # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    log.info("installing %s (then relaunching %s)", setup, relaunch)
    return subprocess.Popen(
        [str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-WindowStyle", "Hidden", "-EncodedCommand", encoded],
        creationflags=flags, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        close_fds=True,
    )
