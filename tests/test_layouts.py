from switcher.layouts import EN, RU, Keyboard, Stroke, canonical_keys, harmonize_case, text_lang


def test_convert_both_ways(keyboard):
    assert keyboard.convert("ghbdtn", EN, RU) == "привет"
    assert keyboard.convert("Ghbdtn? rfr ltkf", EN, RU) == "Привет, как дела"
    assert keyboard.convert("руддщ", RU, EN) == "hello"
    assert keyboard.convert("ok.", EN, RU) == "щлю"
    assert keyboard.convert("`k", EN, RU) == "ёл"


def test_every_key_round_trips():
    for variant in ("pc", "mac"):
        kb = Keyboard(variant)
        for lang in (EN, RU):
            for code in "`1234567890-=qwertyuiop[]\\asdfghjkl;'zxcvbnm,./":
                for shift in (False, True):
                    stroke = Stroke(code, shift)
                    ch = kb.layouts[lang].char(stroke)
                    assert kb.layouts[lang].stroke(ch) is not None


def test_mac_variant_differs_in_punctuation():
    mac = Keyboard("mac")
    assert mac.convert("ghbdtn\\", EN, RU) == "приветё"
    assert mac.convert("^", EN, RU) == ","


def test_stroke_for_char_uses_letters_then_hint(keyboard):
    assert keyboard.stroke_for_char("ж", None) == (Stroke(";"), RU)
    assert keyboard.stroke_for_char(";", EN) == (Stroke(";"), EN)
    # ";" on the Russian layout is Shift+4
    assert keyboard.stroke_for_char(";", RU) == (Stroke("4", True), RU)


def test_caps_words_stay_caps():
    assert harmonize_case("HELLO,", "РУДДЩб") == "РУДДЩБ"
    assert harmonize_case("Hello,", "Руддщб") == "Руддщб"


def test_helpers(keyboard):
    assert text_lang("привет") == RU
    assert text_lang("hi привет") == "mixed"
    assert text_lang("123") is None
    assert canonical_keys(keyboard.strokes("Привет", RU)) == "ghbdtn"
