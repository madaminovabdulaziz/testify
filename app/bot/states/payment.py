"""FSM states for the payment-receipt flow (PRODUCT_BLUEPRINT §8.2)."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class PaymentState(StatesGroup):
    """The user tapped «Я оплатил» and we're waiting for the receipt photo."""

    waiting_for_receipt = State()
