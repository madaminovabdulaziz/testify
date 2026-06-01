"""FSM states for admin-side multi-step flows (PRODUCT_BLUEPRINT §8.3, §8.4)."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class AdminTestUploadState(StatesGroup):
    """Admin upload flow for a new test (/upload_test → file → [images] → publish/cancel)."""

    waiting_for_file = State()
    collecting_images = State()
    confirming_publish = State()


class AdminRejectReasonState(StatesGroup):
    """Admin tapped «❌ Отклонить» — the bot is waiting for a free-text reason."""

    waiting_for_reason = State()


class AdminPanelState(StatesGroup):
    """Admin tapped a multi-step button in the /admin panel.

    Each state corresponds to "the bot is waiting for the admin to type
    one argument" — a query, a user id, a test id, or an attempt id.
    A subsequent text message in that state is consumed as the input,
    OR the admin can tap «↩️ Отменить» to abort and return to the panel.
    """

    waiting_for_find_query = State()
    waiting_for_ban_id = State()
    waiting_for_unban_id = State()
    waiting_for_leaderboard_id = State()
    waiting_for_attempt_id = State()
