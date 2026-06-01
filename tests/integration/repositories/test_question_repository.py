"""Integration test for ``QuestionRepository``."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.admin_repository import AdminRepository
from app.repositories.question_repository import QuestionDraft, QuestionRepository
from app.repositories.test_repository import TestRepository


def _make_drafts() -> list[QuestionDraft]:
    """Build a synthetic 50-question test split per the spec's 35/10/5."""
    drafts: list[QuestionDraft] = []
    sections = [("rus_tili", 1, 35), ("pedagogik", 36, 45), ("kasbiy", 46, 50)]
    for section, lo, hi in sections:
        for pos in range(lo, hi + 1):
            drafts.append(
                QuestionDraft(
                    section=section,
                    position=pos,
                    question_text=f"Вопрос #{pos}",
                    option_a=f"A-{pos}",
                    option_b=f"B-{pos}",
                    option_c=f"C-{pos}",
                    option_d=f"D-{pos}",
                    correct_option="A",
                )
            )
    return drafts


async def test_question_repository_happy_path(session: AsyncSession) -> None:
    admins = AdminRepository(session)
    tests = TestRepository(session)
    questions = QuestionRepository(session)

    admin = await admins.create(telegram_id=900, role="owner", added_by_admin_id=None)
    test = await tests.create_draft(
        title="Sample",
        duration_seconds=3200,
        created_by_admin_id=admin.id,
    )

    # ---------- bulk_create ----------
    drafts = _make_drafts()
    rows = await questions.bulk_create(test.id, drafts)
    assert len(rows) == 50
    assert all(q.id is not None for q in rows)

    # ---------- list_by_test (ordered by position) ----------
    listed = await questions.list_by_test(test.id)
    assert [q.position for q in listed] == list(range(1, 51))
    assert listed[0].section == "rus_tili"
    assert listed[35].section == "pedagogik"
    assert listed[45].section == "kasbiy"

    # ---------- get_by_test_position ----------
    q5 = await questions.get_by_test_position(test.id, 5)
    assert q5 is not None
    assert q5.position == 5
    assert q5.question_text == "Вопрос #5"

    assert await questions.get_by_test_position(test.id, 51) is None


async def test_question_repository_image_collection(session: AsyncSession) -> None:
    admins = AdminRepository(session)
    tests = TestRepository(session)
    questions = QuestionRepository(session)

    admin = await admins.create(telegram_id=901, role="owner", added_by_admin_id=None)
    test = await tests.create_draft(
        title="With images",
        duration_seconds=3200,
        created_by_admin_id=admin.id,
    )

    drafts = _make_drafts()
    # Flag positions 5 and 20 as image questions.
    for idx in (4, 19):  # positions 5 and 20 (0-based list index)
        d = drafts[idx]
        drafts[idx] = QuestionDraft(
            section=d.section,
            position=d.position,
            question_text=d.question_text,
            option_a=d.option_a,
            option_b=d.option_b,
            option_c=d.option_c,
            option_d=d.option_d,
            correct_option=d.correct_option,
            has_image=True,
        )
    await questions.bulk_create(test.id, drafts)

    # Both flagged positions start out missing their image.
    assert await questions.count_with_images(test.id) == 2
    assert await questions.list_missing_image_positions(test.id) == [5, 20]

    # Attach one — it drops out of the pending list.
    updated = await questions.set_image(test.id, 5, file_id="tg-file-5", file_unique_id="uniq-5")
    assert updated == 1
    assert await questions.list_missing_image_positions(test.id) == [20]

    q5 = await questions.get_by_test_position(test.id, 5)
    assert q5 is not None
    assert q5.image_file_id == "tg-file-5"
    assert q5.image_file_unique_id == "uniq-5"

    # Attaching to a text question is a no-op (guarded by has_image).
    assert await questions.set_image(test.id, 1, file_id="x", file_unique_id="y") == 0

    # Finish the second one — nothing left pending.
    await questions.set_image(test.id, 20, file_id="tg-file-20", file_unique_id="uniq-20")
    assert await questions.list_missing_image_positions(test.id) == []
