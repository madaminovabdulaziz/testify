"""Inline keyboard for the payment-instructions screen."""

from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.core.i18n import BTN_HAVE_QUESTION, BTN_I_PAID

# Plain-string callback_data for the "Я оплатил" button.
I_PAID_CALLBACK = "payment:i_paid"


def payment_buttons_keyboard(support_contact: str | None) -> InlineKeyboardMarkup:
    """Two-button keyboard: «Я оплатил» (callback) + «У меня вопрос» (URL, optional)."""
    builder = InlineKeyboardBuilder()
    builder.button(text=BTN_I_PAID, callback_data=I_PAID_CALLBACK)
    if support_contact:
        # ``support_contact`` is expected to be a Telegram username like
        # "@dilnoza" or a bare "dilnoza"; either way we build a t.me link.
        handle = support_contact.lstrip("@")
        builder.button(text=BTN_HAVE_QUESTION, url=f"https://t.me/{handle}")
    builder.adjust(1)
    return builder.as_markup()
