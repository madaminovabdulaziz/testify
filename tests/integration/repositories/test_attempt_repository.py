"""Integration test for ``AttemptRepository``."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.admin_repository import AdminRepository
from app.repositories.attempt_repository import AttemptRepository, AttemptScores
from app.repositories.test_repository import TestRepository
from app.repositories.user_repository import UserRepository
from app.utils.datetime import now_utc


async def test_attempt_repository_happy_path(session: AsyncSession) -> None:
    users = UserRepository(session)
    admins = AdminRepository(session)
    tests = TestRepository(session)
    attempts = AttemptRepository(session)

    admin = await admins.create(telegram_id=900, role="owner", added_by_admin_id=None)
    test = await tests.create_draft(
        title="Sample",
        duration_seconds=3200,
        created_by_admin_id=admin.id,
    )

    alice = await users.create(telegram_id=300, username="alice")
    await users.set_name(alice.id, "Alice")
    await users.mark_approved(alice.id)
    bob = await users.create(telegram_id=301, username="bob")
    await users.set_name(bob.id, "Bob")
    await users.mark_approved(bob.id)

    # ---------- create ----------
    expires_at = now_utc() + timedelta(seconds=3200)
    a = await attempts.create(
        user_id=alice.id, test_id=test.id, started_at=now_utc(), expires_at=expires_at
    )
    assert a.id is not None
    assert a.status == "in_progress"
    assert a.current_position == 1

    # ---------- get_by_id / get_by_user_and_test ----------
    by_id = await attempts.get_by_id(a.id)
    assert by_id is not None
    by_user = await attempts.get_by_user_and_test(alice.id, test.id)
    assert by_user is not None and by_user.id == a.id
    assert await attempts.get_by_user_and_test(bob.id, test.id) is None

    # ---------- set_current_position ----------
    await attempts.set_current_position(a.id, 7)
    session.expunge_all()
    moved = await attempts.get_by_id(a.id)
    assert moved is not None and moved.current_position == 7

    # ---------- mark_warning_sent (first call stamps, second is a no-op) ----------
    assert await attempts.mark_warning_sent(a.id, "10min") == 1
    assert await attempts.mark_warning_sent(a.id, "10min") == 0
    session.expunge_all()
    warned = await attempts.get_by_id(a.id)
    assert warned is not None and warned.warning_10min_sent_at is not None

    # ---------- list_in_progress ----------
    in_progress = await attempts.list_in_progress()
    assert [att.id for att in in_progress] == [a.id]

    # ---------- list_expired_in_progress (future cutoff returns it) ----------
    after_expiry = now_utc() + timedelta(seconds=3300)
    expired_ids = await attempts.list_expired_in_progress(after_expiry)
    assert expired_ids == [a.id]
    assert await attempts.list_expired_in_progress(now_utc()) == []

    # ---------- mark_finished (status-guarded; second call is no-op) ----------
    scores = AttemptScores(total=42, rus_tili=30, pedagogik=8, kasbiy=4)
    assert await attempts.mark_finished(a.id, status="submitted", scores=scores) == 1
    assert await attempts.mark_finished(a.id, status="expired", scores=scores) == 0
    session.expunge_all()
    finished = await attempts.get_by_id(a.id)
    assert finished is not None
    assert finished.status == "submitted"
    assert finished.score_total_correct == 42
    assert finished.finished_at is not None

    # ---------- list_top_for_test ----------
    # Bob takes the same test and outscores Alice.
    b = await attempts.create(
        user_id=bob.id, test_id=test.id, started_at=now_utc(), expires_at=expires_at
    )
    await attempts.mark_finished(
        b.id,
        status="submitted",
        scores=AttemptScores(total=48, rus_tili=33, pedagogik=10, kasbiy=5),
    )
    leaderboard = await attempts.list_top_for_test(test.id, limit=10)
    assert [entry.user_id for entry in leaderboard] == [bob.id, alice.id]
    assert leaderboard[0].score_total_correct == 48
    assert leaderboard[0].full_name == "Bob"
