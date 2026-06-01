"""E2E flow #1: new user → onboarding → payment → approval → first test → score.

PRODUCT_BLUEPRINT §17 acceptance criterion 1 + §7.1 happy path.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.flows._helpers import (
    build_services,
    make_bot_mock,
    make_redis_mock,
    png_bytes,
    valid_xlsx_bytes,
)


async def test_user_walks_full_funnel_and_finishes_with_correct_score(
    session: AsyncSession,
) -> None:
    bot = make_bot_mock()
    redis = make_redis_mock()
    services = build_services(session, bot=bot, redis=redis)

    # ---------- (admin exists for receipt approval) ----------
    admin = await services.admin.create(telegram_id=900, role="owner", added_by_admin_id=None)

    # ---------- onboarding ----------
    user = await services.user.get_or_create(telegram_id=12345, username="malika")
    assert user.status == "new"

    await services.user.start_onboarding(user.id)
    await services.user.set_phone(user.id, "+998901234567")
    await services.user.set_name(user.id, "Malika Tashkent")
    ref_code = await services.ref_code.generate_unique()
    await services.user.attach_reference_code(user.id, ref_code)

    session.expunge_all()
    onboarded = await services.user.get_user(user.id)
    assert onboarded is not None
    assert onboarded.status == "pending_payment"
    assert onboarded.full_name == "Malika Tashkent"
    # set_phone normalizes to digits-only (normalize_phone), so the leading "+"
    # is stripped on the way in.
    assert onboarded.phone == "998901234567"
    assert onboarded.reference_code == ref_code

    # ---------- receipt submission + approval ----------
    submission = await services.receipt.submit(
        onboarded,
        photo_file_id="tg_file_001",
        photo_file_unique_id="tg_unique_001",
        photo_bytes=png_bytes(color=(220, 0, 0)),
    )
    assert submission.warnings == ()  # clean submit → no anti-fraud flags

    session.expunge_all()
    pending = await services.user.get_user(user.id)
    assert pending is not None and pending.status == "pending_approval"

    approved_user = await services.receipt.approve(submission.receipt.id, admin_user=admin)
    assert approved_user.status == "approved"

    # ---------- admin publishes a test ----------
    # 35 rus_tili (correct=A) + 10 pedagogik (correct=A) + 5 kasbiy (correct=A).
    test = await services.test.create_draft_from_excel(
        valid_xlsx_bytes(correct="A"), uploaded_by_admin_id=admin.id, title="E2E test"
    )
    published = await services.test.publish(test.id, notify=False)
    assert published.status == "active"

    # ---------- student takes the test ----------
    session.expunge_all()
    approved_fresh = await services.user.get_user(user.id)
    assert approved_fresh is not None
    attempt = await services.attempt.start(approved_fresh, published)
    assert attempt.status == "in_progress"

    # Answer every question — pick A for 1..40 (correct) and B for 41..50 (wrong).
    state = await services.attempt.get_state(attempt.id, user_id=user.id)
    for question in state.questions:
        chosen = "A" if question.position <= 40 else "B"
        await services.attempt.submit_answer(
            attempt.id,
            user_id=user.id,
            question_position=question.position,
            selected_option=chosen,
        )

    # ---------- finish + verify score ----------
    result = await services.attempt.finish(attempt.id, reason="user")
    assert result.attempt.status == "submitted"
    # 40 of 50 are correct: 35 rus_tili + 5 pedagogik (positions 36–40).
    # Positions 41–45 (pedagogik) and 46–50 (kasbiy) all picked B (wrong → correct was A).
    assert result.scores.total == 40
    assert result.scores.rus_tili == 35
    assert result.scores.pedagogik == 5
    assert result.scores.kasbiy == 0

    session.expunge_all()
    persisted = await services.attempt.get_attempt(attempt.id)
    assert persisted is not None
    assert persisted.score_total_correct == 40
    assert persisted.finished_at is not None
