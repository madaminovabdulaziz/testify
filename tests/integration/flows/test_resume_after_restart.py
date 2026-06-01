"""E2E flow #4: bot restart mid-attempt preserves student's progress.

PRODUCT_BLUEPRINT §8.5 ("Resume behavior") + ARCHITECTURE_SPEC §10.3
(``current_position`` persists to DB, not just FSM).

The bot restart is simulated by discarding the live services bundle
mid-test and rebuilding a fresh one on the same DB connection — this
is what a process restart looks like to the DB, only without losing
the test's transaction.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.flows._helpers import (
    build_services,
    make_bot_mock,
    make_redis_mock,
    valid_xlsx_bytes,
)


async def test_attempt_state_is_preserved_across_a_restart(
    session: AsyncSession,
) -> None:
    bot = make_bot_mock()
    redis = make_redis_mock()

    # ---------- pre-restart phase ----------
    services_before = build_services(session, bot=bot, redis=redis)

    admin = await services_before.admin.create(
        telegram_id=900, role="owner", added_by_admin_id=None
    )
    user = await services_before.user.get_or_create(telegram_id=555, username="rufat")
    await services_before.user.start_onboarding(user.id)
    await services_before.user.set_phone(user.id, "+998901111111")
    await services_before.user.set_name(user.id, "Rufat Tester")
    await services_before.user.attach_reference_code(user.id, "RFT555")
    await services_before.user.mark_approved(user.id)
    session.expunge_all()
    user = await services_before.user.get_user(user.id)
    assert user is not None and user.status == "approved"

    draft = await services_before.test.create_draft_from_excel(
        valid_xlsx_bytes(correct="A"), uploaded_by_admin_id=admin.id
    )
    test = await services_before.test.publish(draft.id, notify=False)

    attempt = await services_before.attempt.start(user, test)

    # Answer a couple of questions and navigate to position 17.
    state = await services_before.attempt.get_state(attempt.id, user_id=user.id)
    answered_positions = (3, 7, 12)
    for question in state.questions:
        if question.position not in answered_positions:
            continue
        await services_before.attempt.submit_answer(
            attempt.id,
            user_id=user.id,
            question_position=question.position,
            selected_option="A",
        )
    await services_before.attempt.set_current_position(attempt.id, user_id=user.id, position=17)

    # ---------- simulated restart: drop the services bundle ----------
    # SQLAlchemy caches anything attached to the old session, so expire
    # to force a fresh read just like a new process would.
    session.expunge_all()
    del services_before

    # ---------- post-restart phase ----------
    services_after = build_services(session, bot=bot, redis=redis)

    user_fresh = await services_after.user.get_user(user.id)
    assert user_fresh is not None
    resumed_state = await services_after.attempt.get_state(attempt.id, user_id=user_fresh.id)

    # Resume on the question the user navigated to, not position 1.
    assert resumed_state.status == "in_progress"
    assert resumed_state.current_position == 17

    # Their three answers survived.
    answered_qids = set(resumed_state.answers_by_question_id.keys())
    pos_by_qid = {q.id: q.position for q in resumed_state.questions}
    answered_positions_recovered = {pos_by_qid[qid] for qid in answered_qids}
    assert answered_positions_recovered == set(answered_positions)

    # Finishing now still works and scores the three answers correctly.
    result = await services_after.attempt.finish(attempt.id, reason="user")
    assert result.attempt.status == "submitted"
    assert result.scores.total == 3
    assert result.scores.rus_tili == 3  # positions 3/7/12 all in rus_tili
