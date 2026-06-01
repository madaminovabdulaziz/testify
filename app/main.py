"""Application entrypoint.

Builds the long-lived :class:`Container`, the aiogram ``Bot``,
``Dispatcher``, then branches on ``settings.env``:

* ``dev`` → ensure the webhook is cleared and start long-polling
  (ARCHITECTURE_SPEC §20).
* anything else → register the webhook with Telegram and spin up the
  aiohttp app on ``:8080`` (ARCHITECTURE_SPEC §13). nginx is expected
  to terminate TLS in front (§14.3).

Background-job lifecycle (ARCHITECTURE_SPEC §11) is shared between
both modes:

1. Install the runtime container into ``app.jobs._runtime`` so
   scheduler-invoked callables can reach the bot + session factory.
2. Start the scheduler.
3. Reconcile in-flight attempts (re-register their timer jobs +
   finalize any whose ``expires_at`` is already past).
4. Register the hourly pending-receipt reminder sweep.
5. *Then* start accepting Telegram updates.

In both modes we print a literal ``bot started`` line to stdout once
the bot is alive so external smoke tests can grep for it.
"""

from __future__ import annotations

import asyncio
import os
import sys

import structlog
from aiohttp import web

from app.bot.bot import build_bot, build_dispatcher
from app.bot.handlers.admin.tests import wait_for_pending_broadcasts
from app.bot.webhook import make_app
from app.core.config import Settings
from app.core.container import Container
from app.core.database import create_engine_and_session
from app.core.logging import configure_logging
from app.core.redis import create_redis_client
from app.core.scheduler import build_scheduler
from app.core.sentry import init_sentry
from app.jobs._runtime import set_runtime_container
from app.jobs.registry import register_recurring_jobs
from app.jobs.startup_reconciliation import reconcile_attempts

# aiohttp listen address for webhook mode. A reverse proxy in front of us
# terminates TLS and forwards to this port: nginx → bot:8080 in the compose
# setup (ARCHITECTURE_SPEC §14.1), or Railway's edge → $PORT in the managed
# setup. ``PORT`` (when set, e.g. by Railway) overrides the default.
_WEBHOOK_BIND_HOST = "0.0.0.0"
_WEBHOOK_BIND_PORT = int(os.environ.get("PORT", "8080"))

logger = structlog.get_logger()


def build_container(settings: Settings) -> Container:
    """Assemble the infrastructure container. No network I/O happens here."""
    engine, session_factory = create_engine_and_session(settings)
    redis = create_redis_client(settings)
    scheduler = build_scheduler(settings)
    bot = build_bot(settings)
    return Container(
        settings=settings,
        engine=engine,
        session_factory=session_factory,
        redis=redis,
        scheduler=scheduler,
        bot=bot,
    )


def _print_started() -> None:
    """Mirror startup to stdout for the smoke-test grep."""
    sys.stdout.write("bot started\n")
    sys.stdout.flush()


async def _start_jobs(container: Container) -> None:
    """Install the runtime container, start the scheduler, reconcile, register recurring."""
    set_runtime_container(container)
    container.scheduler.start()
    logger.info("scheduler_started")

    # Order matters: reconciliation may re-add the per-attempt timer
    # jobs *before* the recurring jobs are registered, but both are
    # idempotent so the order is informative not load-bearing.
    await reconcile_attempts(container)
    register_recurring_jobs(container.scheduler)


async def _stop_jobs(container: Container) -> None:
    """Tear the scheduler down on shutdown (best-effort)."""
    try:
        container.scheduler.shutdown(wait=False)
    except Exception:
        logger.exception("scheduler_shutdown_failed")


async def _dispose_infra(container: Container) -> None:
    """Release the DB engine pool + Redis connections on shutdown.

    Without this, containerized rolling restarts leak connections against
    MySQL's ``max_connections`` budget until the old process is reaped
    (CODE_REVIEW H12). Best-effort: a failure here must not mask the
    original shutdown reason.
    """
    try:
        await container.engine.dispose()
    except Exception:
        logger.exception("engine_dispose_failed")
    try:
        await container.redis.aclose()
    except Exception:
        logger.exception("redis_close_failed")


async def _check_admin_group(container: Container) -> None:
    """Verify the configured ADMIN_GROUP_ID is reachable; log loudly if not.

    A wrong ID makes the receipt-approval flow fail late (when a student
    submits a receipt and the bot can't post to the admin group). Detect
    it here at startup so the operator sees the problem before users do.
    """
    admin_group_id = container.settings.admin_group_id
    try:
        chat = await container.bot.get_chat(admin_group_id)
    except Exception as exc:
        sys.stdout.write(
            "\n"
            "═══════════════════════════════════════════════════════════════\n"
            f"⚠  ADMIN_GROUP_ID={admin_group_id} is not reachable.\n"
            f"   Reason: {exc}\n"
            "\n"
            "   The bot will run, but receipt-approval messages cannot be\n"
            "   posted to the admin group. To fix:\n"
            "     1. Add this bot to the admin group.\n"
            "     2. In the group, send: /chatid\n"
            "     3. Copy the printed ID into .env (ADMIN_GROUP_ID=…).\n"
            "     4. Ctrl-C and `make dev` again.\n"
            "═══════════════════════════════════════════════════════════════\n"
        )
        sys.stdout.flush()
        logger.error(
            "admin_group_unreachable",
            configured_id=admin_group_id,
            error=str(exc),
        )
        return
    logger.info(
        "admin_group_reachable",
        chat_id=chat.id,
        title=getattr(chat, "title", None),
    )


async def _run_polling(container: Container) -> None:
    dispatcher = build_dispatcher(container.redis, container)
    # In dev we want the webhook cleared so long-polling doesn't get an
    # "conflicted update consumer" error (aiogram raises if both are set).
    await container.bot.delete_webhook(drop_pending_updates=False)
    await _start_jobs(container)
    await _check_admin_group(container)
    logger.info("bot_starting", mode="polling")
    _print_started()
    try:
        await dispatcher.start_polling(container.bot)
    finally:
        await _stop_jobs(container)
        await wait_for_pending_broadcasts()
        await container.bot.session.close()
        await _dispose_infra(container)


async def _run_webhook(container: Container) -> None:
    dispatcher = build_dispatcher(container.redis, container)
    settings = container.settings
    # Guaranteed by Settings._require_webhook_config_outside_dev (this runner
    # only runs when env != 'dev'), asserted here for clarity (CODE_REVIEW L11).
    assert settings.webhook_url is not None
    assert settings.webhook_secret is not None

    # Startup order matters (CODE_REVIEW C5). Telegram must not be allowed
    # to deliver updates until the scheduler is running *and* the HTTP site
    # is listening — otherwise a student tapping "Начать тест" in the
    # opening-second window gets an attempt whose timer jobs were scheduled
    # against a not-yet-started scheduler (and, with no backstop, a stuck
    # attempt). So: (1) jobs, (2) HTTP site, (3) register the webhook last.

    # 1. Scheduler + reconciliation first.
    await _start_jobs(container)

    # 2. Bring the aiohttp site up so /healthz answers and the route exists.
    app = make_app(container, dispatcher)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=_WEBHOOK_BIND_HOST, port=_WEBHOOK_BIND_PORT)
    await site.start()

    # 3. Only now point Telegram at us — this is when updates start flowing.
    await container.bot.set_webhook(
        url=str(settings.webhook_url),
        secret_token=settings.webhook_secret.get_secret_value(),
        allowed_updates=dispatcher.resolve_used_update_types(),
        drop_pending_updates=False,
    )

    logger.info("bot_starting", mode="webhook", port=_WEBHOOK_BIND_PORT)
    _print_started()

    try:
        # Block forever until cancelled (SIGTERM → loop cancel).
        await asyncio.Event().wait()
    finally:
        await _stop_jobs(container)
        await wait_for_pending_broadcasts()
        await container.bot.delete_webhook(drop_pending_updates=False)
        await container.bot.session.close()
        await runner.cleanup()
        await _dispose_infra(container)


async def main() -> None:
    settings = Settings()  # type: ignore[call-arg]  # values come from env / .env
    configure_logging(settings)
    init_sentry(settings)

    container = build_container(settings)

    if settings.env == "dev":
        await _run_polling(container)
    else:
        await _run_webhook(container)


if __name__ == "__main__":
    asyncio.run(main())
