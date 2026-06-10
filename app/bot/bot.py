"""Bot and Dispatcher factories (ARCHITECTURE_SPEC §6.1)."""

from __future__ import annotations

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import DefaultKeyBuilder, RedisStorage
from redis.asyncio import Redis

from app.bot.filters.admin_only import AdminOnly
from app.bot.handlers import (
    admin_operations,
    admin_panel,
    admin_receipts,
    admin_settings,
    admin_tests,
    admin_weblogin,
    common,
    onboarding,
    payment,
    test_taking,
)
from app.bot.middlewares import (
    DbSessionMiddleware,
    LoggingMiddleware,
    ThrottleMiddleware,
    UserLoaderMiddleware,
    global_error_handler,
)
from app.core.config import Settings
from app.core.container import Container


def build_bot(settings: Settings) -> Bot:
    """Construct the aiogram Bot with HTML parse-mode defaults."""
    return Bot(
        token=settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def build_dispatcher(redis: Redis, container: Container) -> Dispatcher:
    """Construct the Dispatcher with Redis FSM, middlewares, and the handler routers.

    Middleware order matters and matches ARCHITECTURE_SPEC §6.3:
    Logging → DbSession → UserLoader → Throttle. Registration order =
    execution order (first registered runs outermost).

    Router order matters too: the common router is included **last** so
    its catch-all message handler only fires for updates no other router
    claimed.
    """
    storage = RedisStorage(redis, key_builder=DefaultKeyBuilder(with_destiny=True))
    dispatcher = Dispatcher(storage=storage)

    # Container available as a handler kwarg via aiogram's workflow_data.
    dispatcher["container"] = container

    # ---- middlewares (outer first) ----
    dispatcher.update.middleware(LoggingMiddleware())
    dispatcher.update.middleware(DbSessionMiddleware(container.session_factory))
    dispatcher.update.middleware(UserLoaderMiddleware(redis))
    dispatcher.update.middleware(ThrottleMiddleware(redis, max_per_second=10))

    # ---- routers ----
    # admin_receipts is gated to the configured admin group AND to
    # registered admins (PRODUCT_BLUEPRINT §8.3 / §14.3). The chat filter
    # alone is not enough: anyone the teacher adds to the admin group as an
    # observer could otherwise tap ✅/❌ and bypass the whole `admins`
    # table. AdminOnly() reads the session injected by DbSessionMiddleware,
    # so it composes at router level for both messages and callbacks.
    # admin_tests is NOT chat-gated: /upload_test and /template work in
    # DM from a registered admin too (PRODUCT_BLUEPRINT §14.3).
    admin_group_id = container.settings.admin_group_id
    admin_receipts.router.message.filter(F.chat.id == admin_group_id, AdminOnly())
    admin_receipts.router.callback_query.filter(F.message.chat.id == admin_group_id, AdminOnly())
    dispatcher.include_router(admin_receipts.router)
    dispatcher.include_router(admin_tests.router)
    # admin_operations + admin_settings work in DM AND the admin group.
    # The AdminOnly() router-level filter gates non-admins; no chat
    # filter so the teacher's DM works too (PRODUCT_BLUEPRINT §14.3).
    dispatcher.include_router(admin_operations.router)
    dispatcher.include_router(admin_settings.router)
    dispatcher.include_router(admin_weblogin.router)
    # admin_panel matches on reply-keyboard button text; included after
    # the slash-command admin routers so /set, /find etc. still win when
    # the admin types a command, but before the student / fallback
    # routers so panel buttons aren't swallowed.
    dispatcher.include_router(admin_panel.router)

    # /chatid is a setup diagnostic that must answer in groups/channels
    # (so the operator can read off ADMIN_GROUP_ID); it lives on its own
    # router with no private-chat filter. Included before the student
    # routers so it wins in any chat type.
    dispatcher.include_router(common.chatid_router)
    dispatcher.include_router(onboarding.router)
    dispatcher.include_router(payment.router)
    dispatcher.include_router(test_taking.router)
    dispatcher.include_router(common.router)  # MUST be last (fallback)

    # ---- error handler ----
    dispatcher.errors.register(global_error_handler)

    return dispatcher
