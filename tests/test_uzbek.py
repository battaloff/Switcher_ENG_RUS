from switcher.uzbek import known, looks_uzbek, to_cyrillic


def test_latin_to_cyrillic():
    assert [to_cyrillic(w) for w in ("yo'q", "rahmat", "ertaga", "qayerda", "o'zbek", "ma'lumot", "yozaman", "ming")] \
        == ["йўқ", "раҳмат", "эртага", "қаерда", "ўзбек", "маълумот", "ёзаман", "минг"]


def test_words_and_forms_in_both_alphabets():
    for word in ("олдин", "oldin", "йўқ", "йук", "yo'q", "yo‘q", "рахмат", "odamlarga", "келдим", "ishlayapman",
                 "китобни", "telefoningiz"):
        assert looks_uzbek(word), word
    assert looks_uzbek("qo'shiq") and not known("qo'shiq")  # an Uzbek-only letter is enough to tell


def test_russian_and_english_words_typed_on_the_wrong_layout_are_not_taken_for_uzbek():
    for word in ("ghbdtn", "rfr", "ltkf", "vtyz", "ctujlyz", "руддщ", "цщкд", "то`"):
        assert not known(word), word
