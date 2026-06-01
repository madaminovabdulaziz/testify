"""Inline keyboards for admin-group interactions."""

from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks.receipt import ReceiptDecisionCD
from app.core.i18n import BTN_APPROVE, BTN_REJECT


def receipt_review_keyboard(receipt_id: int) -> InlineKeyboardMarkup:
    """✅ Одобрить + ❌ Отклонить inline buttons attached to a receipt post."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text=BTN_APPROVE,
        callback_data=ReceiptDecisionCD(receipt_id=receipt_id, decision="approve"),
    )
    builder.button(
        text=BTN_REJECT,
        callback_data=ReceiptDecisionCD(receipt_id=receipt_id, decision="reject"),
    )
    return builder.as_markup()
