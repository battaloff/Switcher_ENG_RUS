"""Telling a "Save As" dialog from other file dialogs by what it says (no Windows calls here)."""

from __future__ import annotations

SAVE_WORDS = ("сохран", "save", "экспорт", "export", "зберег")
# what the dialog's main button says, whatever the program calls the dialog itself
SAVE_BUTTONS = ("сохранить", "save", "экспорт", "export", "зберегти", "saqlash")


def is_save_dialog(title: str, buttons: list[str]) -> bool:
    """A file dialog for saving: by its title, or else by its main button ("&Сохранить", "Save")."""
    if any(word in title.lower() for word in SAVE_WORDS):
        return True
    return any(text.replace("&", "").strip().lower().startswith(SAVE_BUTTONS) for text in buttons)
