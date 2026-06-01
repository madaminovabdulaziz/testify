"""Persistent reply keyboard shown to approved students.

A reply keyboard is the standard Telegram-bot UX for a "home" screen:
the buttons stay visible at the bottom of every chat, tapping one sends
its label text as a normal message which the bot handlers match on.

Layout (2×2):

    ┌──────────────────┬──────────────────┐
    │ ▶️ Пройти тест    │ 📜 Мои результаты │
    ├──────────────────┼──────────────────┤
    │ 💬 Чат студентов  │ ❓ Помощь         │
    └──────────────────┴──────────────────┘

The keyboard is shown the first time a user reaches the ``approved``
state (in the approval DM) and stays visible for every subsequent
message. ``is_persistent=True`` tells modern Telegram clients to keep
it expanded by default rather than collapsing into the input area.
"""

from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

from app.core.i18n import (
    BTN_MENU_CHAT,
    BTN_MENU_HELP,
    BTN_MENU_HISTORY,
    BTN_MENU_TAKE_TEST,
)


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Build the 4-button persistent reply keyboard for approved students."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=BTN_MENU_TAKE_TEST),
                KeyboardButton(text=BTN_MENU_HISTORY),
            ],
            [
                KeyboardButton(text=BTN_MENU_CHAT),
                KeyboardButton(text=BTN_MENU_HELP),
            ],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )
