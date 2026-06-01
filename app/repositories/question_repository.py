"""Data access for the ``questions`` table.

A ``Question`` is immutable once a test is published — this repository
bulk-inserts at draft time and reads-only afterward.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select, update

from app.models.question import Question
from app.repositories.base import BaseRepository


@dataclass(frozen=True)
class QuestionDraft:
    """Plain-data shape consumed by :meth:`QuestionRepository.bulk_create`.

    Lives here (not in services) so the repository doesn't have to import
    from a higher layer. The Excel parser constructs these from validated
    rows; the test-create service hands them to the repo unchanged.

    ``has_image`` carries the Excel ``has_image`` flag through to the row; the
    image id itself is filled in later, in-bot, by the authoring flow.
    """

    section: str
    position: int
    question_text: str
    option_a: str
    option_b: str
    option_c: str
    option_d: str
    correct_option: str
    has_image: bool = False


class QuestionRepository(BaseRepository):
    """Reads + writes for ``questions``."""

    async def bulk_create(self, test_id: int, drafts: list[QuestionDraft]) -> list[Question]:
        """Insert every draft as a row of ``questions`` for the given test."""
        rows = [
            Question(
                test_id=test_id,
                section=d.section,
                position=d.position,
                question_text=d.question_text,
                option_a=d.option_a,
                option_b=d.option_b,
                option_c=d.option_c,
                option_d=d.option_d,
                correct_option=d.correct_option,
                has_image=d.has_image,
            )
            for d in drafts
        ]
        self._session.add_all(rows)
        await self._session.flush()
        return rows

    async def list_by_test(self, test_id: int) -> list[Question]:
        """Every question of a test, ordered by ``position``."""
        stmt = select(Question).where(Question.test_id == test_id).order_by(Question.position.asc())
        return list((await self._session.execute(stmt)).scalars().all())

    async def get_by_test_position(self, test_id: int, position: int) -> Question | None:
        """Look up one question by ``(test_id, position)``."""
        stmt = (
            select(Question)
            .where(Question.test_id == test_id, Question.position == position)
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_missing_image_positions(self, test_id: int) -> list[int]:
        """Positions flagged ``has_image`` that still have no image attached.

        Drives the in-bot image-collection step of the authoring flow: the
        teacher is prompted for these positions, in order, until the list is
        empty. Querying the DB (not FSM) means a dropped connection mid-upload
        is recoverable (PRODUCT_BLUEPRINT principle 3).
        """
        stmt = (
            select(Question.position)
            .where(
                Question.test_id == test_id,
                Question.has_image.is_(True),
                Question.image_file_id.is_(None),
            )
            .order_by(Question.position.asc())
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def count_with_images(self, test_id: int) -> int:
        """How many questions of this test are flagged to carry an image."""
        stmt = select(func.count()).where(
            Question.test_id == test_id,
            Question.has_image.is_(True),
        )
        return int((await self._session.execute(stmt)).scalar_one())

    async def set_image(
        self,
        test_id: int,
        position: int,
        *,
        file_id: str,
        file_unique_id: str,
    ) -> int:
        """Attach a Telegram image to one question. Returns the rowcount.

        Scoped to ``(test_id, position)`` so a stale callback can't write
        across tests. The ``has_image`` guard makes attaching to a text
        question a no-op (rowcount 0) rather than a silent corruption.
        """
        stmt = (
            update(Question)
            .where(
                Question.test_id == test_id,
                Question.position == position,
                Question.has_image.is_(True),
            )
            .values(image_file_id=file_id, image_file_unique_id=file_unique_id)
        )
        result = await self._session.execute(stmt)
        return result.rowcount
