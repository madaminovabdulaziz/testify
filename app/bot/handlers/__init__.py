"""Top-level handler routers. The dispatcher includes them in order."""

from app.bot.handlers import common, onboarding, payment, test_taking
from app.bot.handlers.admin import operations as admin_operations
from app.bot.handlers.admin import panel as admin_panel
from app.bot.handlers.admin import receipts as admin_receipts
from app.bot.handlers.admin import settings as admin_settings
from app.bot.handlers.admin import tests as admin_tests

__all__ = [
    "admin_operations",
    "admin_panel",
    "admin_receipts",
    "admin_settings",
    "admin_tests",
    "common",
    "onboarding",
    "payment",
    "test_taking",
]
