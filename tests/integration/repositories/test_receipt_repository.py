"""Integration test for ``ReceiptRepository``."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.admin_repository import AdminRepository
from app.repositories.receipt_repository import ReceiptRepository
from app.repositories.user_repository import UserRepository
from app.utils.datetime import now_utc

# A real pHash whose top bit is set is stored as its signed-64 form (see
# ImageHasher._to_signed_64) — i.e. negative — so it fits MySQL's signed
# BIGINT and asyncmy can bind it. That's exactly the value production writes;
# exercise the round-trip of that case here.
_PHASH_TOP_BIT_SET = 0xABCDEF0123456789 - (1 << 64)


async def test_receipt_repository_happy_path(session: AsyncSession) -> None:
    users = UserRepository(session)
    admins = AdminRepository(session)
    receipts = ReceiptRepository(session)

    user = await users.create(telegram_id=200, username="paid")
    admin = await admins.create(telegram_id=900, role="owner", added_by_admin_id=None)

    # ---------- create ----------
    r1 = await receipts.create(
        user_id=user.id,
        telegram_file_id="file_id_one",
        telegram_file_unique_id="unique_one",
        image_phash=_PHASH_TOP_BIT_SET,
    )
    r2 = await receipts.create(
        user_id=user.id,
        telegram_file_id="file_id_two",
        telegram_file_unique_id="unique_two",
        image_phash=0x1111111111111111,
    )
    assert r1.id is not None and r1.status == "pending"

    # ---------- get_by_id ----------
    fetched = await receipts.get_by_id(r1.id)
    assert fetched is not None and fetched.telegram_file_id == "file_id_one"
    assert await receipts.get_by_id(9999) is None

    # ---------- set_admin_notification_message_id ----------
    await receipts.set_admin_notification_message_id(r1.id, 42)
    session.expunge_all()
    updated = await receipts.get_by_id(r1.id)
    assert updated is not None and updated.admin_notification_message_id == 42

    # ---------- count_pending_for_user ----------
    assert await receipts.count_pending_for_user(user.id) == 2

    # ---------- mark_approved ----------
    rowcount = await receipts.mark_approved(r1.id, reviewed_by_admin_id=admin.id)
    assert rowcount == 1
    # second call is a no-op because the status guard fires
    assert await receipts.mark_approved(r1.id, reviewed_by_admin_id=admin.id) == 0
    session.expunge_all()
    approved = await receipts.get_by_id(r1.id)
    assert approved is not None
    assert approved.status == "approved"
    assert approved.reviewed_by_admin_id == admin.id
    assert approved.reviewed_at is not None

    # ---------- mark_rejected ----------
    rowcount = await receipts.mark_rejected(r2.id, admin.id, "blurry photo")
    assert rowcount == 1
    session.expunge_all()
    rejected = await receipts.get_by_id(r2.id)
    assert rejected is not None
    assert rejected.status == "rejected"
    assert rejected.rejection_reason == "blurry photo"

    assert await receipts.count_pending_for_user(user.id) == 0

    # ---------- list_approved_with_phash ----------
    approved_list = await receipts.list_approved_with_phash()
    assert [r.id for r in approved_list] == [r1.id]
    assert approved_list[0].image_phash == _PHASH_TOP_BIT_SET

    # ---------- list_pending_older_than ----------
    # Make a fresh pending receipt; nothing older-than-now-plus-1h yet.
    r3 = await receipts.create(
        user_id=user.id,
        telegram_file_id="file_id_three",
        telegram_file_unique_id="unique_three",
        image_phash=None,
    )
    cutoff_future = now_utc() + timedelta(hours=1)
    cutoff_past = now_utc() - timedelta(hours=1)

    stale = await receipts.list_pending_older_than(cutoff_future)
    assert [r.id for r in stale] == [r3.id]

    none_yet = await receipts.list_pending_older_than(cutoff_past)
    assert none_yet == []
