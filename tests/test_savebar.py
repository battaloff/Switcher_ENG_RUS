"""The «Сохранить как» panel: which folder a dialog shows, what a button copies, where things go."""

import json

from switcher.config import Config, load_config
from switcher.savebar import (
    Button,
    Plan,
    buttons_of,
    default_buttons,
    folder_from_address,
    hotkey_map,
    hotkey_vk,
    place_bar,
    plan,
    shown_buttons,
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
