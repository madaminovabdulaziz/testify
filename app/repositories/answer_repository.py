"""Data access for the ``answers`` table.

``upsert`` is a MySQL ``INSERT ... ON DUPLICATE KEY UPDATE`` (the unique
``(attempt_id, question_id)`` index makes it a no-op when the user picks
the same option twice and a clean update when they change their mind).
See DATABASE_SPEC §10.8 / §10.11.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Integer, cast, func, select, text

from app.models.answer import Answer
from app.models.question import Question
from app.repositories.base import BaseRepository


@dataclass(frozen=True)
class QuestionStats:
    """One row of per-question correctness analytics."""

    position: int
    section: str
    attempted: int
    correct: int


# Status-guarded upsert (CODE_REVIEW H1). Written as raw SQL because
# SQLAlchemy's ``insert().from_select().on_duplicate_key_update()`` emits the
# MySQL-8 row-alias (``... AS new``) in a position that is invalid for the
# INSERT…SELECT form and fails with a 1064 syntax error on a real server.
# The derived table ``d`` carries the new values; the EXISTS guard makes the
# whole statement a no-op (0 rows) when the attempt is no longer in_progress,
# so a near-buzzer tap can never land an answer on an already-scored attempt.
# ``answered_at`` / ``updated_at`` are handled by the column defaults
# (DEFAULT / ON UPDATE CURRENT_TIMESTAMP(6)).
_GUARDED_UPSERT_SQL = text(
    """
    INSERT INTO answers (attempt_id, question_id, selected_option, is_correct)
    SELECT d.attempt_id, d.question_id, d.selected_option, d.is_correct
    FROM (
        SELECT
            :attempt_id      AS attempt_id,
            :question_id     AS question_id,
            :selected_option AS selected_option,
            :is_correct      AS is_correct
    ) AS d
    WHERE EXISTS (
        SELECT 1 FROM attempts a
        WHERE a.id = d.attempt_id AND a.status = 'in_progress'
    )
    ON DUPLICATE KEY UPDATE
        selected_option = d.selected_option,
        is_correct = d.is_correct
    """
)


class AnswerRepository(BaseRepository):
    """Reads + writes for ``answers``."""

    async def get(self, attempt_id: int, question_id: int) -> Answer | None:
        """Fetch the user's current pick for a given question of an attempt."""
        stmt = (
            select(Answer)
            .where(
                Answer.attempt_id == attempt_id,
                Answer.question_id == question_id,
            )
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def upsert(
        self,
        *,
        attempt_id: int,
        question_id: int,
        selected_option: str,
        is_correct: bool,
    ) -> None:
        """Insert or replace the user's answer for one question — only while the attempt is open.

        Atomically guarded so an answer can never land on a finalized attempt
        (CODE_REVIEW H1); see :data:`_GUARDED_UPSERT_SQL` for the why.
        """
        await self._session.execute(
            _GUARDED_UPSERT_SQL,
            {
                "attempt_id": attempt_id,
                "question_id": question_id,
                "selected_option": selected_option,
                "is_correct": is_correct,
            },
        )

    async def list_by_attempt(self, attempt_id: int) -> list[Answer]:
        """All answers belonging to one attempt."""
        stmt = select(Answer).where(Answer.attempt_id == attempt_id)
        return list((await self._session.execute(stmt)).scalars().all())

    async def aggregate_scores_by_attempt(self, attempt_id: int) -> dict[str, int]:
        """Return ``{section: correct_count}`` for one attempt.

        Sections that have zero correct answers may be absent from the
        dict — callers should treat missing keys as 0.
        """
        stmt = (
            select(
                Question.section.label("section"),
                # cast to Integer first: a bare ``func.sum`` over a Boolean
                # column inherits Boolean type affinity, so MySQL's summed count
                # gets coerced back to a bool (5 → True → 1). Casting the operand
                # to Integer keeps the real per-section / per-question count.
                func.sum(cast(Answer.is_correct, Integer)).label("correct"),
            )
            .join(Question, Question.id == Answer.question_id)
            .where(Answer.attempt_id == attempt_id)
            .group_by(Question.section)
        )
        rows = await self._session.execute(stmt)
        return {row.section: int(row.correct or 0) for row in rows}

    async def count_correctness_by_question(self, test_id: int) -> list[QuestionStats]:
        """Per-question attempted-vs-correct counts across every attempt of a test."""
        stmt = (
            select(
                Question.position.label("position"),
                Question.section.label("section"),
                func.count().label("attempted"),
                # cast to Integer first: a bare ``func.sum`` over a Boolean
                # column inherits Boolean type affinity, so MySQL's summed count
                # gets coerced back to a bool (5 → True → 1). Casting the operand
                # to Integer keeps the real per-section / per-question count.
                func.sum(cast(Answer.is_correct, Integer)).label("correct"),
            )
            .join(Answer, Answer.question_id == Question.id)
            .where(Question.test_id == test_id)
            .group_by(Question.id, Question.position, Question.section)
            .order_by(Question.position.asc())
        )
        rows = await self._session.execute(stmt)
        return [
            QuestionStats(
                position=int(row.position),
                section=row.section,
                attempted=int(row.attempted),
                correct=int(row.correct or 0),
            )
            for row in rows
        ]
