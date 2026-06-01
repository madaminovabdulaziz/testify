"""FSM states for the onboarding flow (PRODUCT_BLUEPRINT §8.1, §10.1)."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class OnboardingState(StatesGroup):
    """User is mid-onboarding; the bot is waiting on the next input."""

    waiting_for_phone = State()
    waiting_for_name = State()
