"""Data access for the ``attempts`` table.

Hot path: ``get_by_id`` + ``set_current_position`` run on every test-screen
button tap. ``mark_finished`` and ``mark_warning_sent`` use status-guarded
UPDATEs so calling them twice is safe (see DATABASE_SPEC §10.9 / §10.15
and ARCHITECTURE_SPEC §11.3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import func, select, update

from app.models.attempt import Attempt
from app.models.test import Test
from app.models.user import User
from app.repositories.base import BaseRepository
from app.utils.datetime import now_utc

WarningSlot = Literal["10min", "5min", "1min"]


@dataclass(frozen=True)
class LeaderboardEntry:
    """One row of the per-test leaderboard returned by :meth:`AttemptRepository.list_top_for_test`."""

    attempt_id: int
    user_id: int
    full_name: str | None
    score_total_correct: int
    finished_at: datetime


@dataclass(frozen=True)
class AttemptScores:
    """Final score breakdown used by :meth:`AttemptRepository.mark_finished`."""

    total: int
    rus_tili: int
    pedagogik: int
    kasbiy: int


class AttemptRepository(BaseRepository):
    """Reads + writes for ``attempts``."""

    async def get_by_id(self, attempt_id: int) -> Attempt | None:
        """Fetch one attempt by id, refreshed from the DB.

        ``populate_existing=True`` forces a reload even when the row is already
        in the session identity map. This is required because callers re-read
        here immediately after a Core ``update()`` (``finish``→``mark_finished``,
        ``claim_warning_slot``→``mark_warning_sent``): that statement's
        ``synchronize_session="evaluate"`` updates string/int columns in place
        but silently leaves the tz-aware datetime columns stale, so without the
        refresh ``finished_at`` / ``warning_*_sent_at`` come back ``None`` even
        though the DB has them. Confirmed against real MySQL.
        """
        return await self._session.get(Attempt, attempt_id, populate_existing=True)

    async def get_by_user_and_test(self, user_id: int, test_id: int) -> Attempt | None:
        """Fetch the user's attempt at a given test (or ``None`` if none yet)."""
        stmt = (
            select(Attempt).where(Attempt.user_id == user_id, Attempt.test_id == test_id).limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_in_progress_for_user(self, user_id: int) -> Attempt | None:
        """Fetch the user's currently-open attempt on **any** test, or ``None``.

        Used so a student who was mid-exam when their test got archived
        (PRODUCT_BLUEPRINT §8.4/§13: "continues to completion on the archived
        test") is routed back to that attempt instead of seeing it vanish or
        starting a second concurrent one (CODE_REVIEW C3). Ordered newest-first
        so legacy data with more than one open attempt resolves deterministically.
        """
        stmt = (
            select(Attempt)
            .where(Attempt.user_id == user_id, Attempt.status == "in_progress")
            .order_by(Attempt.started_at.desc())
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def create(
        self,
        *,
        user_id: int,
        test_id: int,
        started_at: datetime,
        expires_at: datetime,
    ) -> Attempt:
        """Insert a new ``in_progress`` attempt.

        ``started_at`` is passed explicitly (not left to the DB server
        default) so it and ``expires_at`` come from the *same* Python clock —
        otherwise host/MySQL clock drift would make the first render's timer
        wrong by the drift amount (CODE_REVIEW M12).
        """
        attempt = Attempt(
            user_id=user_id,
            test_id=test_id,
            status="in_progress",
            current_position=1,
            started_at=started_at,
            expires_at=expires_at,
        )
        self._session.add(attempt)
        await self._session.flush()
        return attempt

    async def set_current_position(self, attempt_id: int, position: int) -> int:
        """Persist the question the user is currently viewing (DB-backed for resume).

        Guarded with ``status = 'in_progress'`` so a late nav tap can't write
        a cursor onto an already-finalized attempt (which would leave an
        ``expired`` row with a mutated ``current_position`` — CODE_REVIEW M6).
        Returns rowcount.
        """
        stmt = (
            update(Attempt)
            .where(Attempt.id == attempt_id, Attempt.status == "in_progress")
            .values(current_position=position)
        )
        result = await self._session.execute(stmt)
        return result.rowcount

    async def mark_finished(
        self,
        attempt_id: int,
        *,
        status: Literal["submitted", "expired"],
        scores: AttemptScores,
    ) -> int:
        """Finalize an attempt — only succeeds while still ``in_progress``. Returns rowcount."""
        stmt = (
            update(Attempt)
            .where(Attempt.id == attempt_id, Attempt.status == "in_progress")
            .values(
                status=status,
                finished_at=now_utc(),
                score_total_correct=scores.total,
                score_rus_tili_correct=scores.rus_tili,
                score_pedagogik_correct=scores.pedagogik,
                score_kasbiy_correct=scores.kasbiy,
            )
        )
        result = await self._session.execute(stmt)
        return result.rowcount

    async def mark_warning_sent(self, attempt_id: int, slot: WarningSlot) -> int:
        """Atomically claim a warning slot: stamp ``warning_<slot>_sent_at`` iff
        still null **and** the attempt is still ``in_progress``. Returns rowcount.

        The ``status = 'in_progress'`` guard is what makes the claim safe: if
        the attempt was finalized between the scheduler firing and this UPDATE,
        the row no longer matches, so a warning DM is never sent after the
        student has already seen their result (CODE_REVIEW C7).
        """
        column = {
            "10min": Attempt.warning_10min_sent_at,
            "5min": Attempt.warning_5min_sent_at,
            "1min": Attempt.warning_1min_sent_at,
        }[slot]
        stmt = (
            update(Attempt)
            .where(
                Attempt.id == attempt_id,
                Attempt.status == "in_progress",
                column.is_(None),
            )
            .values({column.key: now_utc()})
        )
        result = await self._session.execute(stmt)
        return result.rowcount

    async def list_in_progress(self) -> list[Attempt]:
        """All ``in_progress`` attempts — used to re-schedule jobs at startup."""
        stmt = select(Attempt).where(Attempt.status == "in_progress")
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_in_progress_for_user(self, user_id: int) -> list[Attempt]:
        """Every open attempt belonging to one user — used by the ban cleanup (H20)."""
        stmt = select(Attempt).where(
            Attempt.user_id == user_id,
            Attempt.status == "in_progress",
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_expired_in_progress(self, now: datetime) -> list[int]:
        """Attempt IDs that are still in_progress but past ``expires_at`` — safety-net sweep."""
        stmt = select(Attempt.id).where(
            Attempt.status == "in_progress",
            Attempt.expires_at < now,
        )
        return [int(row[0]) for row in (await self._session.execute(stmt))]

    async def count_by_status(self) -> dict[str, int]:
        """``{status: count}`` across all attempts — feeds /stats."""
        stmt = select(Attempt.status, func.count()).group_by(Attempt.status)
        rows = await self._session.execute(stmt)
        return {status: int(count) for status, count in rows}

    async def list_finished_with_test_for_user(
        self,
        user_id: int,
        *,
        limit: int = 50,
    ) -> list[tuple[Attempt, Test]]:
        """Past finished attempts for one user, newest first, joined with test title."""
        stmt = (
            select(Attempt, Test)
            .join(Test, Test.id == Attempt.test_id)
            .where(
                Attempt.user_id == user_id,
                Attempt.status.in_(("submitted", "expired")),
            )
            .order_by(Attempt.finished_at.desc())
            .limit(limit)
        )
        rows = await self._session.execute(stmt)
        return [(row[0], row[1]) for row in rows]

    async def list_top_for_test(self, test_id: int, limit: int = 20) -> list[LeaderboardEntry]:
        """Top ``limit`` finished attempts for a test, ordered by score then finish time."""
        stmt = (
            select(
                Attempt.id.label("attempt_id"),
                Attempt.user_id.label("user_id"),
                User.full_name.label("full_name"),
                Attempt.score_total_correct.label("score_total_correct"),
                Attempt.finished_at.label("finished_at"),
            )
            .join(User, User.id == Attempt.user_id)
            .where(
                Attempt.test_id == test_id,
                Attempt.status.in_(("submitted", "expired")),
            )
            .order_by(
                Attempt.score_total_correct.desc(),
                Attempt.finished_at.asc(),
            )
            .limit(limit)
        )
        rows = await self._session.execute(stmt)
        return [
            LeaderboardEntry(
                attempt_id=row.attempt_id,
                user_id=row.user_id,
                full_name=row.full_name,
                score_total_correct=int(row.score_total_correct or 0),
                finished_at=row.finished_at,
            )
            for row in rows
        ]
