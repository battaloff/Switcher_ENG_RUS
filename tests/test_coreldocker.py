"""The buttons of a CorelDRAW docker: read from its files, changed, written back."""

import dataclasses
import json
import re
import sys
from pathlib import Path

import pytest
from conftest import make_docker

from switcher import coreldocker
from switcher.config import Config, load_config
from switcher.coreldocker import (
    Docker,
    DockerError,
    Preset,
    auto_subtitle,
    auto_title,
    find_config,
    find_dockers,
    groups_of,
    macro_numbers,
    move,
    move_group,
    problem,
)


@pytest.fixture
def docker(docker_folder, tmp_path):
    return Docker(docker_folder, backups=tmp_path / "copies")


def page_presets(docker) -> list[dict]:
    return find_config(docker.page.read_text(encoding="utf-8-sig"))[0]["presets"]


def project_presets(docker) -> list[dict]:
    return json.loads(docker.manifest.read_text(encoding="utf-8"))["Presets"]


def test_the_docker_is_found_in_corels_addons(tmp_path, docker_folder):
    assert find_dockers([str(tmp_path / "Corel")]) == [docker_folder]
    other = docker_folder.parent / "PageSetupAddon"  # an add-on that Docker Studio did not make
    other.mkdir()
    (other / "PageSetupAddon.gms").write_bytes(b"x")
    (docker_folder.parent / "Broken").mkdir()
    (docker_folder.parent / "Broken" / "dockerstudio.json").write_text("{}")  # a project without its page
    assert find_dockers([str(tmp_path / "Corel"), str(tmp_path / "nothing here")]) == [docker_folder]
    assert find_dockers([]) == []
    with pytest.raises(DockerError):
        Docker(other)
    with pytest.raises(DockerError):
        Docker(tmp_path / "no such folder")


def test_buttons_are_read_in_the_order_the_docker_draws_them(docker):
    presets = docker.load()
    assert docker.name == "POLITEXT"
    assert [p.title for p in presets] == ["450×370", "510×+32", "510×+36", "525×+94,5"]  # «510» together
    assert groups_of(presets) == ["450 – 490", "510", "525"]
    first, second = presets[0], presets[1]
    assert first == Preset("450×370", "450 – 490", "absolute", "#6B7280", 450, 370, True, 0, "a1")
    assert (second.width, second.height, second.absolute, second.guideline) == (510, 32, False, 26)
    assert first.columns() == ("450 мм", "370 мм ровно", "—") and second.columns() == ("510 мм", "+32 мм", "26 мм")
    assert presets[3].columns() == ("525 мм", "+94,5 мм", "57 мм")
    assert second.what() == "ширина 510 · высота +32 · направляющая 26 мм"


def test_saving_without_changes_keeps_the_docker_as_it_is(docker):
    before_page = docker.page.read_text(encoding="utf-8")
    before = {p["id"]: p for p in page_presets(docker)}
    before_project = json.loads(docker.manifest.read_text(encoding="utf-8"))
    docker.save(docker.load())
    after_page = docker.page.read_text(encoding="utf-8")
    config, begin, end = find_config(after_page)
    assert [p["id"] for p in config["presets"]] == ["a1", "b2", "d4", "c3"]
    assert all(before[p["id"]] == p for p in config["presets"])  # every key and value, the macros' names too
    assert {k: v for k, v in config.items() if k != "presets"} == {
        "name": "POLITEXT", "macroProjectName": "Politext", "generateVbaMacros": True, "minimumCorelVersion": 25,
        "columns": 4}
    # outside the list of buttons only one line is added: the guideline's colour (the macros make it blue)
    old_begin, old_end = find_config(before_page)[1:]
    assert after_page[:begin] == before_page[:old_begin]
    assert after_page[end:].replace("          " + coreldocker._BLUE + "\n", "") == before_page[old_end:]
    assert after_page.count("RGBAssign(0, 0, 255)") == 1
    project = json.loads(docker.manifest.read_text(encoding="utf-8"))
    assert {k: v for k, v in project.items() if k != "Presets"} == {
        k: v for k, v in before_project.items() if k != "Presets"}
    old = {p["Id"]: p for p in before_project["Presets"]}
    assert [(p["Id"], p["Order"]) for p in project["Presets"]] == [("a1", 0), ("b2", 1), ("d4", 2), ("c3", 3)]
    assert all({**old[p["Id"]], "Order": p["Order"]} == p for p in project["Presets"])
    raw = docker.manifest.read_bytes()
    assert raw.count(b"\r\n") == raw.count(b"\n") > 10 and all(byte < 128 for byte in raw)  # as Docker Studio writes
    docker.save(docker.load())
    assert docker.page.read_text(encoding="utf-8") == after_page  # the colour line is not added twice


def test_a_button_with_new_numbers_runs_without_its_macro(docker):
    """A macro has its numbers compiled in: the page's own code does the work for a changed button."""
    presets = docker.load()
    presets[1] = dataclasses.replace(presets[1], height=34, title=auto_title(510, 34, False))  # 510×+32 → +34
    presets[0] = dataclasses.replace(presets[0], title="A2", color="#FF8040", subtitle="")  # the look only
    docker.save(presets)
    by_id = {p["id"]: p for p in page_presets(docker)}
    assert by_id["b2"]["generatedMacroName"] == "" and (by_id["b2"]["height"], by_id["b2"]["title"]) == (34, "510×+34")
    assert by_id["a1"]["generatedMacroName"] == "Macros.Preset001" and by_id["a1"]["color"] == "#FF8040"
    assert by_id["c3"]["generatedMacroName"] == "Macros.Preset003" and by_id["c3"]["height"] == 94.5
    assert {p["Id"]: p["Height"] for p in project_presets(docker)}["b2"] == 34
    assert {p["Id"]: (p["Title"], p["Subtitle"]) for p in project_presets(docker)}["a1"] == ("A2", "")


def test_without_the_macros_text_untouched_buttons_keep_their_macros(tmp_path):
    docker = Docker(make_docker(tmp_path, macros=False), backups=tmp_path / "copies")
    presets = docker.load()
    presets[2] = dataclasses.replace(presets[2], guideline=30)
    docker.save(presets)
    assert {p["id"]: p["generatedMacroName"] for p in page_presets(docker)} == {
        "a1": "Macros.Preset001", "b2": "Macros.Preset002", "d4": "", "c3": "Macros.Preset003"}


def test_a_new_button_goes_into_the_page_and_the_project(docker):
    presets = docker.load()
    presets.append(Preset(auto_title(900, 70, False), "900", auto_subtitle(False, 45), "#0EA5E9", 900, 70,
                          guideline=45))
    presets.insert(1, Preset("745×605", "450 – 490", "absolute", "#6B7280", 745, 605, True, id="mine"))
    docker.save(presets)
    loaded = docker.load()
    assert [p.title for p in loaded] == ["450×370", "745×605", "510×+32", "510×+36", "525×+94,5", "900×+70"]
    new = page_presets(docker)[-1]
    assert re.fullmatch(r"[0-9a-f-]{36}", new["id"])
    assert new == {"id": new["id"], "group": "900", "title": "900×+70", "subtitle": "→45mm", "color": "#0EA5E9",
                   "actionType": "ProductionPageSetup", "width": 900, "height": 70, "unit": 3,
                   "heightMode": "AddToCurrent", "guidelineDistanceMm": 45, "macroProject": "", "macroName": "",
                   "macroFileName": "", "generatedMacroName": "", "minimumCorelVersion": 25}
    assert project_presets(docker)[-1] == {
        "Id": new["id"], "Order": 5, "Group": "900", "Title": "900×+70", "Subtitle": "→45mm", "Color": "#0EA5E9",
        "ActionType": "ProductionPageSetup", "Width": 900, "Height": 70, "Unit": "Millimeter",
        "HeightMode": "AddToCurrent", "GuidelineDistanceMm": 45, "MacroProject": "", "MacroName": "",
        "MacroFileName": "", "MinimumCorelVersion": 25}
    mine = page_presets(docker)[1]
    assert (mine["id"], mine["heightMode"], mine["generatedMacroName"]) == ("mine", "Absolute", "")
    # a button taken away is gone from both files
    docker.save([p for p in loaded if p.id != "b2"])
    assert "b2" not in [p["id"] for p in page_presets(docker)] + [p["Id"] for p in project_presets(docker)]


def test_any_text_is_safe_in_the_page(docker):
    presets = docker.load()
    nasty = 'A</script><script>alert("x")</script> \u2028 «кавычки» \\ конец'
    presets[0] = dataclasses.replace(presets[0], title=nasty, group="<b>жирная</b>")
    docker.save(presets)
    html = docker.page.read_text(encoding="utf-8")
    config, begin, end = find_config(html)
    assert config["presets"][0]["title"] == nasty and config["presets"][0]["group"] == "<b>жирная</b>"
    assert "<" not in html[begin:end] and "\u2028" not in html[begin:end]
    assert html.count("</script>") == 1 and docker.load()[0].title == nasty
    assert project_presets(docker)[0]["Title"] == nasty


def test_the_last_changes_can_be_undone(docker):
    original = docker.page.read_bytes(), docker.manifest.read_bytes()
    assert not docker.can_undo() and not docker.undo()
    presets = docker.load()
    docker.save(presets[:3])
    middle = docker.page.read_bytes(), docker.manifest.read_bytes()
    docker.save(presets[:1])
    assert len(docker.load()) == 1 and docker.can_undo()
    assert docker.undo() and (docker.page.read_bytes(), docker.manifest.read_bytes()) == middle
    assert docker.undo() and (docker.page.read_bytes(), docker.manifest.read_bytes()) == original
    assert not docker.can_undo() and not docker.undo()
    # the files as they were before Switcher touched them stay for good
    kept = docker.backups / "original"
    assert ((kept / "Politext.html").read_bytes(), (kept / "dockerstudio.json").read_bytes()) == original
    for count in range(coreldocker.KEEP_UNDO + 5):  # old steps are dropped
        docker.save(presets[:1 + count % 3])
    assert len(docker._kept()) == coreldocker.KEEP_UNDO
    assert ((kept / "Politext.html").read_bytes(), (kept / "dockerstudio.json").read_bytes()) == original


def test_the_page_keeps_its_bom_and_line_ends(tmp_path):
    docker = Docker(make_docker(tmp_path, bom=True, newline="\r\n"), backups=tmp_path / "copies")
    presets = docker.load()
    docker.save(presets + [Preset("650×+80", "650", "→40mm", width=650, height=80, guideline=40)])
    raw = docker.page.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf") and raw.count(b"\r\n") == raw.count(b"\n") > 10
    assert coreldocker._BLUE.encode() + b"\r\n" in raw and len(docker.load()) == 5


def test_a_button_of_another_kind_keeps_its_action(docker):
    config, begin, end = find_config(docker.page.read_text(encoding="utf-8"))
    config["presets"].append({"id": "m5", "group": "Макросы", "title": "Кресты", "subtitle": "", "color": "#111111",
                              "actionType": "RunMacro", "width": 0, "height": 0, "unit": 3,
                              "heightMode": "Absolute", "guidelineDistanceMm": 0, "macroProject": "KREST",
                              "macroName": "Module1.Kresty", "macroFileName": "KREST.gms",
                              "generatedMacroName": "", "minimumCorelVersion": 25, "futureKey": [1, 2]})
    html = docker.page.read_text(encoding="utf-8")
    docker.page.write_text(html[:begin] + coreldocker.script_json(config) + html[end:], encoding="utf-8")
    presets = docker.load()
    macro = presets[-1]
    assert not macro.sets_page and macro.columns() == ("—", "—", "другое действие") and problem(macro) is None
    presets[-1] = dataclasses.replace(macro, title="Кресты 5 мм", color="#222222")
    docker.save(presets)
    saved = page_presets(docker)[-1]
    assert saved == {**config["presets"][-1], "title": "Кресты 5 мм", "color": "#222222"}
    assert project_presets(docker)[-1]["Title"] == "Кресты 5 мм"


def test_buttons_without_an_id_or_with_the_same_one_are_told_apart(docker):
    config, begin, end = find_config(docker.page.read_text(encoding="utf-8"))
    config["presets"][1]["id"] = "a1"  # the same as the first
    del config["presets"][2]["id"]
    html = docker.page.read_text(encoding="utf-8")
    docker.page.write_text(html[:begin] + coreldocker.script_json(config) + html[end:], encoding="utf-8")
    presets = docker.load()
    assert len({p.id for p in presets}) == 4 and presets[0].id == "a1"
    docker.save(presets)
    assert [p.title for p in docker.load()] == ["450×370", "510×+32", "510×+36", "525×+94,5"]
    assert len({p["id"] for p in page_presets(docker)}) == 4 == len({p["Id"] for p in project_presets(docker)})


def test_buttons_move_inside_their_group_and_groups_move_whole(docker):
    presets = docker.load()  # 450×370 | 510×+32, 510×+36 | 525×+94,5
    moved, at = move(presets, 1, 1)
    assert [p.id for p in moved] == ["a1", "d4", "b2", "c3"] and at == 2
    assert move(presets, 2, 1) == (presets, 2)  # the last of its group: it does not jump into the next one
    assert move(presets, 0, -1) == (presets, 0) and move(presets, 9, 1) == (presets, 9)
    moved, at = move_group(presets, 3, -1)
    assert [p.id for p in moved] == ["a1", "c3", "b2", "d4"] and at == 1
    moved, at = move_group(presets, 2, -1)
    assert [p.id for p in moved] == ["b2", "d4", "a1", "c3"] and at == 1
    assert move_group(presets, 3, 1) == (presets, 3) and move_group(presets, 0, -1) == (presets, 0)


def test_captions_follow_the_numbers():
    assert auto_title(450, 370, True) == "450×370" and auto_title(510, 32, False) == "510×+32"
    assert auto_title(745, 37.5, False) == "745×+37,5"
    assert auto_subtitle(False, 26) == "→26mm" and auto_subtitle(True, 0) == "absolute"
    assert auto_subtitle(True, 40) == "→40mm" and auto_subtitle(False, 0) == ""


def test_a_button_that_cannot_work_is_refused():
    good = Preset("510×+32", "510", width=510, height=32, guideline=26)
    assert problem(good) is None and problem(dataclasses.replace(good, height=0, guideline=0)) is None
    assert "надпись" in problem(dataclasses.replace(good, title=" "))
    assert "Ширина" in problem(dataclasses.replace(good, width=0))
    assert "Ширина" in problem(dataclasses.replace(good, width=99999))
    assert "Высота" in problem(dataclasses.replace(good, absolute=True, height=0))
    assert "Прибавка" in problem(dataclasses.replace(good, height=-5))
    assert "Направляющая" in problem(dataclasses.replace(good, guideline=-1))


def test_what_the_macros_do_is_read_from_their_text():
    from conftest import DOCKER_MACROS

    assert macro_numbers(DOCKER_MACROS) == {
        "Macros.Preset001": (450, 370, 0, True), "Macros.Preset002": (510, 32, 26, False),
        "Macros.Preset003": (525, 94.5, 57, False), "Macros.Preset004": (510, 36, 28, False)}
    assert macro_numbers("") == {} and macro_numbers("Public Sub X()\n    MsgBox 1\nEnd Sub") == {}


def test_a_page_that_is_not_a_docker_is_told_plainly(tmp_path):
    folder = tmp_path / "Addons" / "Other"
    folder.mkdir(parents=True)
    (folder / "Other.html").write_text("<html><script>var config = {broken;</script></html>", encoding="utf-8")
    with pytest.raises(DockerError, match="не читается"):
        Docker(folder, backups=tmp_path / "copies").load()
    (folder / "Other.html").write_text('<html><script>var config = {"name":"x"};</script></html>', encoding="utf-8")
    with pytest.raises(DockerError, match="нет кнопок"):
        Docker(folder, backups=tmp_path / "copies").load()
    with pytest.raises(DockerError, match="нет списка кнопок"):
        find_config("<html></html>")


def test_a_closed_folder_is_noticed_and_told(docker, monkeypatch):
    assert docker.writable()
    presets = docker.load()

    def refuse(path, data):
        raise PermissionError(13, "Access is denied", str(path))

    monkeypatch.setattr(Docker, "_write", staticmethod(refuse))
    with pytest.raises(DockerError, match="Windows не разрешает"):
        docker.save(presets[:2])
    assert len(docker.load()) == 4  # nothing was changed


def test_the_settings_remember_the_docker(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"enabled": True}), encoding="utf-8")  # a config from before the docker page
    config = load_config(path)
    assert config.corel_docker.folder == "" == Config().corel_docker.folder
    config.corel_docker.folder = r"C:\Program Files\Corel\CorelDRAW Graphics Suite\26\Programs64\Addons\Politext"
    config.save(path)
    assert load_config(path).corel_docker.folder.endswith(r"Addons\Politext")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' rights to folders")
def test_windows_gives_the_right_to_change_a_folder(tmp_path):
    import ctypes
    import subprocess
    import threading

    from switcher.platform import win_access

    assert re.fullmatch(r"S-1-5-[\d-]+", win_access.user_sid())
    if not ctypes.windll.shell32.IsUserAnAdmin():
        pytest.skip("Windows would ask the person at the keyboard (UAC)")
    folder = tmp_path / "Докер с пробелом"
    folder.mkdir()
    result: list = []
    worker = threading.Thread(target=lambda: result.append(win_access.grant_modify(str(folder), timeout=30)),
                              daemon=True)
    worker.start()
    worker.join(60)  # a question nobody answers must not hang the tests
    assert result == [None], "icacls as administrator did not finish"
    listed = subprocess.run(["icacls", str(folder)], capture_output=True, check=True).stdout.decode(
        "ascii", "replace")
    assert "(OI)(CI)(M)" in listed, listed
    with pytest.raises(FileNotFoundError):
        win_access.grant_modify(str(tmp_path / "нет такой"))


# -- carrying the docker to another computer -------------------------------------------------------

def test_the_docker_is_saved_into_a_file_without_its_macros(docker_folder, tmp_path):
    import zipfile

    (docker_folder / "dockerstudio.json.before-audit-migration-20260724.bak").write_text("old")
    (docker_folder / ".switcher-12-Politext.html.tmp").write_text("half")
    (docker_folder / "img").mkdir()
    (docker_folder / "img" / "logo.png").write_bytes(b"\x89PNG")
    target = tmp_path / "POLITEXT — докер.zip"
    assert coreldocker.export_docker(docker_folder, target) == 6
    names = sorted(zipfile.ZipFile(target).namelist())
    assert names == ["Politext/AppUI.xslt", "Politext/Coreldrw.addon", "Politext/Politext.html",
                     "Politext/UserUI.xslt", "Politext/dockerstudio.json", "Politext/img/logo.png"]
    files = coreldocker.read_package(target)
    assert sorted(files) == [name.split("/", 1)[1] for name in names]
    assert files["Politext.html"] == (docker_folder / "Politext.html").read_bytes()
    assert coreldocker.read_package(docker_folder) == files  # a docker's folder will do as well
    with pytest.raises(DockerError):
        coreldocker.export_docker(tmp_path / "nothing", tmp_path / "x.zip")


def test_a_bad_file_is_refused(tmp_path, docker_folder):
    import zipfile

    def package(entries: dict) -> Path:
        path = tmp_path / f"p{len(list(tmp_path.glob('p*.zip')))}.zip"
        with zipfile.ZipFile(path, "w") as archive:
            for name, data in entries.items():
                archive.writestr(name, data)
        return path

    page = (docker_folder / "Politext.html").read_bytes()
    assert sorted(coreldocker.read_package(package({"Politext.html": page, "AppUI.xslt": b"x"}))) == [
        "AppUI.xslt", "Politext.html"]  # files at the top of the archive
    for name in ("../evil.html", "/etc/evil.html", "Politext/../../evil.html", "C:/evil.html", "a/b:c.html"):
        with pytest.raises(DockerError, match="недопустимым"):
            coreldocker.read_package(package({name: page}))
    with pytest.raises(DockerError, match="нет докера"):
        coreldocker.read_package(package({"readme.txt": b"hello"}))
    (tmp_path / "not a zip.zip").write_text("hello")
    with pytest.raises(DockerError, match="не файл докера"):
        coreldocker.read_package(tmp_path / "not a zip.zip")


def install_targets(root, *versions):
    for version in versions:
        programs = root / "Corel" / "CorelDRAW Graphics Suite" / version / "Programs64"
        (programs / "Addons").mkdir(parents=True)
        (programs / "CorelDRW.exe").write_bytes(b"MZ")
    return coreldocker.corel_targets([str(root / "Corel")])


def test_corels_are_found_with_their_addons(tmp_path):
    for suite in ("CorelDRAW Graphics Suite/27/Programs64", "CorelDRAW Graphics Suite 2025/Programs64",
                  "CorelDRAW Graphics Suite 2020 (64-Bit)/Programs64", "Corel PHOTO-PAINT/Programs64"):
        folder = tmp_path / "Corel" / suite
        folder.mkdir(parents=True)
        (folder / ("CorelDRW.exe" if "PHOTO" not in suite else "CorelPP.exe")).write_bytes(b"MZ")
    targets = coreldocker.corel_targets([str(tmp_path / "Corel"), str(tmp_path / "no such")])
    assert sorted(t.label for t in targets) == ["CorelDRAW 2020 (64-Bit)", "CorelDRAW 2025", "CorelDRAW 27"]
    assert all(t.addons.name == "Addons" and t.addons.parent.name == "Programs64" for t in targets)


def test_the_docker_is_installed_as_one_of_its_own(tmp_path, docker_folder):
    """On the other computer there may be a POLITEXT already — another one, with the same GUIDs and
    the same macro project: the installed docker must not clash with it."""
    from conftest import DOCKER_GUIDS, make_docker

    other_pc = tmp_path / "other"
    other = make_docker(other_pc, version="27")  # the other computer's own POLITEXT
    other_files = {p.name: p.read_bytes() for p in other.iterdir()}
    targets = coreldocker.corel_targets([str(other_pc / "Corel")])
    assert [t.label for t in targets] == ["CorelDRAW 27"]
    files = coreldocker.read_package(docker_folder)
    caption = coreldocker.suggest_caption("POLITEXT", targets)
    assert caption == "POLITEXT 2"
    with pytest.raises(DockerError, match="уже есть докер «POLITEXT»"):
        coreldocker.install_docker(files, "politext", targets)
    (folder,) = coreldocker.install_docker(files, caption, targets)
    assert folder == targets[0].addons / "POLITEXT_2_Switcher"
    assert {p.name: p.read_bytes() for p in other.iterdir()} == other_files  # the other docker untouched
    assert sorted(p.name for p in folder.iterdir()) == [
        "AppUI.xslt", "Coreldrw.addon", "Politext.html", "UserUI.xslt", "dockerstudio.json",
        "switcher-install.json"]  # no macros
    app_ui = (folder / "AppUI.xslt").read_text(encoding="utf-8")
    guids = coreldocker.own_guids(app_ui)
    assert not set(guids.values()) & set(DOCKER_GUIDS.values()) and len(set(guids.values())) == 3
    assert not any(old in (folder / name).read_text(encoding="utf-8") for old in DOCKER_GUIDS.values()
                   for name in ("AppUI.xslt", "UserUI.xslt", "dockerstudio.json", "Politext.html"))
    assert app_ui.count('userCaption="POLITEXT 2"') == 2 and "[VGAppAddonsDir]/POLITEXT_2_Switcher/Politext.html" in app_ui
    assert "2cc24a3e-fe24-4708-9a74-9c75406eebcd" in app_ui  # CorelDRAW's own GUIDs stay
    user_ui = (folder / "UserUI.xslt").read_text(encoding="utf-8")
    assert user_ui.count(guids["command"]) == 2 and "3eaa9bbe-28fd-4672-9128-02974ee96332" in user_ui
    assert (folder / "AppUI.xslt").read_bytes().count(b"\r\n") > 10  # line ends as they were
    page = (folder / "Politext.html").read_text(encoding="utf-8")
    config = find_config(page)[0]
    assert (config["name"], config["macroProjectName"], config["generateVbaMacros"]) == ("POLITEXT 2", "", False)
    assert [p["generatedMacroName"] for p in config["presets"]] == ["", "", "", ""]  # the page does the work
    assert "<title>POLITEXT 2</title>" in page and re.search(r'class="panel-title">\s*POLITEXT 2\s*<', page)
    assert coreldocker._BLUE in page
    project = json.loads((folder / "dockerstudio.json").read_text(encoding="utf-8"))
    assert (project["Name"], project["FolderName"], project["GenerateVbaMacros"], project["MacroProjectName"]) == (
        "POLITEXT 2", "POLITEXT_2_Switcher", False, "")
    assert (project["CommandGuid"], project["BrowserGuid"], project["DockerGuid"]) == (
        guids["command"], guids["browser"], guids["docker"])
    # Switcher finds both dockers and edits the installed one like any other
    assert sorted(f.name for f in find_dockers([str(other_pc / "Corel")])) == ["POLITEXT_2_Switcher", "Politext"]
    installed = Docker(folder, backups=tmp_path / "copies")
    assert len(installed.load()) == 4 and installed.name == "POLITEXT 2"
    # installed again (an update): the same folder and GUIDs, no second copy
    assert coreldocker.install_docker(files, "POLITEXT 2", targets) == [folder]
    assert coreldocker.own_guids((folder / "AppUI.xslt").read_text(encoding="utf-8")) == guids
    assert sorted(p.name for p in targets[0].addons.iterdir()) == ["POLITEXT_2_Switcher", "Politext"]
    assert coreldocker.suggest_caption("POLITEXT 2", targets) == "POLITEXT 2"  # its own name is not «taken»
    # another name: a docker of its own again
    (second,) = coreldocker.install_docker(files, "Плиты офсет", targets)
    assert second.name == "Plity_ofset_Switcher" and coreldocker.own_guids(
        (second / "AppUI.xslt").read_text(encoding="utf-8")) != guids


def test_it_goes_into_every_corel_chosen_and_asks_windows_once(tmp_path, docker_folder, monkeypatch):
    from conftest import make_docker

    other_pc = tmp_path / "other"
    make_docker(other_pc, version="27")
    make_docker(other_pc, version="26", name="Something", caption="Другой докер")
    targets = coreldocker.corel_targets([str(other_pc / "Corel")])
    assert [t.label for t in targets] == ["CorelDRAW 26", "CorelDRAW 27"]
    files = coreldocker.read_package(docker_folder)
    closed = {t.addons for t in targets}  # Program Files: closed until Windows allows it
    real_mkdir = Path.mkdir

    def mkdir(self, *args, **kwargs):
        if self.parent in closed:
            raise PermissionError(13, "Access is denied", str(self))
        return real_mkdir(self, *args, **kwargs)

    asked = []

    def make_writable(folders):
        asked.append(sorted(f.parent.parent.parent.name for f in folders))
        closed.clear()

    monkeypatch.setattr(Path, "mkdir", mkdir)
    folders = coreldocker.install_docker(files, "POLITEXT 2", targets, make_writable)
    assert asked == [["26", "27"]] and all((f / "Politext.html").exists() for f in folders)
    closed.update(t.addons for t in targets)
    with pytest.raises(DockerError, match="не разрешила"):
        coreldocker.install_docker(files, "POLITEXT 3", targets)  # nobody to ask
    with pytest.raises(DockerError, match="Напишите"):
        coreldocker.install_docker(files, " ", targets)
    with pytest.raises(DockerError, match="не найден CorelDRAW"):
        coreldocker.install_docker(files, "X", [])


def test_folder_names_are_latin():
    assert coreldocker.folder_for("POLITEXT 2") == "POLITEXT_2_Switcher"
    assert coreldocker.folder_for("Плиты офсет") == "Plity_ofset_Switcher"
    assert coreldocker.folder_for("ЁЖ & Co!") == "EZH_Co_Switcher"
    assert coreldocker.folder_for("«»") == "Docker_Switcher"


def test_files_without_registration_are_not_installed(tmp_path, docker_folder):
    targets = install_targets(tmp_path / "pc", "27")
    files = coreldocker.read_package(docker_folder)
    del files["UserUI.xslt"]
    with pytest.raises(DockerError, match="AppUI.xslt и UserUI.xslt"):
        coreldocker.install_docker(files, "POLITEXT", targets)
    files = coreldocker.read_package(docker_folder)
    files["AppUI.xslt"] = b"<xsl:stylesheet/>"
    with pytest.raises(DockerError, match="Не понял файл AppUI.xslt"):
        coreldocker.install_docker(files, "POLITEXT", targets)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' rights to folders")
def test_windows_creates_folders_for_the_docker_with_one_question(tmp_path):
    import ctypes
    import subprocess
    import threading

    from switcher.platform import win_access

    if not ctypes.windll.shell32.IsUserAnAdmin():
        pytest.skip("Windows would ask the person at the keyboard (UAC)")
    folders = [tmp_path / "Addons (64-Bit)" / "POLITEXT_2_Switcher", tmp_path / "Другая" / "Папка"]
    (tmp_path / "Другая").mkdir()
    result: list = []
    worker = threading.Thread(target=lambda: result.append(win_access.make_writable([str(f) for f in folders],
                                                                                      timeout=30)), daemon=True)
    worker.start()
    worker.join(60)
    assert result == [None], "cmd as administrator did not finish"
    for folder in folders:
        assert folder.is_dir()
        listed = subprocess.run(["icacls", str(folder)], capture_output=True, check=True).stdout.decode(
            "ascii", "replace")
        assert "(OI)(CI)(M)" in listed, listed


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' «downloaded from another computer» mark")
def test_the_mark_of_another_computer_is_taken_off(tmp_path, docker_folder):
    """With Windows' «downloaded from the internet» mark CorelDRAW shows a docker empty (colorscript.ru)."""
    package = tmp_path / "POLITEXT — докер.zip"
    coreldocker.export_docker(docker_folder, package)
    with open(f"{package}:Zone.Identifier", "w", encoding="ascii") as mark:  # as a browser or a messenger leaves it
        mark.write("[ZoneTransfer]\nZoneId=3\n")
    other_pc = tmp_path / "other"
    targets = install_targets(other_pc, "27")
    (folder,) = coreldocker.install_docker(coreldocker.read_package(package), "POLITEXT", targets)
    for path in folder.iterdir():
        with pytest.raises(OSError):
            open(f"{path}:Zone.Identifier", encoding="ascii").close()
    page = folder / "Politext.html"
    with open(f"{page}:Zone.Identifier", "w", encoding="ascii") as mark:  # copied by hand before Switcher
        mark.write("[ZoneTransfer]\nZoneId=3\n")
    coreldocker.install_docker(coreldocker.read_package(package), "POLITEXT", targets)  # installed again
    with pytest.raises(OSError):
        open(f"{page}:Zone.Identifier", encoding="ascii").close()


# -- how big the buttons are -----------------------------------------------------------------------

def test_the_size_of_the_buttons_is_read_from_the_page(docker):
    from switcher.coreldocker import Look, look_of

    assert docker.look() == Look(4, 42, 10)  # the page's own style
    assert look_of("<html><head></head></html>") == Look()  # no style at all
    assert look_of("<style>.preset{width:33.333%;min-height:50px;font-size:12px}</style>") == Look(3, 50, 12)
    assert Look(3, 56, 13).css() == (".preset{width:33.3333%;min-height:56px;font-size:13px}"
                                     ".preset .subtitle{font-size:10px}")
    assert Look(4, 42, 7).subtitle_font == 7


def test_the_size_of_the_buttons_is_written_into_the_docker(docker):
    from switcher.coreldocker import LOOK_ID, Look

    before = docker.page.read_text(encoding="utf-8")
    buttons = page_presets(docker)
    docker.save_look(Look(3, 56, 13))
    page = docker.page.read_text(encoding="utf-8")
    assert page.count(f'<style id="{LOOK_ID}">') == 1 and docker.look() == Look(3, 56, 13)
    assert page.index(LOOK_ID) > page.index(".subtitle {") and page.index(LOOK_ID) < page.index("</head>")
    assert find_config(page)[0]["columns"] == 3 and page_presets(docker) == buttons  # the buttons as they were
    assert json.loads(docker.manifest.read_text(encoding="utf-8"))["Columns"] == 3
    docker.save_look(Look(6, 30, 8))  # replaced, not added
    page = docker.page.read_text(encoding="utf-8")
    assert page.count(LOOK_ID) == 1 and docker.look() == Look(6, 30, 8)
    docker.save(docker.load())  # changing the buttons keeps the size
    assert docker.look() == Look(6, 30, 8)
    for bad in (Look(0, 42, 10), Look(9, 42, 10), Look(4, 10, 10), Look(4, 42, 30)):
        with pytest.raises(DockerError):
            docker.save_look(bad)
    assert docker.undo() and docker.undo() and docker.look() == Look(3, 56, 13)
    assert docker.undo() and docker.page.read_text(encoding="utf-8") == before


def test_an_installed_docker_keeps_the_size_of_its_buttons(tmp_path, docker):
    from switcher.coreldocker import Look

    docker.save_look(Look(3, 56, 13))
    targets = install_targets(tmp_path / "other", "27")
    (folder,) = coreldocker.install_docker(coreldocker.read_package(docker.folder), "POLITEXT", targets)
    assert Docker(folder, backups=tmp_path / "copies").look() == Look(3, 56, 13)
