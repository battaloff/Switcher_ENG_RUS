from switcher.config import Config
from switcher.layouts import EN, RU


def test_wrong_layout_word_is_fixed_on_space(make_screen):
    s = make_screen(layout=EN)
    s.write("привет ")  # the user means Russian but the layout is English
    assert s.text == "привет "
    assert s.layout == RU
    s.write("как дела ")  # now the layout is right, nothing to fix
    assert s.text == "привет как дела "


def test_english_typed_on_russian_layout(make_screen):
    s = make_screen(layout=RU)
    s.write("hello world ")
    assert s.text == "hello world "
    assert s.layout == EN


def test_correct_text_is_untouched(make_screen):
    s = make_screen(layout=EN)
    s.write("see you at the git meeting ")
    assert s.text == "see you at the git meeting "
    s = make_screen(layout=RU)
    s.write("я пушну коммит завтра ")
    assert s.text == "я пушну коммит завтра "


def test_punctuation_follows_the_layout(make_screen):
    s = make_screen(layout=EN)
    s.write("привет, как дела? ", RU)
    assert s.text == "привет, как дела? "


def test_look_back_fixes_the_short_word_before(make_screen):
    s = make_screen(layout=EN)
    s.write("у меня ")
    assert s.text == "у меня "
    s = make_screen(layout=EN)
    s.write("а в итоге ")
    assert s.text == "а в итоге "


def test_look_back_after_a_manual_layout_switch(make_screen):
    s = make_screen(layout=RU)
    s.write("да ", RU)
    s.click()
    s.switch_layout(EN)  # the user picks the wrong layout by hand
    s.write("у меня ", RU)
    assert s.text == "да у меня "


def test_undo_with_double_shift_and_learning(make_screen, profile):
    s = make_screen(layout=EN)
    s.write("ghbdtn ", EN)  # the user really means "ghbdtn"
    assert s.text == "привет "
    s.double_shift()
    assert s.text == "ghbdtn "
    assert s.layout == EN
    assert profile.layout_rule("ghbdtn", "notes") == (EN, "learned")
    # next time it stays as typed
    s.write("ghbdtn ", EN)
    assert s.text == "ghbdtn ghbdtn "


def test_manual_convert_teaches_a_rule(make_screen, profile):
    s = make_screen(layout=EN)
    s.write("vs ", EN)  # ambiguous: could be "vs" or "мы"
    assert s.text == "vs "
    s.double_shift()
    assert s.text == "мы "
    assert s.layout == RU
    assert profile.layout_rule("vs", "notes")[0] == RU
    s.click()
    s.switch_layout(EN)
    s.write("vs ", EN)
    assert s.text == "мы мы "


def test_double_shift_twice_reverts_and_forgets(make_screen, profile):
    s = make_screen(layout=EN)
    s.write("vs ", EN)
    s.double_shift()
    s.double_shift()
    assert s.text == "vs "
    assert profile.layout_rule("vs", "notes") is None


def test_manual_convert_in_the_middle_of_a_word(make_screen, profile):
    s = make_screen(layout=EN)
    s.write("ghb", EN)
    s.double_shift()
    assert s.text == "при"
    s.write("вет ", RU)  # the OS layout is Russian now
    assert s.text == "привет "
    assert profile.layout_rule("ghbdtn", "notes")[0] == RU


def test_capital_letters_do_not_trigger_double_shift(make_screen):
    s = make_screen(layout=EN)
    s.shift_letter("h")
    s.shift_letter("i")
    s.space()
    assert s.text == "HI "


def test_retyping_in_the_other_layout_is_learned(make_screen, profile):
    s = make_screen(layout=EN)
    s.write("ghbdtn ", EN)
    assert s.text == "привет "
    s.backspace_key(7)  # erase "привет "
    s.switch_layout(EN)
    s.write("ghbdtn ", EN)
    assert s.text == "ghbdtn "
    assert profile.layout_rule("ghbdtn", "notes") == (EN, "learned")
    assert [e["kind"] for e in profile.events(["undo"])] == ["undo"]


def test_retype_after_a_miss_is_learned(make_screen, profile):
    s = make_screen(layout=EN)
    s.write("vs ", EN)  # kept, but the user wanted "мы"
    s.backspace_key(3)
    s.switch_layout(RU)
    s.write("мы ")
    assert s.text == "мы "
    assert profile.layout_rule("vs", "notes")[0] == RU


def test_typo_fixed_twice_becomes_a_rule(make_screen, profile):
    s = make_screen(layout=RU)
    for _ in range(2):
        s.write("превет ")
        s.backspace_key(7)
        s.write("привет ")
        s.click()
    assert profile.replace_rule("превет", "notes").value == "привет"
    s.write("Превет ")
    assert s.text.endswith("Привет ")
    # and the replacement can be undone like any other change
    s.double_shift()
    assert s.text.endswith("Превет ")
    assert profile.replace_rule("превет", "notes") is None


def test_prefix_fix_is_journaled(make_screen, profile):
    s = make_screen(layout=EN)
    s.write("ghb", EN)
    s.backspace_key(3)
    s.switch_layout(RU)
    s.write("привет ")
    assert [e["kind"] for e in profile.events(["prefix_fix"])] == ["prefix_fix"]


def test_caps_lock_is_fixed(make_screen):
    s = make_screen(layout=RU)
    s.caps = True
    s.shift_letter("g")  # Shift+П with Caps Lock on types "п"
    s.write("ривет ", RU)
    assert s.text == "Привет "
    assert s.caps is False


def test_mouse_click_forgets_the_context(make_screen):
    s = make_screen(layout=EN)
    s.write("ghbdtn", EN)
    s.click()  # the cursor may be elsewhere now
    s.space()
    assert s.text == "ghbdtn "


def test_enter_does_not_rewrite_by_default(make_screen):
    s = make_screen(layout=EN)
    s.write("ghbdtn\n", EN)
    assert s.text == "ghbdtn\n"
    s = make_screen(Config(convert_on_enter=True), layout=EN)
    s.write("ghbdtn\n", EN)
    assert s.text == "привет\n"


def test_excluded_apps_are_left_alone(make_screen):
    s = make_screen(layout=EN, app="KeePassXC")
    s.write("ghbdtn ", EN)
    assert s.text == "ghbdtn "


def test_pause_hotkey(make_screen):
    s = make_screen(layout=EN)
    s.controller.run_hotkey("toggle")
    s.write("ghbdtn ", EN)
    assert s.text == "ghbdtn "
    assert s.notes


def test_selection_is_converted(make_screen):
    s = make_screen(layout=EN)
    s.text = "Ghbdtn? rfr ltkf"
    s.selection = "Ghbdtn? rfr ltkf"
    s.controller.convert_selection()
    assert s.text == "Привет, как дела"
    assert s.layout == RU


class FakeAI:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def fix_phrase(self, pieces, style="", app=""):
        self.calls.append(pieces)
        return self.answer


def test_ai_fix_rewrites_the_phrase_and_learns(make_screen, profile):
    ai = FakeAI("я запушил в main ")
    s = make_screen(Config(auto_switch=False), layout=EN, ai=ai)
    s.write("z pfgeibk d main ", EN)
    assert s.text == "z pfgeibk d main "
    s.controller.run_hotkey("ai_fix")
    assert s.text == "я запушил в main "
    pieces = ai.calls[0]
    assert pieces[1] == {"screen": "pfgeibk", "en": "pfgeibk", "ru": "запушил", "delim": " "}
    assert profile.layout_rule("pfgeibk", "notes")[0] == RU
