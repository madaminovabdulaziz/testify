"""aiogram keyboard builders grouped by domain."""

from app.bot.keyboards.admin import receipt_review_keyboard
from app.bot.keyboards.admin_panel import admin_cancel_keyboard, admin_panel_keyboard
from app.bot.keyboards.common import publish_buttons_keyboard
from app.bot.keyboards.main_menu import main_menu_keyboard
from app.bot.keyboards.onboarding import (
    START_ONBOARDING_CALLBACK,
    contact_request_keyboard,
    welcome_keyboard,
)
from app.bot.keyboards.payment import I_PAID_CALLBACK, payment_buttons_keyboard

__all__ = [
    "I_PAID_CALLBACK",
    "START_ONBOARDING_CALLBACK",
    "admin_cancel_keyboard",
    "admin_panel_keyboard",
    "contact_request_keyboard",
    "main_menu_keyboard",
    "payment_buttons_keyboard",
    "publish_buttons_keyboard",
    "receipt_review_keyboard",
    "welcome_keyboard",
]
