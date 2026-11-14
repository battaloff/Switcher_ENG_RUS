"""The «Сохранить как» panel: which folder a dialog shows, what a button copies, where things go."""

import json

from switcher.config import Config, load_config
from switcher.savebar import (
    PALETTE,
    Button,
    Plan,
    buttons_of,
    color_name,
    darker,
    default_buttons,
    folder_from_address,
    hotkey_map,
    hotkey_vk,
    ink_end,
    normal_color,
    place_bar,
    plan,
    settled_ink,
    shown_buttons,
    text_on,
)

JOB = r"\\192.168.1.41\f\вывод CtP ФАЙЛЫ НА ФОРМЫ!!!!!\2026\НОЯБРЬ\2,11,26\готов\ayk для s.raximov   (id_57306)"
KNOWN = {"рабочий стол": r"C:\Users\me\Desktop", "desktop": r"C:\Users\me\Desktop",
         "документы": r"C:\Users\me\Documents"}


def test_the_folder_is_read_from_the_address_bar():
    assert folder_from_address("Адрес: " + JOB, KNOWN) == (JOB, "ayk для s.raximov   (id_57306)")
    assert folder_from_address("Адрес: " + JOB + "\\PDF", KNOWN) == (JOB + "\\PDF", "PDF")
    assert folder_from_address(r"Address: C:\Work\Order 1\\", KNOWN) == (r"C:\Work\Order 1", "Order 1")
    assert folder_from_address(r"C:\Work", KNOWN) == (r"C:\Work", "Work")  # no "Адрес:" at all
    assert folder_from_address("Адрес: C:\\", KNOWN) == ("C:\\", "C:\\")


def test_folders_the_address_bar_shows_by_name():
    assert folder_from_address("Адрес: Рабочий стол", KNOWN) == (r"C:\Users\me\Desktop", "Рабочий стол")
    assert folder_from_address(r"Адрес: Библиотеки\Документы\Работа", KNOWN) == (
        r"C:\Users\me\Documents\Работа", "Работа")
    assert folder_from_address("Адрес: Локальный диск (D:)", KNOWN) == ("D:\\", "Локальный диск (D:)")
    assert folder_from_address(r"Адрес: Этот компьютер\Новый том (e:)\Заказы", KNOWN) == (r"E:\Заказы", "Заказы")
    assert folder_from_address("Адрес: Этот компьютер", KNOWN) is None
    assert folder_from_address("Адрес: Сеть", KNOWN) is None
    assert folder_from_address("", KNOWN) is None


def test_a_click_copies_the_name_and_goes_into_pdf():
    assert plan(JOB, "ayk для s.raximov   (id_57306)", True) == Plan("ayk для s.raximov   (id_57306)", JOB + "\\PDF")
    assert plan(JOB, "x", False) == Plan("x", None)
    # already in "PDF": the order's name, and no "PDF" inside "PDF"
    assert plan(JOB + "\\PDF", "PDF", True) == Plan("ayk для s.raximov   (id_57306)", None)
    assert plan(JOB + "\\pdf\\", "pdf", True) == Plan("ayk для s.raximov   (id_57306)", None)
    assert plan("C:\\", "C:\\", True) == Plan("C:\\", "C:\\PDF")


def test_the_buttons_and_what_they_copy():
    buttons = buttons_of(default_buttons())
    assert [b.label for b in buttons] == ["Копировать имя папки", "PANCH", "PANCH + ЗАГИБ", "ОБЖИГ"]
    assert [b.text("Заказ") for b in buttons] == ["Заказ", "Заказ PANCH", "Заказ PANCH + ЗАГИБ", "Заказ ОБЖИГ"]
    assert [b.hotkey for b in buttons] == [f"<ctrl>+<shift>+{i}" for i in range(1, 5)]
    assert Button("x", prefix="2026_", suffix="_A").text("Заказ") == "2026_Заказ_A"
    junk = [{"label": ""}, "PANCH", {"label": " A ", "suffix": None, "enabled": 0}, {"label": "B"}]
    assert buttons_of(junk) == [Button("A", enabled=False), Button("B")]
    assert buttons_of(None) == []
    config = Config().save_bar
    config.buttons = junk
    assert shown_buttons(config) == [Button("B")]


def test_a_button_has_its_own_colour():
    green = Button("ОБЖИГ", suffix=" ОБЖИГ", color="#30A46C")
    assert buttons_of([green.to_dict()]) == [green]
    assert buttons_of([{"label": "A", "color": "#30a46c"}])[0].color == "#30A46C"
    assert buttons_of([{"label": "A", "color": "зелёный"}, {"label": "B"}])[0].color == ""  # hand-edited junk
    assert normal_color(None) == "" and normal_color(" #ffc83d ") == "#FFC83D" and normal_color("#FFF") == ""
    assert color_name("#30a46c") == "Зелёный" and color_name("#123456") == "#123456" and color_name("") == "обычный"
    assert darker("#FFC83D") == "#E0B036"
    # the caption stays readable: dark on yellow and orange, white on blue, green, red…
    on = {name: text_on(value) for name, value in PALETTE}
    assert on["Жёлтый"] == on["Оранжевый"] == "#1B1B1B"
    assert on["Синий"] == on["Зелёный"] == on["Красный"] == on["Фиолетовый"] == "#FFFFFF"


def test_a_button_can_make_a_new_folder():
    from switcher.savebar import COPY, NEW_FOLDER, new_folder_button

    button = new_folder_button()
    assert button.action == NEW_FOLDER and buttons_of([button.to_dict()]) == [button]
    assert button.what() == "создаёт новую папку" and Button("PANCH", suffix=" PANCH").what() == "«папка» PANCH"
    assert buttons_of([{"label": "A", "action": "format C:"}])[0].action == COPY  # hand-edited junk


def test_hotkeys_by_their_windows_key_codes():
    assert hotkey_vk("<ctrl>+<shift>+1") == (frozenset({"ctrl", "shift"}), 0x31)
    assert hotkey_vk("<ctrl>+<alt>+p") == (frozenset({"ctrl", "alt"}), 0x50)
    assert hotkey_vk("<ctrl>+<f5>") == (frozenset({"ctrl"}), 0x74)
    assert hotkey_vk("double_shift") is None and hotkey_vk("") is None and hotkey_vk("<ctrl>+<home>") is None
    same = [Button("a", hotkey="<ctrl>+<shift>+1"), Button("b", hotkey="<ctrl>+<shift>+1"), Button("c", hotkey="")]
    assert hotkey_map(same) == {(frozenset({"ctrl", "shift"}), 0x31): 0}


def test_the_panel_goes_next_to_new_folder_or_above_the_dialog():
    dialog, row = (100, 100, 1500, 900), (108, 160, 1492, 196)
    assert place_bar(dialog, row, 330, (420, 28), "inside") == (330, 164, "inside")
    assert place_bar(dialog, row, 330, (1100, 28), "inside")[2] == "above"  # does not fit in the row
    assert place_bar(dialog, None, None, (420, 28), "inside") == (108, 68, "above")  # the row was not found
    assert place_bar(dialog, row, 330, (420, 28), "above") == (108, 68, "above")
    assert place_bar((100, 10, 1500, 900), row, 330, (420, 28), "above")[1] == 0  # no room above the screen's top
    assert place_bar(dialog, (108, 160, 1492, 176), 330, (420, 28), "inside")[2] == "above"  # a row too low


def test_where_new_folder_ends_is_seen_on_the_screen():
    bg, ink = 0xF5F6F7, 0x1B1B1B
    width, height = 400, 3
    columns = [bg] * width
    # «Упорядочить ▾», «Новая папка», and the view buttons far to the right
    for x in list(range(10, 80)) + list(range(110, 185)) + list(range(390, 400)):
        columns[x] = ink if x % 3 else bg
    pixels = [bg] * width + columns + [bg] * width
    assert ink_end(pixels, width, 1.0) == 184
    assert ink_end([bg] * (width * height), width, 1.0) is None  # not drawn yet
    assert ink_end([], 0, 1.0) is None


def test_where_new_folder_ends_is_taken_once_the_screen_agrees():
    assert settled_ink([150], 160) is None  # one look is not enough
    assert settled_ink([150, 152], 160) == 152
    assert settled_ink([20, 150], 160) is None  # still being drawn
    assert settled_ink([20, 150, 151], 160) == 151
    assert settled_ink([15, 15], 160) is None  # a half-drawn row: «Упор…» only, far less than the captions
    assert settled_ink([None, None], 160) is None


def test_the_settings_keep_the_buttons(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"enabled": True}), encoding="utf-8")  # a config from before the panel
    config = load_config(path)
    assert config.save_bar.enabled and config.save_bar.make_pdf_folder and config.save_bar.place == "inside"
    assert not config.save_bar.tooltips and not config.save_bar.notifications
    assert len(config.save_bar.buttons) == 4
    config.save_bar.buttons = [Button("Мой", suffix=" X", hotkey="<ctrl>+<shift>+9").to_dict()]
    config.save_bar.enabled = False
    config.save(path)
    again = load_config(path)
    assert not again.save_bar.enabled and buttons_of(again.save_bar.buttons) == [Button("Мой", "", " X",
                                                                                       "<ctrl>+<shift>+9")]
