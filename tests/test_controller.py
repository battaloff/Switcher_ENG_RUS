from switcher.config import Config
from switcher.layouts import EN, RU
from switcher.report import rule_rows


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

    def fix_phrase(self, pieces, style="", app="", typos=None, uzbek=False):
        self.calls.append(pieces)
        self.typos, self.uzbek = typos, uzbek
        if isinstance(self.answer, Exception):
            raise self.answer
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
    s.switch_layout(EN)  # the user picked the layout by hand just now, and picked it wrong
    s.write("прив", RU)
    assert s.text.endswith("прив")  # an obvious start still switches at once


# -- autocorrect -----------------------------------------------------------------


def test_typos_are_fixed_when_the_word_ends(make_screen, profile):
    s = make_screen(layout=RU)
    s.write("Превет, как дила? ")
    assert s.text == "Привет, как дела? "
    s = make_screen(layout=EN)
    s.write("teh cat ")
    assert s.text == "the cat "
    assert [e["kind"] for e in profile.events(["spell"])] == ["spell", "spell", "spell"]


def test_a_wrong_vowel_in_a_long_word(make_screen):
    s = make_screen(layout=RU)
    s.write("Здривствуйте, как дела ")
    assert s.text == "Здравствуйте, как дела "


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


def test_rename_box_in_capitals_finished_with_enter(make_screen):
    """The screenshot: "НА МАШИНАХ" typed with Shift on the English layout in Explorer, then Enter."""
    s = make_screen(layout=EN, app="explorer")
    for code in "yf":
        s.shift_letter(code)
    s.space()
    for code in "vfib":
        s.shift_letter(code)
    assert s.text == "НА МАШИ" and s.layout == RU  # the word before switched along with it
    for code in "yf[":
        s.shift_letter(code)
    s.enter()
    assert s.text == "НА МАШИНАХ\n"


def test_short_word_is_fixed_even_when_the_phrase_ends_with_enter(make_screen):
    s = make_screen(layout=EN)
    s.write("у меня\n", RU)
    assert s.text == "у меня\n"


def test_double_shift_mid_word_takes_back_the_words_switched_along(make_screen):
    s = make_screen(layout=EN)
    s.write("у меня", RU)  # keys of "у меня" on the English layout, no space yet
    assert s.text == "у меня"
    s.double_shift()
    assert s.text == "e vtyz" and s.layout == EN


# -- fixing the selection (Shift+Pause) -------------------------------------------


def select(s, text):
    s.text = text
    s.selection = text


def test_right_text_is_left_alone_and_a_second_press_swaps_it(make_screen):
    s = make_screen(layout=EN)
    select(s, "shift+pause")
    s.controller.convert_selection()
    assert s.text == "shift+pause" and "выглядит правильно" in s.notes[-1]
    s.selection = "shift+pause"  # still selected, pressed again: the user insists
    s.controller.convert_selection()
    assert s.text == "ыршае+зфгыу" and s.layout == RU


def test_only_wrong_words_change_and_typos_are_fixed(make_screen):
    s = make_screen(layout=EN)
    select(s, "hello ghbdtn? rfr ltkf? превет")  # "?" on the EN layout is the Russian ","
    s.controller.convert_selection()
    assert s.text == "hello привет, как дела, привет"


def test_claude_fixes_the_selection_with_typos_allowed(make_screen):
    ai = FakeAI("Reliable")
    s = make_screen(layout=EN, ai=ai)
    select(s, "RE;liable")
    s.controller.convert_selection()
    assert s.text == "Reliable"
    assert ai.typos is True
    assert ai.calls[0] == [{"screen": "RE;liable", "en": "RE;liable", "ru": "КУждшфиду", "delim": ""}]


def test_without_claude_the_selection_is_still_fixed_locally(make_screen):
    s = make_screen(layout=EN, ai=FakeAI(RuntimeError("нет сети")))
    select(s, "Ghbdtn? rfr ltkf RE;liable")
    s.controller.convert_selection()
    assert s.text == "Привет, как дела RE;liable"
    assert "без него" in s.notes[-1]


def test_what_switcher_knows_is_fixed_at_once_without_claude(make_screen):
    ai = FakeAI("должен не понадобиться")
    s = make_screen(layout=EN, ai=ai)
    select(s, "Ghbdtn? rfr ltkf")
    s.controller.convert_selection()
    assert s.text == "Привет, как дела"
    select(s, "shift+pause")
    s.controller.convert_selection()
    assert s.text == "shift+pause" and "выглядит правильно" in s.notes[-1]
    assert ai.calls == []


def test_words_joined_by_keys_both_layouts_share_are_fixed_one_by_one(make_screen):
    s = make_screen(layout=RU, ai=FakeAI("должен не понадобиться"))
    select(s, "ыршае=зфгыу")
    s.controller.convert_selection()
    assert s.text == "shift=pause" and s.layout == EN
    select(s, "ыршае+зфгыу")
    s.controller.convert_selection()
    assert s.text == "shift+pause"


def test_the_claude_hotkey_always_asks_claude(make_screen):
    ai = FakeAI("Привет, как дела")
    s = make_screen(layout=EN, ai=ai)
    select(s, "Ghbdtn? rfr ltkf")
    s.controller.ai_fix()
    assert s.text == "Привет, как дела" and len(ai.calls) == 1


def test_unknown_text_without_claude_says_so(make_screen):
    s = make_screen(layout=EN)
    select(s, "RE;liable")
    s.controller.convert_selection()
    assert s.text == "RE;liable" and "Не знаю" in s.notes[-1]


def test_selection_hotkey_waits_until_shift_is_released(make_screen):
    config = Config()
    config.hotkeys.convert_selection = "<shift>+<pause>"
    s = make_screen(config, layout=EN)
    select(s, "Ghbdtn")
    s._event("press", "shift")
    s._event("press", "pause")
    assert s.text == "Ghbdtn"  # Ctrl+C now would reach the app as Ctrl+Shift+C
    s._event("release", "shift")
    assert s.text == "Привет"


def test_two_capitals_are_fixed(make_screen):
    for keys, layout, expected in [("PLhfdcndeqnt ", RU, "Здравствуйте "), ("GHbdtn ", EN, "Привет "),
                                   ("HEllo ", EN, "Hello "), ("THe ", EN, "The "),
                                   ("PLhbdcndeqnt ", RU, "Здравствуйте ")]:  # the typo is fixed as well
        s = make_screen(layout=layout)
        s.keys(keys)
        assert s.text == expected, keys


def test_names_spelt_with_two_capitals_are_left_alone(make_screen):
    for word in ("PCs ", "IDs ", "OK ", "VMware ", "OAuth ", "IPhone "):
        s = make_screen(layout=EN)
        s.keys(word)
        assert s.text == word


def test_double_shift_puts_two_capitals_back_for_good(make_screen, profile):
    s = make_screen(layout=EN)
    s.keys("HEllo ")
    assert s.text == "Hello "
    s.double_shift()
    assert s.text == "HEllo "
    assert profile.get_rule("case", "hello").value == "HEllo"
    row = next(r for r in rule_rows(profile, s.kb) if r["rule"].kind == "case")
    assert row["word"] == "HEllo" and "заглавные" in row["result"]
    s.keys("HEllo ")
    assert s.text == "HEllo HEllo "


def test_two_capitals_can_be_switched_off(make_screen):
    config = Config()
    config.fix_two_capitals = False
    s = make_screen(config, layout=RU)
    s.keys("GHbdtn ")
    assert s.text == "ПРивет "


def test_two_capitals_in_the_selection(make_screen):
    ai = FakeAI("не понадобится")
    s = make_screen(layout=EN, ai=ai)
    select(s, "ЗДравствуйте ПРивет")
    s.controller.convert_selection()
    assert s.text == "Здравствуйте Привет" and ai.calls == []


def uzbek_config():
    config = Config()
    config.writes_uzbek = True
    return config


def test_uzbek_words_are_left_alone_for_someone_who_writes_uzbek(make_screen):
    s = make_screen(layout=RU)
    s.write("олдин жуда ", RU)
    assert s.text == "один ;elf "  # what happens without the setting
    s = make_screen(uzbek_config(), layout=RU)
    s.write("олдин улар жуда эмас ёмон ", RU)
    assert s.text == "олдин улар жуда эмас ёмон "
    s = make_screen(uzbek_config(), layout=EN)
    s.keys("bugun keldim yo'q oldin ")
    assert s.text == "bugun keldim yo'q oldin "


def test_russian_and_english_are_still_fixed_with_uzbek_on(make_screen):
    s = make_screen(uzbek_config(), layout=RU)
    s.write("превет ", RU)
    s.keys("hello ")  # on the Russian layout: "руддщ"
    assert s.text == "привет hello "
    s = make_screen(uzbek_config(), layout=EN)
    s.keys("ye ghbdtn ")  # "ye" is also Uzbek, but too short to tell
    assert s.text == "ну привет "


def test_an_uzbek_phrase_takes_back_the_correction_of_the_word_before(make_screen, profile):
    s = make_screen(uzbek_config(), layout=RU)
    s.write("Бозор ", RU)
    assert s.text == "Обзор "  # not a listed word: looks like a Russian typo…
    s.write("мен ", RU)
    assert s.text == "Бозор мен "  # …until the next word shows the phrase is Uzbek
    assert profile.personal_zipf("бозор", RU) is not None
    s.write("бозор ", RU)
    assert s.text == "Бозор мен бозор "


def test_no_switch_into_a_non_word_for_uzbek_writers(make_screen):
    s = make_screen(uzbek_config(), layout=RU)
    s.write("гушт ", RU)
    assert s.text == "гушт "


def test_uzbek_selection_is_left_alone_and_claude_is_told(make_screen):
    ai = FakeAI("не понадобится")
    s = make_screen(uzbek_config(), layout=EN, ai=ai)
    select(s, "олдин улар жуда")
    s.controller.convert_selection()
    assert s.text == "олдин улар жуда" and ai.calls == []
    ai.answer = "Reliable"
    select(s, "RE;liable")
    s.controller.convert_selection()
    assert s.text == "Reliable" and ai.uzbek is True


def test_quotes_typed_with_the_other_layouts_key(make_screen):
    # the Russian quote is Shift+2 ("@" on English), the English one Shift+' ("Э" on Russian)
    for layout in (RU, EN):
        for keys in ('";len dsdjlf" b "dsdtltyj" ', '@;len dsdjlf@ b @dsdtltyj@ '):
            s = make_screen(layout=layout)
            s.keys(keys)
            assert s.text == '"ждут вывода" и "выведено" ', (layout, keys)
    s = make_screen(layout=RU)
    s.keys('@hello@ ')  # English in quotes, typed on the Russian layout
    assert s.text == '"hello" '


def test_words_starting_with_e_are_not_taken_for_quotes(make_screen):
    for layout in (RU, EN):
        s = make_screen(layout=layout)
        s.keys('"njn "[ ')
        assert s.text == "Этот Эх ", layout


def test_mentions_and_emails_stay(make_screen):
    s = make_screen(layout=EN)
    s.keys("@ivan ivan@mail ")
    assert s.text == "@ivan ivan@mail "


def test_double_shift_puts_the_letter_back(make_screen):
    s = make_screen(layout=RU)
    s.keys('";len ')
    assert s.text == '"ждут '
    s.double_shift()
    assert s.text == "Эждут "


def test_a_long_word_with_an_extra_letter_is_fixed(make_screen):
    s = make_screen(layout=RU)
    s.write("отреагироваоли ", RU)
    assert s.text == "отреагировали "  # was left: 3.4 - 1.0 - 1.5 came out 0.8999… against 0.9
    s = make_screen(layout=EN, ai=FakeAI("не понадобится"))
    select(s, "отреагироваоли")
    s.controller.convert_selection()
    assert s.text == "отреагировали" and s.controller.ai.calls == []


def test_the_selection_fix_is_bolder_than_autocorrect(make_screen):
    s = make_screen(layout=RU)
    s.write("дорошка ", RU)
    assert s.text == "дорошка "  # typing: not sure enough to change it unasked
    s = make_screen(layout=EN)  # no Claude: the bolder guess is pasted
    select(s, "дорошка")
    s.controller.convert_selection()
    assert s.text == "дорожка"
    ai = FakeAI("дорожка")
    s = make_screen(layout=EN, ai=ai)  # with Claude: Claude checks the bolder guess
    select(s, "дорошка")
    s.controller.convert_selection()
    assert s.text == "дорожка" and len(ai.calls) == 1


def test_typos_in_the_selection_are_fixed_even_with_autocorrect_off(make_screen):
    config = Config()
    config.autocorrect = False
    s = make_screen(config, layout=EN)
    select(s, "превет")
    s.controller.convert_selection()
    assert s.text == "привет"


def test_typing_after_win_l_and_unlocking(make_screen):
    # Win+L: the lock screen takes over at once, so the releases of Win and L never reach Switcher
    s = make_screen(layout=EN)
    s._event("press", "cmd")
    s._event("press", "char", char="l", code="l")
    s.lose_releases()
    s.keys("ghbdtn ")  # back after unlocking: Windows says no modifier is held
    assert s.text.endswith("привет ")


def test_typing_after_ctrl_alt_del(make_screen):
    s = make_screen(layout=EN)
    s._event("press", "ctrl")
    s._event("press", "alt")
    s._event("press", "delete")
    s.lose_releases()
    s.keys("ghbdtn ")
    assert s.text.endswith("привет ")


def test_a_lost_release_is_forgotten_in_time_where_the_os_cannot_tell(make_screen):
    s = make_screen(layout=EN)
    s.os_knows_mods = False
    s._event("press", "cmd")
    s.keys("ghbdtn ")
    assert s.text == "ghbdtn "  # Win held: shortcuts, left alone
    s.clock += 60
    s.keys("ghbdtn ")
    assert s.text == "ghbdtn привет "


def test_a_new_hook_forgets_held_keys(make_screen):
    s = make_screen(layout=EN)
    s.os_knows_mods = False
    s._event("press", "ctrl")
    s._event("press", "hook-restored")
    s.keys("ghbdtn ")
    assert s.text == "привет "


def snippet_config(**snippets):
    config = Config()
    config.snippets = {"015": "015-510-400_4_", "525": "525-459_4_", "745": "745-605_4_", **snippets}
    config.snippets_only_in_save_dialogs = False  # how snippets work; where they work is tested below
    return config


def test_by_default_snippets_complete_only_file_names(make_screen):
    config = snippet_config()
    config.snippets_only_in_save_dialogs = Config().snippets_only_in_save_dialogs
    s = make_screen(config, layout=EN, app="CorelDRW")
    s.keys("745 ")  # a size typed in CorelDRAW
    assert s.text == "745 "
    s.save_dialog = True  # File → Export: the file name
    s.click()
    s.text = ""
    s.keys("745")
    assert s.text == "745-605_4_"


def test_a_snippet_is_completed_as_soon_as_its_start_is_typed(make_screen):
    s = make_screen(snippet_config(), layout=EN)
    s.keys("015")
    assert s.text == "015-510-400_4_"
    s.keys("12 745")
    assert s.text == "015-510-400_4_12 745-605_4_"


def test_snippets_can_be_switched_off(make_screen):
    config = snippet_config()
    config.snippets_enabled = False
    s = make_screen(config, layout=EN)
    s.keys("015 ")
    assert s.text == "015 "


def test_text_typed_by_autohotkey_is_not_taken_for_the_word(make_screen):
    s = make_screen(layout=EN)
    s.keys("ghb")
    s.text += "by the way"  # an AutoHotkey hotstring typed this; Switcher only hears that something did
    s._event("press", "foreign-input")
    s.keys(" ")
    assert s.text == "ghbby the way "  # nothing erased or "fixed" in what AutoHotkey typed
    s.keys("ghbdtn ")
    assert s.text == "ghbby the way привет "


def test_only_at_the_start_of_a_word(make_screen):
    s = make_screen(snippet_config(), layout=EN)
    s.keys("1015 20150 ")
    assert s.text == "1015 20150 "


def test_double_shift_takes_the_completion_back(make_screen):
    s = make_screen(snippet_config(), layout=RU)
    s.keys("525")
    assert s.text == "525-459_4_"
    s.double_shift()
    assert s.text == "525"
    s.keys("1 ")
    assert s.text == "5251 "


def test_a_shorter_snippet_waits_for_the_end_of_the_word(make_screen):
    s = make_screen(snippet_config(**{"01": "01-ТЕСТ"}), layout=EN)
    s.keys("01 ")
    assert s.text == "01-ТЕСТ "
    s.keys("015")
    assert s.text == "01-ТЕСТ 015-510-400_4_"


def test_a_snippet_typed_on_the_wrong_layout(make_screen):
    s = make_screen(snippet_config(**{"адр": "г. Ташкент, ул. Навои 1"}), layout=EN)
    s.keys("flh")  # "адр" on the English layout
    assert s.text == "г. Ташкент, ул. Навои 1"


def test_save_as_switches_to_english(make_screen):
    s = make_screen(layout=RU)
    s._event("press", "save-dialog")
    assert s.layout == EN
    config = Config()
    config.english_in_save_dialogs = False
    s = make_screen(config, layout=RU)
    s._event("press", "save-dialog")
    assert s.layout == RU


def test_a_snippet_survives_the_save_dialog_switch_coming_late(make_screen):
    s = make_screen(snippet_config(), layout=RU)
    s.keys("01")
    s._event("press", "save-dialog")  # the dialog was slow to set up: we switch while the user types
    s.keys("5")
    assert s.text == "015-510-400_4_" and s.layout == EN


def test_a_proactive_new_hook_does_not_forget_the_word(make_screen):
    s = make_screen(snippet_config(), layout=EN)
    s.keys("52")
    s._event("press", "hook-reinstalled")  # back after a break: nothing was missed
    s.keys("5")
    assert s.text == "525-459_4_"


def test_a_snippet_typed_on_the_numeric_keypad(make_screen):
    from switcher.platform.base import numpad_key

    s = make_screen(snippet_config(), layout=RU)
    for vk in (0x60, 0x61, 0x65):  # NumPad 0, 1, 5: what Windows reports for them
        char, code = numpad_key(vk, s.layout)
        s.text += char
        s._event("press", "char", char=char, code=code)
    assert s.text == "015-510-400_4_"
    assert numpad_key(0x6E, "ru") == (",", None) and numpad_key(0x6E, "en") == (".", None)
    assert numpad_key(0x41, "en") is None


def test_letters_no_word_can_start_with_switch_at_once(make_screen):
    """Like Punto: "фьш" starts no Russian word, so "amirbek" typed on the Russian layout switches early."""
    s = make_screen(layout=RU)
    s.keys("ami")
    assert s.text == "ami" and s.layout == EN
    s.keys("rbek ")
    assert s.text == "amirbek "


def test_a_typo_that_looks_impossible_is_put_back_at_the_end(make_screen):
    s = make_screen(layout=RU)
    s.write("ыегодня ", RU)  # "ыег…" switches early, the whole word is Russian with a typo
    assert s.text == "сегодня "


def test_wrong_layout_and_a_typo_together(make_screen):
    """"j,][zdktybb" reads "объхявлении" in Russian: one key too many — the user meant "объявлении"."""
    s = make_screen(layout=EN)
    s.keys("j,][zdktybb ")
    assert s.text == "объявлении " and s.layout == RU
    s.double_shift()  # not wanted: exactly what was typed comes back
    assert s.text == "j,][zdktybb " and s.layout == EN


def test_double_shift_fixes_the_typo_too(make_screen):
    config = Config()
    config.auto_switch = False  # nothing changes by itself: the user asks with double Shift
    s = make_screen(config, layout=EN)
    s.keys("j,][zdktybb ")
    assert s.text == "j,][zdktybb "
    s.double_shift()
    assert s.text == "объявлении " and s.layout == RU
    s.double_shift()  # and back, to exactly what was typed
    assert s.text == "j,][zdktybb " and s.layout == EN


def test_the_selection_gets_the_layout_and_the_typo_fixed(make_screen):
    s = make_screen(layout=EN)
    s.keys("j,][zdktybb")
    s.selection = "j,][zdktybb"
    s.controller.convert_selection()
    assert s.text == "объявлении"


def test_words_that_are_no_typos_stay(make_screen):
    s = make_screen(layout=EN)
    s.keys("github nginx lol ")
    assert s.text == "github nginx lol "
    s = make_screen(layout=RU)
    s.write("щас ржунимагу ", RU)
    assert s.text == "щас ржунимагу "


def test_a_rule_for_one_key_does_not_turn_ya_into_z(make_screen, profile):
    """A rule "the key z stays English" (learned from one odd case) turned «Да я понял» into «Да z понял»."""
    profile.add_rule("layout", "z", EN, source="learned")
    profile.add_rule("layout", "yf", EN, source="learned")  # "на"
    s = make_screen(layout=RU)
    s.write("Да я понял, на ", RU)
    assert s.text == "Да я понял, на "
    profile.add_rule("layout", "z", EN, source="user")  # one the user added by hand still counts
    s = make_screen(layout=RU)
    s.write("я ", RU)
    assert s.text == "z "


def test_no_rule_is_learned_for_one_key_or_a_common_word(models, keyboard, profile):
    from switcher.learner import Learner

    learner = Learner(profile, models, keyboard)
    strokes = keyboard.strokes("z", EN)
    learner.undo(app="telegram", strokes=strokes, typed_lang=EN, typed_text="z", converted_lang=RU,
                 converted_text="я")  # the user wanted a Latin "z" once
    learner.manual(app="telegram", strokes=keyboard.strokes("yf", EN), typed_lang=RU, typed_text="на",
                   target_lang=EN, target_text="yf")  # "на" is too common to ever be turned into "yf"
    learner.manual(app="telegram", strokes=keyboard.strokes("ghbdtn", EN), typed_lang=EN, typed_text="ghbdtn",
                   target_lang=RU, target_text="привет")
    assert [r.pattern for r in profile.rules("layout")] == ["ghbdtn"]


def test_risky_rules_learned_before_are_dropped(models, keyboard, profile):
    from switcher.learner import Learner

    profile.add_rule("layout", "z", EN, source="learned")
    profile.add_rule("layout", "yf", EN, source="ai")
    profile.add_rule("layout", "ghbdtn", RU, source="learned")
    profile.add_rule("layout", "d", EN, source="user")
    Learner(profile, models, keyboard)
    assert sorted(r.pattern for r in profile.rules("layout")) == ["d", "ghbdtn"]


def test_two_capitals_in_words_switcher_does_not_know(make_screen):
    s = make_screen(layout=RU)
    s.write("ЙУк МАники ", RU)  # Uzbek "йук", a name: the Shift was let go a letter late
    assert s.text == "Йук Маники "


def test_abbreviations_keep_their_capitals(make_screen):
    s = make_screen(layout=RU)
    s.write("с ДРом ИПшник ЦУшки ПКа ВКонтакте ТЦ ", RU)
    assert s.text == "с ДРом ИПшник ЦУшки ПКа ВКонтакте ТЦ "
    s = make_screen(layout=RU)
    s.write("СТол ГРом ", RU)  # known words: a late Shift
    assert s.text == "Стол Гром "


def test_a_long_word_with_a_neighbouring_key_hit_on_the_way(make_screen):
    s = make_screen(layout=RU)
    s.write("Заниматекльная ", RU)  # "к" is next to "е": the finger caught it on the way
    assert s.text == "Занимательная "
