"""CallbackData for the announcement confirm step."""

from __future__ import annotations

from typing import Literal

from aiogram.filters.callback_data import CallbackData


class BroadcastConfirmCD(CallbackData, prefix="bcast"):
    """«✅ Отправить» / «🗑 Отменить» under the announcement preview."""

    action: Literal["send", "cancel"]
