import pytest

from switcher.cli import main


@pytest.fixture
def home(tmp_path, monkeypatch, models):
    monkeypatch.setenv("SWITCHER_HOME", str(tmp_path))
    return tmp_path


def test_convert(home, capsys):
    main(["convert", "Ghbdtn?", "rfr", "ltkf"])
    assert capsys.readouterr().out.strip() == "Привет, как дела"


def test_explain(home, capsys):
    main(["explain", "ghbdtn"])
    out = capsys.readouterr().out
    assert "привет" in out and "переключить" in out


def test_rules_roundtrip(home, capsys):
    assert main(["rules", "add", "ok", "--lang", "en"]) == 0
    assert main(["rules", "typo", "превет", "привет"]) == 0
    main(["rules", "list"])
    out = capsys.readouterr().out
    assert "'ok'" in out and "всегда EN" in out and "превет" in out
    main(["explain", "щл"])
    assert "причина: rule" in capsys.readouterr().out
    assert main(["rules", "remove", "ok"]) == 0
    main(["stats", "-v"])
    out = capsys.readouterr().out
    assert "Правил: 1" in out


def test_forget_needs_confirmation(home, capsys):
    assert main(["forget"]) == 1
    assert main(["forget", "--yes"]) == 0
