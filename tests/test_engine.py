import pytest

from switcher.engine import Context, explain
from switcher.layouts import EN, RU


def decide(engine, keyboard, text, typed_lang, **ctx):
    """``text`` is what appeared on screen in ``typed_lang``."""
    strokes = keyboard.strokes(text, typed_lang)
    return engine.decide(strokes, typed_lang, Context(**ctx))


@pytest.mark.parametrize("typed, lang, expected", [
    ("ghbdtn", EN, "привет"),
    ("Ghbdtn?", EN, "Привет,"),
    ("rfr", EN, "как"),
    ("ltkf", EN, "дела"),
    ("vjcrdf", EN, "москва"),
    ("Vjcrdf", EN, "Москва"),
    ("gjcvjnhb", EN, "посмотри"),
    ("руддщ", RU, "hello"),
    ("цщкдв", RU, "world"),
    ("шЗрщту", RU, "iPhone"),
    ("щл", RU, "ok"),
    ("yt", EN, "не"),
    ("xnj", EN, "что"),
    ("ytn", EN, "нет"),
    ("ljhjuj", EN, "дорого"),
])
def test_wrong_layout_is_converted(engine, keyboard, typed, lang, expected):
    d = decide(engine, keyboard, typed, lang)
    assert d.action == "convert", explain(d)
    assert d.text == expected


@pytest.mark.parametrize("typed, lang", [
    ("hello", EN), ("world", EN), ("a", EN), ("I", EN), ("vs", EN), ("git", EN), ("React", EN),
    ("привет", RU), ("и", RU), ("в", RU), ("мы", RU), ("пушнуть", RU), ("коммит", RU),
    ("getUserName", EN), ("user_id", EN), ("https://x.com", EN), ("v2", EN), ("3d", EN),
    ("hello,", EN), ("привет,", RU), ("it's", EN), ("e-mail", EN), ("т.е.", RU), ("...", EN),
])
def test_correct_text_is_kept(engine, keyboard, typed, lang):
    d = decide(engine, keyboard, typed, lang)
    assert d.action == "keep", explain(d)


def test_single_letter_needs_context(engine, keyboard):
    assert decide(engine, keyboard, "e", EN).action == "keep"
    # after the next word turned out Russian, "e" is clearly "у"
    d = decide(engine, keyboard, "e", EN, next_lang=RU)
    assert d.action == "convert" and d.text == "у"
    # and in the middle of a Russian phrase
    d = decide(engine, keyboard, "b", EN, prev_langs=[RU, RU])
    assert d.action == "convert" and d.text == "и"


def test_manual_switch_makes_engine_careful(engine, keyboard):
    plain = decide(engine, keyboard, "vs", EN, prev_langs=[RU, RU, RU])
    careful = decide(engine, keyboard, "vs", EN, prev_langs=[RU, RU, RU], manual_switch=True)
    assert careful.threshold > plain.threshold


def test_caps_lock_inversion(engine, keyboard):
    d = decide(engine, keyboard, "пРИВЕТ", RU)
    assert d.action == "fix_case" and d.text == "Привет"
    d = decide(engine, keyboard, "hELLO", EN)
    assert d.action == "fix_case" and d.text == "Hello"


def test_user_rule_wins(engine, keyboard, profile):
    profile.add_rule("layout", "ghbdtn", EN, source="user")
    assert decide(engine, keyboard, "ghbdtn", EN).action == "keep"
    profile.add_rule("layout", "vs", RU, source="user")
    d = decide(engine, keyboard, "vs", EN)
    assert d.action == "convert" and d.text == "мы" and d.reason == "rule"


def test_personal_vocab_changes_the_verdict(engine, keyboard, profile):
    before = decide(engine, keyboard, "rjvvbnyenm", EN)  # "коммитнуть" typed on EN
    for _ in range(5):
        profile.bump_vocab(RU, "коммитнуть")
    after = decide(engine, keyboard, "rjvvbnyenm", EN)
    assert after.alt.source == "personal"
    assert after.margin > before.margin
    assert after.action == "convert"


def test_explain_is_readable(engine, keyboard):
    text = explain(decide(engine, keyboard, "ghbdtn", EN))
    assert "привет" in text and "переключить" in text
