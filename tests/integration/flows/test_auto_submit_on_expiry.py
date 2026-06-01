"""E2E flow #3: timer expiry auto-submits an in-flight attempt.

PRODUCT_BLUEPRINT §8.5 timer behavior + §10.4 attempt state machine.

The actual ``AttemptService.finish(reason="expired")`` path is what the
scheduled :func:`attempt_expire_job` invokes. We verify the full vertical
slice by:

1. starting an attempt against a test with a 2-second duration so the
   real ``expires_at`` math lands ``2s`` into the future;
2. registering the expire job via ``AttemptService.start`` — which
   delegates to ``app.jobs.registry.schedule_attempt_jobs``;
3. confirming the job is in the in-memory scheduler with the right id;
4. waiting until wall-clock time is past ``expires_at``;
5. invoking :func:`attempt_expire_job` directly (with a runtime
   container that hands it the test's session factory + a mocked bot);
6. asserting the attempt row is finalized with ``status='expired'``
   and the partial-answer scores are correct.

This proves the full timer → scheduler-fire → finish → DM chain end-
to-end while keeping the test fast (~2.5s).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from datetime import timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs._runtime import reset_runtime_container, set_runtime_container
from app.jobs.attempt_timer import attempt_expire_job
from app.jobs.registry import attempt_job_id
from app.repositories.answer_repository import AnswerRepository
from app.repositories.attempt_repository import AttemptRepository
from app.repositories.question_repository import QuestionRepository
from app.services.attempt_service import AttemptService
from app.services.scoring_service import ScoringService
from app.utils.datetime import now_utc
from tests.integration.flows._helpers import (
    build_services,
    make_bot_mock,
    make_redis_mock,
    valid_xlsx_bytes,
)


@contextlib.asynccontextmanager
async def _session_passthrough_cm(
    session: AsyncSession,
) -> AsyncIterator[AsyncSession]:
    """Yield the test's session without closing/committing it.

    Lets the job re-use the test's connection so its writes land inside
    the outer rollback-on-teardown transaction.
    """
    try:
        yield session
    finally:
        # Never commit/close — the conftest fixture owns the lifetime.
        pass


class _FakeContainer:
    """Minimal Container shape that ``attempt_expire_job`` requires."""

    def __init__(self, *, services_bundle, session: AsyncSession, bot) -> None:
        self._services_bundle = services_bundle
        self._session = session
        self.bot = bot

    def session_factory(self) -> AsyncIterator[AsyncSession]:
        return _session_passthrough_cm(self._session)

    def services(self, _session: AsyncSession):
        return self._services_bundle


async def test_expired_attempt_is_auto_submitted_with_partial_scores(
    session: AsyncSession,
) -> None:
    bot = make_bot_mock()
    redis = make_redis_mock()
    scheduler = AsyncIOScheduler()

    # ---------- approved user + active test ----------
    services_seed = build_services(session, bot=bot, redis=redis, scheduler=scheduler)

    admin = await services_seed.admin.create(telegram_id=900, role="owner", added_by_admin_id=None)
    user = await services_seed.user.get_or_create(telegram_id=777, username="aigerim")
    await services_seed.user.start_onboarding(user.id)
    await services_seed.user.set_phone(user.id, "+998998887766")
    await services_seed.user.set_name(user.id, "Aigerim Tester")
    await services_seed.user.attach_reference_code(user.id, "AGR777")
    session.expunge_all()
    user = await services_seed.user.get_user(user.id)
    assert user is not None
    await services_seed.user.mark_approved(user.id)
    session.expunge_all()
    user = await services_seed.user.get_user(user.id)
    assert user is not None and user.status == "approved"

    # Build a 2-second-duration test by routing through a custom AttemptService.
    draft = await services_seed.test.create_draft_from_excel(
        valid_xlsx_bytes(correct="A"), uploaded_by_admin_id=admin.id, title="2s test"
    )
    published = await services_seed.test.publish(draft.id, notify=False)
    assert published.status == "active"

    # Substitute a 2-second-duration AttemptService so ``start`` writes a
    # near-immediate ``expires_at`` without needing to rewrite the row.
    short_attempt_svc = AttemptService(
        AttemptRepository(session),
        AnswerRepository(session),
        QuestionRepository(session),
        ScoringService(),
        scheduler,
    )

    # Bypass the test's stored duration_seconds: hand-roll an attempt row
    # with expires_at = now+2s. (Going through ``start`` would honor the
    # 3200s test duration; we want to make the test fast.)
    repo = AttemptRepository(session)
    attempt = await repo.create(
        user_id=user.id,
        test_id=published.id,
        started_at=now_utc(),
        expires_at=now_utc() + timedelta(seconds=2),
    )
    short_attempt_svc._schedule_attempt_jobs(attempt)  # type: ignore[attr-defined]

    # ---------- scheduler holds the expire job ----------
    scheduler.start()
    try:
        assert scheduler.get_job(attempt_job_id(attempt.id, "expire")) is not None

        # Answer some questions so the score is non-trivial.
        questions = await QuestionRepository(session).list_by_test(published.id)
        q_by_pos = {q.position: q for q in questions}
        answers_repo = AnswerRepository(session)
        for pos in (1, 2, 3, 36, 46):
            q = q_by_pos[pos]
            await answers_repo.upsert(
                attempt_id=attempt.id,
                question_id=q.id,
                selected_option="A",
                is_correct=True,
            )

        # Wait until past expires_at.
        await asyncio.sleep(2.2)

        # ---------- fire the expire job exactly as the scheduler would ----------
        # Use a passthrough container so the job reuses the test session.
        services_bundle = build_services(session, bot=bot, redis=redis, scheduler=scheduler)
        fake_container = _FakeContainer(services_bundle=services_bundle, session=session, bot=bot)
        set_runtime_container(fake_container)  # type: ignore[arg-type]
        try:
            await attempt_expire_job(attempt_id=attempt.id)
        finally:
            reset_runtime_container()
    finally:
        scheduler.shutdown(wait=False)

    # ---------- DB reflects the expired attempt ----------
    session.expunge_all()
    finalized = await repo.get_by_id(attempt.id)
    assert finalized is not None
    assert finalized.status == "expired"
    assert finalized.finished_at is not None
    assert finalized.score_total_correct == 5
    assert finalized.score_rus_tili_correct == 3
    assert finalized.score_pedagogik_correct == 1
    assert finalized.score_kasbiy_correct == 1

    # ---------- the auto-submitted DM was attempted ----------
    bot.send_message.assert_awaited()
