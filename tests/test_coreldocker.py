"""The buttons of a CorelDRAW docker: read from its files, changed, written back."""

import dataclasses
import json
import re
import sys

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
