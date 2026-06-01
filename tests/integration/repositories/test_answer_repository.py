"""Integration test for ``AnswerRepository``."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.admin_repository import AdminRepository
from app.repositories.answer_repository import AnswerRepository
from app.repositories.attempt_repository import AttemptRepository
from app.repositories.question_repository import QuestionDraft, QuestionRepository
from app.repositories.test_repository import TestRepository
from app.repositories.user_repository import UserRepository
from app.utils.datetime import now_utc


def _drafts() -> list[QuestionDraft]:
    out: list[QuestionDraft] = []
    for section, lo, hi in (("rus_tili", 1, 35), ("pedagogik", 36, 45), ("kasbiy", 46, 50)):
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
                    correct_option="A",
                )
            )
    return out


async def test_answer_repository_happy_path(session: AsyncSession) -> None:
    users = UserRepository(session)
    admins = AdminRepository(session)
    tests = TestRepository(session)
    questions = QuestionRepository(session)
    attempts = AttemptRepository(session)
    answers = AnswerRepository(session)

    admin = await admins.create(telegram_id=900, role="owner", added_by_admin_id=None)
    test = await tests.create_draft(
        title="Sample",
        duration_seconds=3200,
        created_by_admin_id=admin.id,
    )
    q_rows = await questions.bulk_create(test.id, _drafts())
    # Convenience lookup: position -> question id.
    q_by_pos = {q.position: q for q in q_rows}

    user = await users.create(telegram_id=400, username="ansr")
    await users.mark_approved(user.id)
    attempt = await attempts.create(
        user_id=user.id,
        test_id=test.id,
        started_at=now_utc(),
        expires_at=now_utc() + timedelta(seconds=3200),
    )

    # ---------- upsert (insert path) ----------
    await answers.upsert(
        attempt_id=attempt.id,
        question_id=q_by_pos[1].id,
        selected_option="A",
        is_correct=True,
    )
    # ---------- get ----------
    answer = await answers.get(attempt.id, q_by_pos[1].id)
    assert answer is not None
    assert answer.selected_option == "A"
    assert answer.is_correct is True

    # ---------- upsert (update path: user changes mind) ----------
    await answers.upsert(
        attempt_id=attempt.id,
        question_id=q_by_pos[1].id,
        selected_option="B",
        is_correct=False,
    )
    session.expunge_all()
    changed = await answers.get(attempt.id, q_by_pos[1].id)
    assert changed is not None
    assert changed.selected_option == "B"
    assert changed.is_correct is False

    # ---------- multi-row inserts spanning sections ----------
    fixtures = [
        # rus_tili: 5 correct, 1 wrong
        (1, "A", True),
        (2, "A", True),
        (3, "A", True),
        (4, "A", True),
        (5, "A", True),
        (6, "B", False),
        # pedagogik: 2 correct
        (36, "A", True),
        (37, "A", True),
        # kasbiy: 1 correct, 1 wrong
        (46, "A", True),
        (47, "C", False),
    ]
    for pos, opt, ok in fixtures:
        await answers.upsert(
            attempt_id=attempt.id,
            question_id=q_by_pos[pos].id,
            selected_option=opt,
            is_correct=ok,
        )

    # ---------- list_by_attempt ----------
    all_for_attempt = await answers.list_by_attempt(attempt.id)
    # 10 unique positions answered (Q1 was upserted twice).
    assert len(all_for_attempt) == 10

    # ---------- aggregate_scores_by_attempt ----------
    scores = await answers.aggregate_scores_by_attempt(attempt.id)
    assert scores.get("rus_tili", 0) == 5
    assert scores.get("pedagogik", 0) == 2
    assert scores.get("kasbiy", 0) == 1

    # ---------- count_correctness_by_question ----------
    stats = await answers.count_correctness_by_question(test.id)
    by_pos = {s.position: s for s in stats}
    # Q1 was upserted three times; the final fixture row is ("A", correct), so
    # its persisted is_correct is True → correct count 1.
    assert by_pos[1].attempted == 1 and by_pos[1].correct == 1
    assert by_pos[2].attempted == 1 and by_pos[2].correct == 1
    assert by_pos[6].attempted == 1 and by_pos[6].correct == 0
    assert by_pos[36].attempted == 1 and by_pos[36].correct == 1
    assert by_pos[47].attempted == 1 and by_pos[47].correct == 0
    # questions never answered are absent from the result set
    assert 7 not in by_pos
    assert 50 not in by_pos
