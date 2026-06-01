"""E2E flow #5: duplicate-image receipt detection across users.

PRODUCT_BLUEPRINT §8.2 step 4 + §14.1 (perceptual-hash dedup). The
second user submitting the same image should still get the receipt
queued, but the admin sees a duplicate warning in the result DTO.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.receipt_service import ReceiptWarning
from tests.integration.flows._helpers import (
    build_services,
    make_bot_mock,
    make_redis_mock,
    png_bytes,
)


async def test_second_user_submitting_same_image_triggers_duplicate_warning(
    session: AsyncSession,
) -> None:
    bot = make_bot_mock()
    redis = make_redis_mock()
    services = build_services(session, bot=bot, redis=redis)

    admin = await services.admin.create(telegram_id=900, role="owner", added_by_admin_id=None)

    # ---------- user A submits + admin approves ----------
    user_a = await services.user.get_or_create(telegram_id=1001, username="anvar")
    await services.user.start_onboarding(user_a.id)
    await services.user.set_phone(user_a.id, "+998901112223")
    await services.user.set_name(user_a.id, "Anvar A")
    await services.user.attach_reference_code(user_a.id, "ANV001")
    session.expunge_all()
    user_a = await services.user.get_user(user_a.id)
    assert user_a is not None

    receipt_bytes = png_bytes(color=(123, 45, 67))

    submission_a = await services.receipt.submit(
        user_a,
        photo_file_id="dup_image",
        photo_file_unique_id="dup_unique",
        photo_bytes=receipt_bytes,
    )
    assert submission_a.warnings == ()  # first submit of this image → no flags
    approved_a = await services.receipt.approve(submission_a.receipt.id, admin_user=admin)
    assert approved_a.status == "approved"

    # ---------- user B submits the SAME image ----------
    user_b = await services.user.get_or_create(telegram_id=1002, username="bek")
    await services.user.start_onboarding(user_b.id)
    await services.user.set_phone(user_b.id, "+998901112224")
    await services.user.set_name(user_b.id, "Bek B")
    await services.user.attach_reference_code(user_b.id, "BEK001")
    session.expunge_all()
    user_b = await services.user.get_user(user_b.id)
    assert user_b is not None

    submission_b = await services.receipt.submit(
        user_b,
        photo_file_id="dup_image_b",
        photo_file_unique_id="dup_unique_b",
        photo_bytes=receipt_bytes,
    )

    # ---------- pHash hit → admin sees the warning flag ----------
    assert ReceiptWarning.DUPLICATE_APPROVED in submission_b.warnings
    # Submission still goes through — admin makes the final call.
    assert submission_b.receipt.status == "pending"
    assert submission_b.receipt.user_id == user_b.id
    # The two receipts share their image_phash (the bot computes it from
    # bytes, so identical bytes ⇒ identical 64-bit hash).
    assert submission_a.receipt.image_phash == submission_b.receipt.image_phash
