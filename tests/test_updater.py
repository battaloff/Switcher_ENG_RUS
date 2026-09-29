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


def test_checks_can_be_turned_off_and_errors_are_readable(monkeypatch):
    monkeypatch.setenv("SWITCHER_UPDATE_REPO", "")
    with pytest.raises(updater.UpdateError, match="отключена"):
        updater.fetch_releases()
    monkeypatch.setenv("SWITCHER_UPDATE_REPO", "o/r")

    def limited(*a, **k):
        raise urllib.error.HTTPError("u", 403, "rate limited", {}, io.BytesIO())

    monkeypatch.setattr(updater.urllib.request, "urlopen", limited)
    with pytest.raises(updater.UpdateError, match="ограничил"):
        updater.fetch_releases()

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(updater.urllib.request, "urlopen",
                        lambda *a, **k: Response(json.dumps([release_json("9.9.9")]).encode()))
    assert [r.version for r in updater.fetch_releases()] == ["9.9.9"]


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
