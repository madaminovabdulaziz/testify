"""aiogram StatesGroup declarations for every multi-step flow."""

from app.bot.states.admin import (
    AdminPanelState,
    AdminRejectReasonState,
    AdminTestUploadState,
)
from app.bot.states.onboarding import OnboardingState
from app.bot.states.payment import PaymentState
from app.bot.states.test_taking import TestState

__all__ = [
    "AdminPanelState",
    "AdminRejectReasonState",
    "AdminTestUploadState",
    "OnboardingState",
    "PaymentState",
    "TestState",
]
