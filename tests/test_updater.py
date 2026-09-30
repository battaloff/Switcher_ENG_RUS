import hashlib
import http.server
import io
import json
import threading
import time
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

from switcher import updater
from switcher.app import App


def release_json(version, body="- Новое: что-то", digest=None, **extra):
    asset = {"name": f"SwitcherSetup-{version}.exe", "size": 4,
             "browser_download_url": f"https://example.invalid/SwitcherSetup-{version}.exe"}
    if digest:
        asset["digest"] = digest
    item = {"tag_name": f"v{version}", "name": f"Switcher v{version}", "body": body, "draft": False,
            "prerelease": False, "published_at": "2026-09-29T10:00:00Z",
            "html_url": f"https://github.com/o/r/releases/tag/v{version}", "assets": [asset]}
    item.update(extra)
    return item


def test_versions_and_notes():
    assert updater.parse_version("v0.10.2") == (0, 10, 2)
    assert updater.parse_version("0.2") == (0, 2)
    assert updater.parse_version("latest") is None
    body = ("## Что нового\n- **Новое:** обновление из программы ([подробнее](https://x))\n"
            "* Исправлено: `Ctrl+V` в русской раскладке\n\n---\nКак установить: …")
    assert updater.short_notes(body) == ["Новое: обновление из программы (подробнее)",
                                         "Исправлено: Ctrl+V в русской раскладке"]
    many = "\n".join(f"- пункт {i}" for i in range(10))
    assert updater.short_notes(many, limit=4)[-1] == "…и ещё 7"


def test_releases_are_parsed_newest_first(monkeypatch):
    monkeypatch.setattr(updater, "current_version", lambda: (0, 2, 0))
    data = [
        release_json("0.2.0"),
        release_json("0.3.0", digest="sha256:ABCD"),
        release_json("0.1.5", body="- старое"),
        release_json("0.4.0", draft=True),
        {"tag_name": "v0.3.5", "assets": [{"name": "notes.txt", "browser_download_url": "x"}]},
    ]
    releases = updater.parse_releases(data)
    assert [r.version for r in releases] == ["0.3.0", "0.2.0", "0.1.5"]
    assert [r.relation for r in releases] == ["newer", "current", "older"]
    assert releases[0].sha256 == "abcd"
    assert releases[2].notes == ["старое"]
    assert releases[0].date == "2026-09-29"


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_github(monkeypatch, manifest=None, api=None, api_error=None):
    """urlopen stand-in: the manifest on the download servers and the REST API."""
    calls = []

    def urlopen(request, timeout=None):
        url = request.full_url
        calls.append(url)
        if url.endswith("/releases/latest/download/releases.json"):
            if manifest is None:
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO())
            return Response(json.dumps(manifest).encode())
        if api_error:
            code, headers = api_error
            raise urllib.error.HTTPError(url, code, "error", headers, io.BytesIO())
        return Response(json.dumps(api or []).encode())

    monkeypatch.setattr(updater.urllib.request, "urlopen", urlopen)
    return calls


def test_checks_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("SWITCHER_UPDATE_REPO", "")
    with pytest.raises(updater.UpdateError, match="отключена"):
        updater.fetch_releases()


def test_the_manifest_is_read_first_and_needs_no_api(monkeypatch):
    monkeypatch.setenv("SWITCHER_UPDATE_REPO", "o/r")
    calls = fake_github(monkeypatch, manifest=updater.manifest([release_json("0.3.0"), release_json("0.2.0")]),
                        api_error=(403, {"X-RateLimit-Remaining": "0"}))
    assert [r.version for r in updater.fetch_releases()] == ["0.3.0", "0.2.0"]
    assert calls == ["https://github.com/o/r/releases/latest/download/releases.json"]


def test_without_a_manifest_the_api_is_asked(monkeypatch):
    monkeypatch.setenv("SWITCHER_UPDATE_REPO", "o/r")
    calls = fake_github(monkeypatch, api=[release_json("9.9.9")])
    assert [r.version for r in updater.fetch_releases()] == ["9.9.9"]
    assert calls[-1].startswith("https://api.github.com/repos/o/r/releases")


def test_api_errors_say_what_happened(monkeypatch):
    monkeypatch.setenv("SWITCHER_UPDATE_REPO", "o/r")
    fake_github(monkeypatch, api_error=(403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1790690000"}))
    with pytest.raises(updater.UpdateError, match="ограничил число запросов с вашего адреса.*после"):
        updater.fetch_releases()
    fake_github(monkeypatch, api_error=(403, {"X-RateLimit-Remaining": "12"}))
    with pytest.raises(updater.UpdateError, match="ошибкой 403"):
        updater.fetch_releases()


def test_manifest_keeps_only_what_the_app_reads():
    data = [release_json("0.2.1", digest="sha256:ab"), release_json("0.3.0", draft=True),
            {"tag_name": "v0.1.0", "assets": [{"name": "notes.txt"}]}]
    data[0]["author"] = {"login": "someone"}
    result = updater.manifest(data)
    assert result["schema"] == 1 and [r["tag_name"] for r in result["releases"]] == ["v0.2.1"]
    assert "author" not in result["releases"][0]
    assert result["releases"][0]["assets"][0]["digest"] == "sha256:ab"
    assert updater.parse_releases(result["releases"])[0].sha256 == "ab"


@pytest.fixture
def server():
    files: dict[str, bytes] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            data = files.get(self.path)
            if data is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield files, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def make_release(base, name, data, sha=True):
    return updater.Release(version="1.2.3", key=(1, 2, 3), title="t", notes=[], date="", url="",
                           asset_url=f"{base}/{name}", asset_name=name, size=len(data),
                           sha256=hashlib.sha256(data).hexdigest() if sha else None)


def test_download_checks_what_it_got(server, tmp_path):
    files, base = server
    data = b"MZ" + b"\0" * 300_000
    files["/SwitcherSetup-1.2.3.exe"] = data
    (tmp_path / "SwitcherSetup-1.0.0.exe").write_bytes(b"old")
    seen = []
    path = updater.download(make_release(base, "SwitcherSetup-1.2.3.exe", data), folder=tmp_path,
                            progress=lambda done, total: seen.append((done, total)))
    assert path.read_bytes() == data
    assert seen[-1] == (len(data), len(data))
    assert [p.name for p in tmp_path.iterdir()] == ["SwitcherSetup-1.2.3.exe"]  # the old one is gone

    tampered = make_release(base, "SwitcherSetup-1.2.3.exe", data)
    tampered.sha256 = "0" * 64
    with pytest.raises(updater.UpdateError, match="контрольная сумма"):
        updater.download(tampered, folder=tmp_path)
    files["/SwitcherSetup-1.2.4.exe"] = b"<html>not an exe</html>"
    with pytest.raises(updater.UpdateError, match="не программа"):
        updater.download(make_release(base, "SwitcherSetup-1.2.4.exe", files["/SwitcherSetup-1.2.4.exe"], sha=False),
                         folder=tmp_path)
    with pytest.raises(updater.UpdateError, match="404"):
        updater.download(make_release(base, "SwitcherSetup-9.exe", b"MZ"), folder=tmp_path)
    assert not list(tmp_path.glob("*.part"))


def test_helper_waits_installs_and_relaunches():
    setup = Path(r"C:\Users\Д'Артаньян\AppData\Local\Switcher\cache\updates\SwitcherSetup-1.2.3.exe")
    args = updater.installer_args(autostart=False, log_file=Path(r"C:\Users\Д'Артаньян\install.log"))
    assert "/MERGETASKS=!autostart" in args and "/VERYSILENT" in args
    assert '/LOG="C:\\Users\\Д\'Артаньян\\install.log"' in args
    assert "/MERGETASKS=autostart" in updater.installer_args(autostart=True, log_file=Path("x.log"))
    script = updater.helper_script(setup, args, wait_pid=4321, relaunch=Path(r"C:\Programs\Switcher\Switcher.exe"))
    lines = script.splitlines()
    assert lines[0].startswith("Wait-Process -Id 4321")
    assert "Stop-Process -Id 4321 -Force" in lines[1]
    assert r"-FilePath 'C:\Users\Д''Артаньян\AppData" in script  # apostrophe escaped for PowerShell
    assert "-Wait -PassThru" in script
    assert lines[-1] == r"Start-Process -FilePath 'C:\Programs\Switcher\Switcher.exe'"


class Meta:
    def __init__(self):
        self.values = {}

    def get_meta(self, key, default=""):
        return self.values.get(key, default)

    def set_meta(self, key, value):
        self.values[key] = value


def test_install_update_remembers_the_target_and_quits(monkeypatch, tmp_path):
    from switcher import autostart

    calls = []
    monkeypatch.setattr(updater, "launch_installer", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(autostart, "is_enabled", lambda: True)
    fake = SimpleNamespace(profile=Meta(), stop_event=threading.Event())
    App.install_update(fake, SimpleNamespace(version="0.3.0"), tmp_path / "SwitcherSetup-0.3.0.exe")
    assert fake.profile.values["update_to"] == "0.3.0"
    assert fake.stop_event.is_set()
    (setup,), kwargs = calls[0]
    assert setup.name == "SwitcherSetup-0.3.0.exe" and kwargs["autostart"] is True and kwargs["wait_pid"]


def test_after_the_reinstall_the_result_is_reported():
    from switcher import __version__

    notes = []
    fake = SimpleNamespace(profile=Meta(), backend=SimpleNamespace(notify=notes.append))
    fake.profile.values["update_to"] = __version__
    App._report_update(fake)
    assert notes[-1] == f"Готово: работает версия {__version__}."
    assert fake.profile.values["update_to"] == ""
    fake.profile.values["update_to"] = "99.0.0"
    App._report_update(fake)
    assert "Не получилось установить версию 99.0.0" in notes[-1]


def test_cached_list_is_reused_until_it_is_old(monkeypatch):
    fetched = []
    monkeypatch.setattr(updater, "fetch_releases", lambda: fetched.append(1) or ["r"])
    got = []
    fake = SimpleNamespace(profile=Meta(), releases=None, release_listeners=[got.append])
    assert App.check_updates(fake) == ["r"] and got == [["r"]]
    App.check_updates(fake)
    assert len(fetched) == 1
    App.check_updates(fake, force=True)
    assert len(fetched) == 2
    fake.profile.values["update_checked_at"] = str(time.time() - updater.CHECK_EVERY - 1)
    App.check_updates(fake)
    assert len(fetched) == 3


def test_running_app_announces_a_new_version_once(monkeypatch):
    class Stop:
        """Lets the check loop run ``rounds`` times, as if that much time had passed."""

        def __init__(self, rounds):
            self.rounds, self.waits = rounds, []

        def wait(self, delay):
            self.waits.append(delay)
            self.rounds -= 1
            return self.rounds < 0

    newer = SimpleNamespace(version="9.0.0", relation="newer", prerelease=False, notes=["Новое: что-то"])
    checks, notes = [], []
    fake = SimpleNamespace(profile=Meta(), backend=SimpleNamespace(notify=notes.append),
                           config=SimpleNamespace(updates=SimpleNamespace(check_automatically=True)))

    def check_updates(force=False):
        checks.append(time.time())
        fake.profile.values["update_checked_at"] = str(time.time())
        return [newer]

    fake.check_updates = check_updates
    fake.stop_event = Stop(3)
    App._auto_check_updates(fake)
    assert len(checks) == 1  # due at the start; then not again within CHECK_EVERY
    assert notes == ["Вышла версия 9.0.0: Новое: что-то. Обновить: Настройки → Обновления."]
    assert fake.stop_event.waits == [20.0, updater.CHECK_TICK, updater.CHECK_TICK, updater.CHECK_TICK]
    fake.profile.values["update_checked_at"] = str(time.time() - updater.CHECK_EVERY - 1)
    fake.stop_event = Stop(1)
    App._auto_check_updates(fake)
    assert len(checks) == 2 and len(notes) == 1  # checked again hours later, but told only once


def test_changelog_documents_the_current_version():
    import importlib.util

    from switcher import __version__

    spec = importlib.util.spec_from_file_location("release_notes", Path(__file__).parent.parent / "tools" /
                                                  "release_notes.py")
    release_notes = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release_notes)
    body = release_notes.notes(__version__)
    shown = updater.short_notes(body)  # what the updates page will list for this release
    assert shown and all(line.startswith(("Новое:", "Исправлено:", "Первая версия")) for line in shown)
    assert "Установка" not in " ".join(shown)
    with pytest.raises(SystemExit):
        release_notes.section("99.99.99")
