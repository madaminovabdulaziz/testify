"""Callback data for the publish-or-cancel buttons on the draft preview (PRODUCT_BLUEPRINT §8.4)."""

from __future__ import annotations

from typing import Literal

from aiogram.filters.callback_data import CallbackData

PublishAction = Literal["publish_notify", "publish_silent", "cancel"]


class PublishCD(CallbackData, prefix="pub"):
    """Admin's choice on the draft-test preview screen."""

    draft_id: int
    action: PublishAction
