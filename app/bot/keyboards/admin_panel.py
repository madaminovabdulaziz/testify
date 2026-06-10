"""Persistent reply keyboard for the teacher's admin panel (/admin).

Layout — 3×3 grid of action buttons, a single-button row, + a
full-width "close" row:

    ┌──────────────┬──────────────────┬────────────────┐
    │ 📊 Статистика │ 🗂 Тесты          │ ⚙️ Настройки    │
    ├──────────────┼──────────────────┼────────────────┤
    │ 📋 Загрузить  │ 🏆 Лидерборд      │ 🔎 Попытка      │
    ├──────────────┼──────────────────┼────────────────┤
    │ 🔍 Найти      │ 🚫 Забанить       │ ✅ Разбанить    │
    ├──────────────┴──────────────────┴────────────────┤
    │                  📤 Шаблон Excel                  │
    ├──────────────────────────────────────────────────┤
    │              🔙 Закрыть админ-панель              │
    └──────────────────────────────────────────────────┘

«🗂 Тесты» lists recent tests with their ids so the teacher can read
off a ``test_id`` for 🏆 Лидерборд, then an ``attempt_id`` from the
board for 🔎 Детали попытки — closing the id-discovery chain.

Plus a single-button "cancel" keyboard shown during multi-step flows
(when the bot is waiting for the admin to enter an id / query). This
prevents accidental cross-button taps from being interpreted as input.
"""

from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

from app.core.i18n import (
    BTN_ADMIN_ATTEMPT,
    BTN_ADMIN_BAN,
    BTN_ADMIN_BROADCAST,
    BTN_ADMIN_CANCEL,
    BTN_ADMIN_CLOSE,
    BTN_ADMIN_FIND,
    BTN_ADMIN_LEADERBOARD,
    BTN_ADMIN_SETTINGS,
    BTN_ADMIN_STATS,
    BTN_ADMIN_TEMPLATE,
    BTN_ADMIN_TESTS,
    BTN_ADMIN_UNBAN,
    BTN_ADMIN_UPLOAD_TEST,
)


def admin_panel_keyboard() -> ReplyKeyboardMarkup:
    """3×3 admin grid + a full-width template row + a close-panel row."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=BTN_ADMIN_STATS),
                KeyboardButton(text=BTN_ADMIN_TESTS),
                KeyboardButton(text=BTN_ADMIN_SETTINGS),
            ],
            [
                KeyboardButton(text=BTN_ADMIN_UPLOAD_TEST),
                KeyboardButton(text=BTN_ADMIN_LEADERBOARD),
                KeyboardButton(text=BTN_ADMIN_ATTEMPT),
            ],
            [
                KeyboardButton(text=BTN_ADMIN_FIND),
                KeyboardButton(text=BTN_ADMIN_BAN),
                KeyboardButton(text=BTN_ADMIN_UNBAN),
            ],
            [
                KeyboardButton(text=BTN_ADMIN_BROADCAST),
                KeyboardButton(text=BTN_ADMIN_TEMPLATE),
            ],
            [KeyboardButton(text=BTN_ADMIN_CLOSE)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def admin_cancel_keyboard() -> ReplyKeyboardMarkup:
    """Single-button keyboard shown while we're waiting for the admin's input."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_ADMIN_CANCEL)]],
        resize_keyboard=True,
        is_persistent=True,
    )
