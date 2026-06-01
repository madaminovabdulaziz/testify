"""Unit tests for :class:`app.services.receipt_service.ReceiptService`."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.exceptions import (
    ReceiptAlreadyPendingError,
    ReceiptAlreadyProcessedError,
    ReceiptLimitExceededError,
    ReceiptUserBannedError,
)
from app.services.receipt_service import (
    ReceiptService,
    ReceiptSubmissionResult,
    ReceiptWarning,
)
from app.services.user_service import UserService


def _user(
    *, id: int = 1, status: str = "pending_payment", phone: str | None = None
) -> SimpleNamespace:
    return SimpleNamespace(id=id, status=status, phone=phone)


def _admin(*, id: int = 100) -> SimpleNamespace:
    return SimpleNamespace(id=id)


def _receipt(
    *,
    id: int = 1,
    user_id: int = 1,
    status: str = "pending",
    image_phash: int | None = 0xABCDEF0123456789,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=id,
        user_id=user_id,
        status=status,
        image_phash=image_phash,
    )


def _make_service(
    *,
    pending_count: int = 0,
    pending_for_user: list | None = None,
    dup_receipts: list | None = None,
    pending_other: list | None = None,
    phone_twin: object | None = None,
    create_returns: object | None = None,
    mark_approved_rowcount: int = 1,
    mark_rejected_rowcount: int = 1,
    user_mark_approved_rowcount: int = 1,
    user_mark_rejected_rowcount: int = 1,
    user_after: object | None = None,
    is_similar_returns: bool | list[bool] = False,
    phash_value: int = 0xDEADBEEFCAFEBABE,
) -> tuple[ReceiptService, MagicMock, MagicMock, MagicMock]:
    """Build a ReceiptService wired to AsyncMock collaborators."""
    receipts_repo = MagicMock()
    receipts_repo.count_pending_for_user = AsyncMock(return_value=pending_count)
    receipts_repo.list_pending_for_user = AsyncMock(return_value=pending_for_user or [])

    async def _list_with_phash(statuses, *, exclude_user_id=None):
        if "pending" in statuses:
            return pending_other or []
        return dup_receipts or []  # approved / rejected scan

    receipts_repo.list_with_phash = AsyncMock(side_effect=_list_with_phash)
    receipts_repo.create = AsyncMock(return_value=create_returns or _receipt())
    receipts_repo.mark_approved = AsyncMock(return_value=mark_approved_rowcount)
    receipts_repo.mark_rejected = AsyncMock(return_value=mark_rejected_rowcount)
    receipts_repo.get_by_id = AsyncMock(return_value=create_returns or _receipt())

    users_repo = MagicMock()
    users_repo.get_by_id = AsyncMock(return_value=user_after or _user(status="pending_approval"))
    users_repo.set_status = AsyncMock()
    users_repo.mark_approved = AsyncMock(return_value=user_mark_approved_rowcount)
    users_repo.mark_rejected = AsyncMock(return_value=user_mark_rejected_rowcount)
    users_repo.find_approved_by_phone = AsyncMock(return_value=phone_twin)

    hasher = MagicMock()
    hasher.hash = MagicMock(return_value=phash_value)
    if isinstance(is_similar_returns, list):
        hasher.is_similar = MagicMock(side_effect=is_similar_returns)
    else:
        hasher.is_similar = MagicMock(return_value=is_similar_returns)

    settings = MagicMock()
    settings.get_int = AsyncMock(return_value=5)

    # Inject a real UserService over the mock repo so the existing
    # users_repo.* assertions still hold (L7).
    svc = ReceiptService(
        receipts_repo,
        users_repo,
        hasher,
        settings,
        UserService(users_repo),
        max_pending_per_user=3,
    )
    return svc, receipts_repo, users_repo, hasher


# ---------- submit happy path ----------


async def test_submit_persists_receipt_when_clean() -> None:
    svc, receipts, _users, hasher = _make_service(
        pending_count=0,
        pending_for_user=[],
        dup_receipts=[],
    )
    user = _user(id=42, status="pending_payment")

    result = await svc.submit(
        user,
        photo_file_id="file123",
        photo_file_unique_id="unique123",
        photo_bytes=b"\x89PNG\r\n\x1a\n",
    )

    assert isinstance(result, ReceiptSubmissionResult)
    assert result.warnings == ()
    receipts.create.assert_awaited_once()
    create_kwargs = receipts.create.call_args.kwargs
    assert create_kwargs["user_id"] == 42
    assert create_kwargs["telegram_file_id"] == "file123"
    assert create_kwargs["telegram_file_unique_id"] == "unique123"
    assert create_kwargs["image_phash"] == hasher.hash.return_value


# ---------- pending limit ----------


async def test_submit_raises_when_pending_limit_hit() -> None:
    svc, receipts, _, _ = _make_service(pending_count=3)
    user = _user(id=42)

    with pytest.raises(ReceiptLimitExceededError):
        await svc.submit(
            user,
            photo_file_id="x",
            photo_file_unique_id="u",
            photo_bytes=b"img",
        )
    receipts.create.assert_not_awaited()


# ---------- pending duplicate ----------


async def test_submit_raises_when_same_image_already_pending() -> None:
    """If pHash matches an existing pending receipt for this user, silently reject."""
    existing_pending = _receipt(image_phash=0x1111)
    svc, receipts, _, hasher = _make_service(
        pending_for_user=[existing_pending],
        is_similar_returns=True,
    )
    user = _user(id=42)

    with pytest.raises(ReceiptAlreadyPendingError):
        await svc.submit(
            user,
            photo_file_id="x",
            photo_file_unique_id="u",
            photo_bytes=b"img",
        )

    receipts.create.assert_not_awaited()
    hasher.is_similar.assert_called()


async def test_submit_skips_pending_dup_check_when_existing_phash_is_null() -> None:
    """Pending row without a phash can't be compared — proceed normally."""
    existing_pending = _receipt(image_phash=None)
    svc, receipts, _, hasher = _make_service(
        pending_for_user=[existing_pending],
        dup_receipts=[],
    )
    user = _user(id=42)

    await svc.submit(
        user,
        photo_file_id="x",
        photo_file_unique_id="u",
        photo_bytes=b"img",
    )

    receipts.create.assert_awaited_once()
    # is_similar never invoked for the pending check (phash is None)
    # — and there are no approved-with-phash entries either.
    hasher.is_similar.assert_not_called()


# ---------- anti-fraud warnings (M8/M9/M10) ----------


async def test_submit_warns_when_pHash_collides_with_approved() -> None:
    approved = _receipt(status="approved", image_phash=0x2222)
    svc, _, _, _ = _make_service(
        pending_for_user=[],
        dup_receipts=[approved],
        is_similar_returns=True,
    )
    user = _user(id=42)

    result = await svc.submit(user, photo_file_id="x", photo_file_unique_id="u", photo_bytes=b"img")
    assert ReceiptWarning.DUPLICATE_APPROVED in result.warnings


async def test_submit_warns_when_pHash_collides_with_rejected() -> None:
    # M10: fraudster resubmits a tweaked copy of a previously rejected receipt.
    rejected = _receipt(status="rejected", image_phash=0x2222)
    svc, _, _, _ = _make_service(
        pending_for_user=[], dup_receipts=[rejected], is_similar_returns=True
    )
    user = _user(id=42)

    result = await svc.submit(user, photo_file_id="x", photo_file_unique_id="u", photo_bytes=b"img")
    assert ReceiptWarning.DUPLICATE_REJECTED in result.warnings


async def test_submit_warns_on_cross_user_pending_duplicate() -> None:
    # M9: another user submitted the same screenshot and it's still pending.
    other_pending = _receipt(user_id=99, status="pending", image_phash=0x2222)
    svc, _, _, _ = _make_service(
        pending_for_user=[], pending_other=[other_pending], is_similar_returns=True
    )
    user = _user(id=42)

    result = await svc.submit(user, photo_file_id="x", photo_file_unique_id="u", photo_bytes=b"img")
    assert ReceiptWarning.DUPLICATE_PENDING_OTHER in result.warnings


async def test_submit_warns_when_phone_reused_by_approved_user() -> None:
    # M8: phone already attached to a different approved student.
    svc, _, _, _ = _make_service(
        pending_for_user=[],
        dup_receipts=[],
        phone_twin=_user(id=7, status="approved"),
    )
    user = _user(id=42, phone="998901234567")

    result = await svc.submit(user, photo_file_id="x", photo_file_unique_id="u", photo_bytes=b"img")
    assert ReceiptWarning.PHONE_REUSED in result.warnings


async def test_submit_promotes_user_to_pending_approval() -> None:
    svc, _, users, _ = _make_service(
        user_after=_user(id=42, status="pending_payment"),
    )
    user = _user(id=42, status="pending_payment")

    await svc.submit(
        user,
        photo_file_id="x",
        photo_file_unique_id="u",
        photo_bytes=b"img",
    )

    users.set_status.assert_awaited_once_with(42, "pending_approval")


# ---------- approve ----------


async def test_approve_marks_user_and_returns_them() -> None:
    receipt_row = _receipt(id=7, user_id=42)
    approved_user = _user(id=42, status="approved")
    svc, receipts, users, _ = _make_service(
        create_returns=receipt_row,
        user_after=approved_user,
    )
    admin = _admin(id=100)

    result = await svc.approve(receipt_id=7, admin_user=admin)

    receipts.mark_approved.assert_awaited_once_with(7, reviewed_by_admin_id=100)
    users.mark_approved.assert_awaited_once_with(42)
    assert result is approved_user


async def test_approve_raises_when_already_processed() -> None:
    svc, _, _, _ = _make_service(mark_approved_rowcount=0)
    admin = _admin()

    with pytest.raises(ReceiptAlreadyProcessedError):
        await svc.approve(receipt_id=7, admin_user=admin)


async def test_approve_banned_user_raises_and_keeps_state() -> None:
    # The user mark_approved UPDATE flips 0 rows (status<>'banned' guard) and
    # the re-read shows them still banned → refuse, so the transaction rolls
    # back and the ban survives (CODE_REVIEW C2).
    receipt_row = _receipt(id=7, user_id=42)
    svc, _, _, _ = _make_service(
        create_returns=receipt_row,
        user_after=_user(id=42, status="banned"),
        user_mark_approved_rowcount=0,
    )
    admin = _admin(id=100)

    with pytest.raises(ReceiptUserBannedError):
        await svc.approve(receipt_id=7, admin_user=admin)


async def test_approve_idempotent_when_user_already_approved() -> None:
    # 0 rowcount but the user is already approved (a second receipt for the
    # same student) — that's legitimate, not a ban; return them normally.
    receipt_row = _receipt(id=7, user_id=42)
    approved_user = _user(id=42, status="approved")
    svc, _, _, _ = _make_service(
        create_returns=receipt_row,
        user_after=approved_user,
        user_mark_approved_rowcount=0,
    )
    admin = _admin(id=100)

    result = await svc.approve(receipt_id=7, admin_user=admin)
    assert result is approved_user


# ---------- reject ----------


async def test_reject_marks_user_rejected_and_stores_reason() -> None:
    receipt_row = _receipt(id=7, user_id=42)
    rejected_user = _user(id=42, status="rejected")
    svc, receipts, users, _ = _make_service(
        create_returns=receipt_row,
        user_after=rejected_user,
    )
    admin = _admin(id=100)

    result = await svc.reject(receipt_id=7, admin_user=admin, reason="  blurry  ")

    receipts.mark_rejected.assert_awaited_once_with(7, reviewed_by_admin_id=100, reason="blurry")
    users.mark_rejected.assert_awaited_once_with(42)
    assert result is rejected_user


async def test_reject_requires_non_empty_reason() -> None:
    svc, _, _, _ = _make_service()
    admin = _admin()

    with pytest.raises(ValueError):
        await svc.reject(receipt_id=7, admin_user=admin, reason="   ")


async def test_reject_raises_when_already_processed() -> None:
    svc, _, _, _ = _make_service(mark_rejected_rowcount=0)
    admin = _admin()

    with pytest.raises(ReceiptAlreadyProcessedError):
        await svc.reject(receipt_id=7, admin_user=admin, reason="reason")


# ---------- count_pending_for_user ----------


async def test_count_pending_delegates_to_repo() -> None:
    svc, receipts, _, _ = _make_service(pending_count=2)
    assert await svc.count_pending_for_user(42) == 2
    receipts.count_pending_for_user.assert_awaited_with(42)
