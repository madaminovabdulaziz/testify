"""Shared fixtures + builders for the end-to-end flow tests.

Per ARCHITECTURE_SPEC §17.2 the five critical flows each exercise the
full vertical stack (handler → service → repository → MySQL) but stop
short of going through aiogram itself. Telegram is mocked; the DB is
real (testcontainers).

The helpers here:

* :func:`build_services` — assemble every service from a single session,
  matching what :meth:`Container.services` does in production. The
  scheduler is created but *not started* — jobs don't fire during the
  test; we drive expiry manually via :func:`run_expire_job`.
* :func:`make_bot_mock` — A ``MagicMock`` Bot with ``send_message``,
  ``send_photo``, ``edit_message_caption``, ``download`` all preset.
* :func:`png_bytes` — solid-color PNG bytes for deterministic pHash.
* :func:`valid_xlsx_bytes` — a 50-question .xlsx that passes the parser.
* :func:`run_expire_job` — invokes :func:`attempt_expire_job` against
  the test's session + mocked bot via a temporary runtime container.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from openpyxl import Workbook
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.admin_repository import AdminRepository
from app.repositories.answer_repository import AnswerRepository
from app.repositories.attempt_repository import AttemptRepository
from app.repositories.question_repository import QuestionRepository
from app.repositories.receipt_repository import ReceiptRepository
from app.repositories.settings_repository import SettingsRepository
from app.repositories.test_repository import TestRepository
from app.repositories.user_repository import UserRepository
from app.services.attempt_service import AttemptService
from app.services.excel_parser import ExcelParser
from app.services.image_hasher import ImageHasher
from app.services.notification_service import NotificationService
from app.services.receipt_service import ReceiptService
from app.services.reference_code import ReferenceCodeService
from app.services.scoring_service import ScoringService
from app.services.settings_service import SettingsService
from app.services.stats_service import StatsService
from app.services.test_service import TestService
from app.services.user_service import UserService


@dataclass(frozen=True)
class FlowServices:
    """Per-test bundle, shape-compatible with ``app.core.container.Services``.

    Not the production type (we don't want to drag the Container's Redis
    dep into the test) but exposing the same names so tests can use
    ``services.attempt`` / ``services.receipt`` like in the handlers.
    """

    user: UserService
    receipt: ReceiptService
    test: TestService
    attempt: AttemptService
    settings: SettingsService
    notification: NotificationService
    excel_parser: ExcelParser
    ref_code: ReferenceCodeService
    admin: AdminRepository
    stats: StatsService


def build_services(
    session: AsyncSession,
    *,
    bot: MagicMock,
    redis: MagicMock,
    scheduler: AsyncIOScheduler | None = None,
    admin_group_id: int = -1001,
) -> FlowServices:
    """Construct every service against a single ``AsyncSession``.

    Mirrors :meth:`app.core.container.Container.services`. The scheduler
    is unused unless the test explicitly fires a job — we pass an
    unstarted instance so ``AttemptService._schedule_attempt_jobs``
    can call ``add_job`` (jobstore is in-memory and silently drops the
    DateTrigger when the scheduler isn't running).
    """
    sched = scheduler or AsyncIOScheduler()

    user_repo = UserRepository(session)
    question_repo = QuestionRepository(session)
    settings_service = SettingsService(SettingsRepository(session), redis)
    user_service = UserService(user_repo)
    receipt_service = ReceiptService(
        ReceiptRepository(session),
        user_repo,
        ImageHasher(),
        settings_service,
        user_service,
        max_pending_per_user=3,
    )
    test_service = TestService(
        TestRepository(session),
        question_repo,
        ExcelParser(),
        default_duration_seconds=3200,
    )
    attempt_service = AttemptService(
        AttemptRepository(session),
        AnswerRepository(session),
        question_repo,
        ScoringService(),
        sched,
    )

    return FlowServices(
        user=user_service,
        receipt=receipt_service,
        test=test_service,
        attempt=attempt_service,
        settings=settings_service,
        notification=NotificationService(
            bot,
            user_repo,
            admin_group_id=admin_group_id,
            broadcast_concurrency=5,
            broadcast_rate_per_second=10_000,
        ),
        excel_parser=ExcelParser(),
        ref_code=ReferenceCodeService(user_repo),
        admin=AdminRepository(session),
        stats=StatsService(user_service, receipt_service, test_service, attempt_service),
    )


def make_bot_mock() -> MagicMock:
    """Telegram ``Bot`` mock with the methods our code may call.

    All return ``MagicMock()`` so caller `.message_id` / etc. attribute
    chains don't blow up.
    """
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot.send_photo = AsyncMock(return_value=MagicMock(message_id=2))
    bot.edit_message_caption = AsyncMock(return_value=MagicMock())
    bot.download = AsyncMock()
    return bot


def make_redis_mock() -> MagicMock:
    """Redis mock that behaves as a no-op cache.

    ``get`` always returns ``None`` (cache miss → falls through to DB),
    ``set``/``delete`` are no-ops. Settings reads thus always go to
    MySQL — which is what the E2E test wants to exercise anyway.
    """
    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)
    redis.delete = AsyncMock(return_value=1)
    redis.ping = AsyncMock(return_value=True)
    return redis


def png_bytes(*, color: tuple[int, int, int], size: int = 64) -> bytes:
    """Build a solid-color PNG — same color ⇒ same pHash."""
    img = Image.new("RGB", (size, size), color)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def valid_xlsx_bytes(*, correct: str = "A") -> bytes:
    """A 50-question Excel workbook that passes :class:`ExcelParser`."""
    wb = Workbook()
    sheet = wb.active
    sheet.title = "Questions"
    sheet.append(
        [
            "section",
            "position",
            "question_text",
            "option_a",
            "option_b",
            "option_c",
            "option_d",
            "correct_option",
        ]
    )
    for pos in range(1, 36):
        sheet.append(["rus_tili", pos, f"Q{pos}", "alpha", "beta", "gamma", "delta", correct])
    for pos in range(36, 46):
        sheet.append(["pedagogik", pos, f"Q{pos}", "alpha", "beta", "gamma", "delta", correct])
    for pos in range(46, 51):
        sheet.append(["kasbiy", pos, f"Q{pos}", "alpha", "beta", "gamma", "delta", correct])
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


__all__ = [
    "FlowServices",
    "build_services",
    "make_bot_mock",
    "make_redis_mock",
    "png_bytes",
    "valid_xlsx_bytes",
]
