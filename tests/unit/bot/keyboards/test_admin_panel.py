"""Smoke test for the /admin reply keyboards' structure."""

from __future__ import annotations

from app.bot.keyboards.admin_panel import admin_cancel_keyboard, admin_panel_keyboard
from app.core.i18n import (
    BTN_ADMIN_ATTEMPT,
    BTN_ADMIN_BAN,
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

_EXPECTED_ACTION_BUTTONS = {
    BTN_ADMIN_STATS,
    BTN_ADMIN_TESTS,
    BTN_ADMIN_UPLOAD_TEST,
    BTN_ADMIN_SETTINGS,
    BTN_ADMIN_FIND,
    BTN_ADMIN_LEADERBOARD,
    BTN_ADMIN_ATTEMPT,
    BTN_ADMIN_BAN,
    BTN_ADMIN_UNBAN,
    BTN_ADMIN_TEMPLATE,
}


def test_admin_panel_layout_is_three_by_three_plus_template_and_close_rows() -> None:
    kb = admin_panel_keyboard()
    rows = kb.keyboard
    # 3×3 action grid, a full-width template row, then the close row.
    assert len(rows) == 5
    assert [len(row) for row in rows] == [3, 3, 3, 1, 1]


def test_admin_panel_includes_every_action_button_plus_close() -> None:
    kb = admin_panel_keyboard()
    labels = {btn.text for row in kb.keyboard for btn in row}
    assert _EXPECTED_ACTION_BUTTONS.issubset(labels)
    assert BTN_ADMIN_CLOSE in labels


def test_admin_panel_is_persistent_and_resized() -> None:
    kb = admin_panel_keyboard()
    assert kb.is_persistent is True
    assert kb.resize_keyboard is True


def test_cancel_keyboard_is_single_button() -> None:
    kb = admin_cancel_keyboard()
    assert len(kb.keyboard) == 1
    assert len(kb.keyboard[0]) == 1
    assert kb.keyboard[0][0].text == BTN_ADMIN_CANCEL
