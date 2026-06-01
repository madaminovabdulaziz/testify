"""Integration test for :class:`AttemptService` against real MySQL.

Covers the two prompt-mandated scenarios:

* ``start()`` creates the row with ``expires_at == started_at + duration``
* ``finish()`` is idempotent — calling it twice doesn't change the row
  the second time.

Uses an APScheduler with the default in-memory jobstore so no Redis is
needed for the timer-registration side effect.
"""

from __future__ import annotations

from datetime import timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.admin_repository import AdminRepository
from app.repositories.answer_repository import AnswerRepository
from app.repositories.attempt_repository import AttemptRepository
from app.repositories.question_repository import QuestionDraft, QuestionRepository
from app.repositories.test_repository import TestRepository
from app.repositories.user_repository import UserRepository
from app.services.attempt_service import AttemptService
from app.services.scoring_service import ScoringService


def _drafts(*, correct: str = "A") -> list[QuestionDraft]:
    out: list[QuestionDraft] = []
    for section, lo, hi in (
        ("rus_tili", 1, 35),
        ("pedagogik", 36, 45),
        ("kasbiy", 46, 50),
    ):
        for pos in range(lo, hi + 1):
            out.append(
                QuestionDraft(
                    section=section,
                    position=pos,
                    question_text=f"Q{pos}",
                    option_a="A",
                    option_b="B",
                    option_c="C",
                    option_d="D",
                    correct_option=correct,
                )
            )
    return out


async def _bootstrap(session: AsyncSession) -> tuple[object, object, AttemptService]:
    users = UserRepository(session)
    admins = AdminRepository(session)
    tests = TestRepository(session)
    questions = QuestionRepository(session)
    attempts = AttemptRepository(session)
    answers = AnswerRepository(session)
    scheduler = AsyncIOScheduler()  # in-memory jobstore; no Redis required
    svc = AttemptService(attempts, answers, questions, ScoringService(), scheduler)

    admin = await admins.create(telegram_id=900, role="owner", added_by_admin_id=None)
    test = await tests.create_draft(
        title="Sample",
        duration_seconds=3200,
        created_by_admin_id=admin.id,
    )
    await questions.bulk_create(test.id, _drafts())

    user = await users.create(telegram_id=700, username="alice")
    await users.set_name(user.id, "Alice")
    await users.mark_approved(user.id)
    session.expunge_all()
    user_fresh = await users.get_by_id(user.id)
    assert user_fresh is not None
    return user_fresh, test, svc


async def test_start_creates_attempt_with_correct_expires_at(
    session: AsyncSession,
) -> None:
    user, test, svc = await _bootstrap(session)

    attempt = await svc.start(user, test)

    assert attempt.status == "in_progress"
    assert attempt.current_position == 1
    # PRODUCT_BLUEPRINT §9.10: 3200s timer. ``expires_at - started_at``
    # equals exactly the test's configured ``duration_seconds``.
    delta = attempt.expires_at - attempt.started_at
    assert delta == timedelta(seconds=test.duration_seconds)
    assert delta.total_seconds() == 3200


async def test_finish_is_idempotent(session: AsyncSession) -> None:
    user, test, svc = await _bootstrap(session)
    attempts_repo = AttemptRepository(session)
    answers = AnswerRepository(session)
    questions_repo = QuestionRepository(session)

    attempt = await svc.start(user, test)

    # Answer a couple of questions so the score is non-trivial.
    qs = await questions_repo.list_by_test(test.id)
    q_by_pos = {q.position: q for q in qs}
    for pos in (1, 2, 36, 46):
        await answers.upsert(
            attempt_id=attempt.id,
            question_id=q_by_pos[pos].id,
            selected_option="A",
            is_correct=True,
        )

    # ---------- first finish ----------
    result1 = await svc.finish(attempt.id, reason="user")
    assert result1.scores.total == 4
    assert result1.attempt.status == "submitted"
    first_finished_at = result1.attempt.finished_at
    assert first_finished_at is not None

    # ---------- second finish (idempotent) ----------
    result2 = await svc.finish(attempt.id, reason="user")

    # Re-read the row directly to confirm nothing was rewritten.
    session.expunge_all()
    refreshed = await attempts_repo.get_by_id(attempt.id)
    assert refreshed is not None
    assert refreshed.status == "submitted"
    assert refreshed.finished_at == first_finished_at
    assert refreshed.score_total_correct == 4

    # Second-call result mirrors the first.
    assert result2.scores.total == 4
    assert result2.attempt.finished_at == first_finished_at


async def test_finish_after_expired_does_not_flip_status_back(
    session: AsyncSession,
) -> None:
    """Once an attempt is 'expired', a user-tap finish call must not change it to 'submitted'."""
    user, test, svc = await _bootstrap(session)
    attempts_repo = AttemptRepository(session)

    attempt = await svc.start(user, test)

    # Simulate the timer firing first.
    await svc.finish(attempt.id, reason="expired")

    # Now the (slow) user-tap arrives. Should be a no-op for status.
    result = await svc.finish(attempt.id, reason="user")
    assert result.attempt.status == "expired"

    session.expunge_all()
    final = await attempts_repo.get_by_id(attempt.id)
    assert final is not None and final.status == "expired"


async def test_claim_warning_slot_is_atomic_and_idempotent(
    session: AsyncSession,
) -> None:
    """The slot claim's status-guarded UPDATE owns the dispatch on row==1, no-ops on row==0."""
    user, test, svc = await _bootstrap(session)

    attempt = await svc.start(user, test)

    # First caller wins the dispatch.
    first = await svc.claim_warning_slot(attempt.id, "10min")
    assert first is not None
    assert first.warning_10min_sent_at is not None

    # Second caller sees the stamp and gets None back.
    second = await svc.claim_warning_slot(attempt.id, "10min")
    assert second is None

    # Different slot is still up for grabs.
    other_slot = await svc.claim_warning_slot(attempt.id, "5min")
    assert other_slot is not None
    assert other_slot.warning_5min_sent_at is not None


async def test_claim_warning_slot_returns_none_for_finished_attempt(
    session: AsyncSession,
) -> None:
    """Don't fire a warning after the attempt is already over."""
    user, test, svc = await _bootstrap(session)
    attempt = await svc.start(user, test)
    await svc.finish(attempt.id, reason="user")

    claimed = await svc.claim_warning_slot(attempt.id, "1min")
    assert claimed is None


async def test_list_in_progress_returns_only_active_attempts(
    session: AsyncSession,
) -> None:
    """Reconciliation feed — finished attempts should not show up."""
    user, test, svc = await _bootstrap(session)

    attempt = await svc.start(user, test)
    listed_before = await svc.list_in_progress()
    assert [a.id for a in listed_before] == [attempt.id]

    await svc.finish(attempt.id, reason="user")
    listed_after = await svc.list_in_progress()
    assert listed_after == []
