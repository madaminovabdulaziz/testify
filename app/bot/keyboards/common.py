"""Inline keyboards used by the admin test-publish flow."""

from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks.publish import PublishCD
from app.core.i18n import BTN_PUBLISH_CANCEL, BTN_PUBLISH_NOTIFY, BTN_PUBLISH_SILENT


def publish_buttons_keyboard(draft_id: int) -> InlineKeyboardMarkup:
    """Three-row vertical keyboard: publish-notify, publish-silent, cancel."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text=BTN_PUBLISH_NOTIFY,
        callback_data=PublishCD(draft_id=draft_id, action="publish_notify"),
    )
    builder.button(
        text=BTN_PUBLISH_SILENT,
        callback_data=PublishCD(draft_id=draft_id, action="publish_silent"),
    )
    builder.button(
        text=BTN_PUBLISH_CANCEL,
        callback_data=PublishCD(draft_id=draft_id, action="cancel"),
    )
    builder.adjust(1)
    return builder.as_markup()


def cancel_upload_keyboard(draft_id: int) -> InlineKeyboardMarkup:
    """Single «🗑 Отменить» button — abort an in-progress upload (e.g. mid image collection).

    Reuses the publish-cancel callback so the existing handler hard-deletes the
    draft and clears FSM state, regardless of which upload sub-step we're in.
    """
    builder = InlineKeyboardBuilder()
    builder.button(
        text=BTN_PUBLISH_CANCEL,
        callback_data=PublishCD(draft_id=draft_id, action="cancel"),
    )
    builder.adjust(1)
    return builder.as_markup()
