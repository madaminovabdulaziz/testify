"""E2E flow #2: receipt rejected → user resubmits → admin approves.

PRODUCT_BLUEPRINT §8.3 step 5 (re-approval) + §10.1 state machine
(rejected → pending_approval on new submission → approved).
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.flows._helpers import (
    build_services,
    make_bot_mock,
    make_redis_mock,
    png_bytes,
)


async def test_rejected_receipt_can_be_resubmitted_and_approved(
    session: AsyncSession,
) -> None:
    bot = make_bot_mock()
    redis = make_redis_mock()
    services = build_services(session, bot=bot, redis=redis)

    admin = await services.admin.create(telegram_id=900, role="owner", added_by_admin_id=None)

    # Onboard a user up to ``pending_payment``.
    user = await services.user.get_or_create(telegram_id=4242, username="zara")
    await services.user.start_onboarding(user.id)
    await services.user.set_phone(user.id, "+998901112233")
    await services.user.set_name(user.id, "Zara Test")
    await services.user.attach_reference_code(user.id, "ZRA001")
    session.expunge_all()
    user = await services.user.get_user(user.id)
    assert user is not None and user.status == "pending_payment"

    # ---------- first receipt → rejected ----------
    first = await services.receipt.submit(
        user,
        photo_file_id="receipt_v1",
        photo_file_unique_id="unique_v1",
        photo_bytes=png_bytes(color=(50, 50, 200)),
    )
    session.expunge_all()
    after_first_submit = await services.user.get_user(user.id)
    assert after_first_submit is not None
    assert after_first_submit.status == "pending_approval"

    rejected_user = await services.receipt.reject(
        first.receipt.id, admin_user=admin, reason="фото нечитаемое"
    )
    assert rejected_user.status == "rejected"

    # ---------- second receipt → approved ----------
    session.expunge_all()
    user_after_rejection = await services.user.get_user(user.id)
    assert user_after_rejection is not None
    assert user_after_rejection.status == "rejected"

    # Different image so pHash dedup doesn't trip.
    second = await services.receipt.submit(
        user_after_rejection,
        photo_file_id="receipt_v2",
        photo_file_unique_id="unique_v2",
        photo_bytes=png_bytes(color=(0, 200, 50)),
    )
    session.expunge_all()
    after_second_submit = await services.user.get_user(user.id)
    assert after_second_submit is not None
    assert after_second_submit.status == "pending_approval"

    approved = await services.receipt.approve(second.receipt.id, admin_user=admin)
    assert approved.status == "approved"

    # ---------- DB row reflects the final state ----------
    session.expunge_all()
    final = await services.user.get_user(user.id)
    assert final is not None
    assert final.status == "approved"
    assert final.approved_at is not None

    # First receipt stays in 'rejected' with its reason captured.
    from app.repositories.receipt_repository import ReceiptRepository

    receipts = ReceiptRepository(session)
    first_row = await receipts.get_by_id(first.receipt.id)
    assert first_row is not None
    assert first_row.status == "rejected"
    assert first_row.rejection_reason == "фото нечитаемое"

    second_row = await receipts.get_by_id(second.receipt.id)
    assert second_row is not None
    assert second_row.status == "approved"
