"""Callback data for the admin-group receipt-review buttons (PRODUCT_BLUEPRINT §8.3)."""

from __future__ import annotations

from typing import Literal

from aiogram.filters.callback_data import CallbackData


class ReceiptDecisionCD(CallbackData, prefix="rd"):
    """Admin tapped ✅ Одобрить or ❌ Отклонить on a receipt notification."""

    receipt_id: int
    decision: Literal["approve", "reject"]
