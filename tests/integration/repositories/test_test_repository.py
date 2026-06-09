"""Integration test for ``TestRepository``."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.admin_repository import AdminRepository
from app.repositories.attempt_repository import AttemptRepository, AttemptScores
from app.repositories.question_repository import QuestionDraft, QuestionRepository
from app.repositories.test_repository import TestRepository
from app.repositories.user_repository import UserRepository
from app.utils.datetime import now_utc


async def test_test_repository_happy_path(session: AsyncSession) -> None:
    admins = AdminRepository(session)
    tests = TestRepository(session)

    admin = await admins.create(telegram_id=900, role="owner", added_by_admin_id=None)

    # ---------- nothing active yet ----------
    assert await tests.get_active() is None

    # ---------- create_draft ----------
    draft1 = await tests.create_draft(
        title="Тест от 2026-05-21",
        duration_seconds=3200,
        created_by_admin_id=admin.id,
    )
    assert draft1.status == "draft"
    assert draft1.duration_seconds == 3200

    # ---------- get_by_id ----------
    fetched = await tests.get_by_id(draft1.id)
    assert fetched is not None and fetched.id == draft1.id
    assert await tests.get_by_id(9999) is None

    # ---------- mark_active ----------
    rowcount = await tests.mark_active(draft1.id)
    assert rowcount == 1
    # second call is a no-op because draft1 is no longer 'draft'
    assert await tests.mark_active(draft1.id) == 0
    session.expunge_all()
    active = await tests.get_active()
    assert active is not None and active.id == draft1.id
    assert active.published_at is not None

    # ---------- mark_archived ----------
    assert await tests.mark_archived(draft1.id) == 1
    session.expunge_all()
    assert await tests.get_active() is None
    archived = await tests.get_by_id(draft1.id)
    assert archived is not None
    assert archived.status == "archived"
    assert archived.archived_at is not None

    # ---------- delete_draft ----------
    draft2 = await tests.create_draft(
        title="Throwaway draft",
        duration_seconds=3200,
        created_by_admin_id=admin.id,
    )
    rowcount = await tests.delete_draft(draft2.id)
    assert rowcount == 1
    assert await tests.get_by_id(draft2.id) is None
    # Already-archived row cannot be deleted via this method.
    assert await tests.delete_draft(draft1.id) == 0


async def test_list_recent_returns_ids_with_counts(session: AsyncSession) -> None:
    admins = AdminRepository(session)
    tests = TestRepository(session)
    questions = QuestionRepository(session)
    users = UserRepository(session)
    attempts = AttemptRepository(session)

    admin = await admins.create(telegram_id=900, role="owner", added_by_admin_id=None)

    # Two tests; the second is newest, so it must sort first.
    older = await tests.create_draft(
        title="Старый тест", duration_seconds=3200, created_by_admin_id=admin.id
    )
    newer = await tests.create_draft(
        title="Новый тест", duration_seconds=3200, created_by_admin_id=admin.id
    )

    # Three questions on the newer test (positions are section-constrained).
    await questions.bulk_create(
        newer.id,
        [
            QuestionDraft(
                section="rus_tili",
                position=p,
                question_text=f"Q{p}",
                option_a="a",
                option_b="b",
                option_c="c",
                option_d="d",
                correct_option="A",
            )
            for p in (1, 2, 3)
        ],
    )

    # One finished + one in_progress attempt on the newer test. Only the
    # finished one must be counted (it matches the leaderboard's filter).
    alice = await users.create(telegram_id=300, username="alice")
    await users.mark_approved(alice.id)
    bob = await users.create(telegram_id=301, username="bob")
    await users.mark_approved(bob.id)

    expires_at = now_utc() + timedelta(seconds=3200)
    finished = await attempts.create(
        user_id=alice.id, test_id=newer.id, started_at=now_utc(), expires_at=expires_at
    )
    await attempts.mark_finished(
        finished.id,
        status="submitted",
        scores=AttemptScores(total=40, rus_tili=30, pedagogik=7, kasbiy=3),
    )
    await attempts.create(  # in_progress — must NOT be counted
        user_id=bob.id, test_id=newer.id, started_at=now_utc(), expires_at=expires_at
    )

    entries = await tests.list_recent(limit=15)

    # Newest first.
    assert [e.id for e in entries] == [newer.id, older.id]

    by_id = {e.id: e for e in entries}
    assert by_id[newer.id].question_count == 3
    assert by_id[newer.id].attempt_count == 1  # finished only
    assert by_id[newer.id].status == "draft"
    assert by_id[older.id].question_count == 0
    assert by_id[older.id].attempt_count == 0

    # limit is honoured.
    assert len(await tests.list_recent(limit=1)) == 1
