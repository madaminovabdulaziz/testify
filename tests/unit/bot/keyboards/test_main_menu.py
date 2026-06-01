"""Smoke test for the main-menu reply keyboard structure."""

from __future__ import annotations

from app.bot.keyboards.main_menu import main_menu_keyboard
from app.core.i18n import (
    BTN_MENU_CHAT,
    BTN_MENU_HELP,
    BTN_MENU_HISTORY,
    BTN_MENU_TAKE_TEST,
)


def test_main_menu_has_four_labelled_buttons_in_two_rows() -> None:
    kb = main_menu_keyboard()
    rows = kb.keyboard
    assert len(rows) == 2
    assert len(rows[0]) == 2
    assert len(rows[1]) == 2
    labels = {btn.text for row in rows for btn in row}
    assert labels == {
        BTN_MENU_TAKE_TEST,
        BTN_MENU_HISTORY,
        BTN_MENU_CHAT,
        BTN_MENU_HELP,
    }


def test_main_menu_is_persistent_and_resized() -> None:
    kb = main_menu_keyboard()
    assert kb.resize_keyboard is True
    assert kb.is_persistent is True
