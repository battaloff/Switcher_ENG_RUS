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
    s = make_screen(Config(autocorrect=False), layout=RU)  # for typos autocorrect does not know
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
    s = make_screen(Config(early_switch=False), layout=EN)
    s.write("ghbdtn", EN)
    s.click()  # the cursor may be elsewhere now
    s.space()
    assert s.text == "ghbdtn "


def test_enter_does_not_rewrite_by_default(make_screen):
    s = make_screen(Config(early_switch=False), layout=EN)
    s.write("ghbdtn\n", EN)
    assert s.text == "ghbdtn\n"
    s = make_screen(Config(convert_on_enter=True, early_switch=False), layout=EN)
    s.write("ghbdtn\n", EN)
    assert s.text == "привет\n"
    # switched while typing, before Enter could send anything
    s = make_screen(layout=EN)
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


def test_own_window_is_left_alone(make_screen):
    s = make_screen(layout=EN, app="Switcher")
    s.write("ghbdtn ", EN)
    assert s.text == "ghbdtn "


def test_any_action_can_use_double_ctrl(make_screen):
    config = Config()
    config.hotkeys.toggle = "double_ctrl"
    s = make_screen(config)
    for _ in range(2):
        s._event("press", "ctrl")
        s._event("release", "ctrl")
    assert s.controller.enabled is False


def test_single_key_hotkey_like_punto_pause(make_screen):
    config = Config()
    config.hotkeys.convert_last = "<pause>"
    s = make_screen(config, layout=EN)
    s.write("vs ", EN)
    s._event("press", "pause")
    assert s.text == "мы "


def test_hotkeys_do_nothing_in_own_settings_window(make_screen):
    config = Config()
    config.hotkeys.toggle = "double_ctrl"
    s = make_screen(config, app="Switcher")
    for _ in range(2):
        s._event("press", "ctrl")
        s._event("release", "ctrl")
    assert s.controller.enabled is True


# -- early switch (Punto-style) ------------------------------------------------


def test_layout_switches_after_the_first_letters(make_screen):
    s = make_screen(layout=EN)
    s.write("прив", RU)  # keys of "прив" pressed on the English layout
    assert s.text == "прив" and s.layout == RU
    s.write("ет, как дела ", RU)  # the rest is typed in Russian already
    assert s.text == "привет, как дела "
    s = make_screen(layout=RU)
    s.write("world", EN)
    assert s.text == "world" and s.layout == EN


def test_correct_words_never_switch_mid_word(make_screen):
    for text, lang in (("привет как дела я пушну коммит ", RU), ("hello world see you at the meeting ", EN)):
        s = make_screen(layout=lang)
        s.write(text, lang)
        assert s.text == text and s.layout == lang


def test_early_switch_is_taken_back_when_the_whole_word_says_so(make_screen, monkeypatch):
    from switcher import controller as ctl
    from switcher.engine import EarlyDecision

    def eager(engine, strokes, typed_lang, ctx=None):  # a wrong guess after three letters
        if len(strokes) == 3 and typed_lang == EN:
            text = engine.keyboard.text(strokes, RU)
            return EarlyDecision(True, EN, RU, text=text, reason="early")
        return EarlyDecision(False, typed_lang, RU if typed_lang == EN else EN)

    monkeypatch.setattr(ctl, "decide_prefix", eager)
    s = make_screen(layout=EN)
    s.write("hel", EN)
    assert s.text == "руд" and s.layout == RU
    s.write("lo ", EN)
    assert s.text == "hello " and s.layout == EN


def test_double_shift_mid_word_rejects_the_early_switch_and_teaches(make_screen, profile):
    s = make_screen(layout=EN)
    s.write("ghbd", EN)
    assert s.text == "прив"
    s.double_shift()
    assert s.text == "ghbd" and s.layout == EN
    s.write("tn ", EN)
    assert s.text == "ghbdtn "
    assert profile.layout_rule("ghbdtn", "notes") == (EN, "learned")
    s.write("ghbdtn ", EN)  # no early switch and no switch at the end any more
    assert s.text == "ghbdtn ghbdtn "


def test_undo_after_the_word_restores_what_was_typed(make_screen, profile):
    s = make_screen(layout=EN)
    s.write("ghbdtn ", EN)
    assert s.text == "привет "
    s.double_shift()
    assert s.text == "ghbdtn " and s.layout == EN
    assert profile.layout_rule("ghbdtn", "notes") == (EN, "learned")


def test_erasing_the_switched_letters_and_retyping_teaches(make_screen, profile):
    s = make_screen(layout=EN)
    s.write("ghbd", EN)
    assert s.text == "прив"
    s.backspace_key(4)
    s.switch_layout(EN)
    s.write("ghbdtn ", EN)
    assert s.text == "ghbdtn "
    assert profile.layout_rule("ghbdtn", "notes") == (EN, "learned")


def test_early_switch_fixes_the_short_word_before(make_screen):
    s = make_screen(layout=EN)
    s.write("у меня ", RU)
    assert s.text == "у меня "


def test_no_early_switch_where_it_is_off_or_risky(make_screen):
    s = make_screen(Config(early_switch=False), layout=EN)
    s.write("прив", RU)
    assert s.text == "ghbd"
    s.write("ет ", RU)
    assert s.text == "привет "  # still fixed, at the end of the word
    s = make_screen(layout=EN, app="Code")  # code editors: only whole words, more carefully
    s.write("прив", RU)
    assert s.text == "ghbd"
    s = make_screen(layout=EN)
    s.write("да ", RU)
    s.click()
    s.clock += 2  # a moment later, not the echo of our own switch
    s.switch_layout(EN)  # the user picked the layout by hand just now
    s.write("прив", RU)
    assert s.text.endswith("ghbd")


# -- autocorrect -----------------------------------------------------------------


def test_typos_are_fixed_when_the_word_ends(make_screen, profile):
    s = make_screen(layout=RU)
    s.write("Превет, как дила? ")
    assert s.text == "Привет, как дела? "
    s = make_screen(layout=EN)
    s.write("teh cat ")
    assert s.text == "the cat "
    assert [e["kind"] for e in profile.events(["spell"])] == ["spell", "spell", "spell"]


def test_wrong_layout_and_typo_together(make_screen):
    s = make_screen(layout=EN)
    s.write("превет ", RU)  # keys of "превет" on the English layout
    assert s.text == "привет "


def test_undoing_a_correction_protects_the_word(make_screen, profile):
    s = make_screen(layout=RU)
    s.write("превет ")
    assert s.text == "привет "
    s.double_shift()
    assert s.text == "превет "
    s.write("превет ")
    assert s.text == "превет превет "
    assert profile.personal_zipf("превет", RU) is not None


def test_retyping_the_original_protects_it_too(make_screen):
    s = make_screen(layout=RU)
    s.write("превет ")
    s.backspace_key(7)
    s.write("превет ")
    assert s.text == "превет "


def test_autocorrect_leaves_names_abbreviations_code_and_real_words(make_screen):
    s = make_screen(layout=RU)
    s.write("были у Превета и в ПРЕВЕТ щас ваще ")
    assert s.text == "были у Превета и в ПРЕВЕТ щас ваще "
    s = make_screen(layout=RU, app="Code")
    s.write("превет ")
    assert s.text == "превет "
    s = make_screen(Config(autocorrect=False), layout=RU)
    s.write("превет ")
    assert s.text == "превет "
