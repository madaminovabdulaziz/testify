"""DI container — the single object handlers reach into for everything else.

ARCHITECTURE_SPEC §7. ``Container`` holds the long-lived process-wide
handles (settings, engine, redis, scheduler, bot). ``Services`` is the
per-request bundle returned by :meth:`Container.services`; every
service instance is built on the same ``AsyncSession`` so they share
the request's transaction.
"""

from __future__ import annotations

from dataclasses import dataclass

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.repositories.admin_repository import AdminRepository
from app.repositories.answer_repository import AnswerRepository
from app.repositories.attempt_repository import AttemptRepository
from app.repositories.broadcast_repository import BroadcastRepository
from app.repositories.question_repository import QuestionRepository
from app.repositories.receipt_repository import ReceiptRepository
from app.repositories.settings_repository import SettingsRepository
from app.repositories.test_repository import TestRepository
from app.repositories.user_repository import UserRepository
from app.services.admin_service import AdminService
from app.services.attempt_service import AttemptService
from app.services.broadcast_service import BroadcastService
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
class Services:
    """Per-request bundle of every service, all sharing one ``AsyncSession``."""

    user: UserService
    receipt: ReceiptService
    test: TestService
    attempt: AttemptService
    settings: SettingsService
    notification: NotificationService
    excel_parser: ExcelParser
    ref_code: ReferenceCodeService
    admin: AdminService
    stats: StatsService
    broadcast: BroadcastService


@dataclass(frozen=True)
class Container:
    """Process-wide infrastructure handles."""

    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    redis: Redis
    scheduler: AsyncIOScheduler
    bot: Bot

    def services(self, session: AsyncSession) -> Services:
        """Instantiate every service against the given request session.

        Services are cheap to construct — one ``UserRepository`` and so on
        per service — so we rebuild the bundle on every handler invocation
        rather than caching. No state leakage across users that way.
        """
        user_repo = UserRepository(session)
        question_repo = QuestionRepository(session)
        settings_service = SettingsService(SettingsRepository(session), self.redis)

        user_service = UserService(user_repo)
        receipt_service = ReceiptService(
            ReceiptRepository(session),
            user_repo,
            ImageHasher(),
            settings_service,
            user_service,
            max_pending_per_user=self.settings.receipt_max_pending_per_user,
        )
        test_service = TestService(
            TestRepository(session),
            question_repo,
            ExcelParser(),
            default_duration_seconds=self.settings.test_duration_seconds,
        )
        attempt_service = AttemptService(
            AttemptRepository(session),
            AnswerRepository(session),
            question_repo,
            ScoringService(),
            self.scheduler,
        )

        return Services(
            user=user_service,
            receipt=receipt_service,
            test=test_service,
            attempt=attempt_service,
            settings=settings_service,
            notification=NotificationService(
                self.bot,
                user_repo,
                admin_group_id=self.settings.admin_group_id,
                broadcast_concurrency=self.settings.broadcast_concurrency,
                broadcast_rate_per_second=self.settings.broadcast_messages_per_second,
            ),
            excel_parser=ExcelParser(),
            ref_code=ReferenceCodeService(user_repo),
            admin=AdminService(AdminRepository(session)),
            stats=StatsService(user_service, receipt_service, test_service, attempt_service),
            broadcast=BroadcastService(BroadcastRepository(session), user_repo),
        )
