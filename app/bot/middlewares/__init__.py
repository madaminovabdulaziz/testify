"""aiogram middlewares + the global error handler."""

from app.bot.middlewares.db_session import DbSessionMiddleware
from app.bot.middlewares.error_handler import global_error_handler
from app.bot.middlewares.logging import LoggingMiddleware
from app.bot.middlewares.throttle import ThrottleMiddleware
from app.bot.middlewares.user_loader import UserLoaderMiddleware

__all__ = [
    "DbSessionMiddleware",
    "LoggingMiddleware",
    "ThrottleMiddleware",
    "UserLoaderMiddleware",
    "global_error_handler",
]
