"""Integration test for :class:`ReceiptService` against real MySQL."""

from __future__ import annotations

import random
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import (
    ReceiptAlreadyPendingError,
    ReceiptAlreadyProcessedError,
    ReceiptLimitExceededError,
)
from app.repositories.admin_repository import AdminRepository
from app.repositories.receipt_repository import ReceiptRepository
from app.repositories.user_repository import UserRepository
from app.services.image_hasher import ImageHasher
from app.services.receipt_service import ReceiptService, ReceiptWarning
from app.services.user_service import UserService


def _png_bytes(*, color: tuple[int, int, int]) -> bytes:
    """Build a tiny solid-color PNG; the color determines the pHash."""
    img = Image.new("RGB", (64, 64), color)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _distinct_png(idx: int) -> bytes:
    """Build a high-entropy PNG seeded by ``idx`` → a distinct pHash.

    Solid-color (and even simple two-tone) images hash alike — too little
    spatial-frequency content — and would trip the duplicate guard before the
    pending-limit check we're actually exercising. Seeded random noise gives
    each image a near-maximally different perceptual hash while staying
    deterministic across runs.
    """
    rnd = random.Random(idx)
    data = bytes(rnd.getrandbits(8) for _ in range(64 * 64 * 3))
    img = Image.frombytes("RGB", (64, 64), data)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _settings_stub() -> MagicMock:
    """Minimal SettingsService stand-in returning the default pHash threshold."""
    settings = MagicMock()
    settings.get_int = AsyncMock(return_value=5)
    return settings


async def test_receipt_service_submit_and_approve_flow(session: AsyncSession) -> None:
    users = UserRepository(session)
    admins = AdminRepository(session)
    receipts = ReceiptRepository(session)
    hasher = ImageHasher()
    svc = ReceiptService(
        receipts, users, hasher, _settings_stub(), UserService(users), max_pending_per_user=3
    )

    user = await users.create(telegram_id=600, username="paid")
    await users.set_status(user.id, "pending_payment")
    admin = await admins.create(telegram_id=900, role="owner", added_by_admin_id=None)

    # ---------- happy submit ----------
    result = await svc.submit(
        user,
        photo_file_id="file_red",
        photo_file_unique_id="unique_red",
        photo_bytes=_png_bytes(color=(220, 0, 0)),
    )
    assert result.receipt.id is not None
    assert result.warnings == ()

    session.expunge_all()
    refreshed = await users.get_by_id(user.id)
    assert refreshed is not None and refreshed.status == "pending_approval"

    # ---------- silent dedup against own pending queue ----------
    with pytest.raises(ReceiptAlreadyPendingError):
        await svc.submit(
            user,
            photo_file_id="file_red_again",
            photo_file_unique_id="unique_red_again",
            photo_bytes=_png_bytes(color=(220, 0, 0)),  # same image → same phash
        )

    # ---------- approval flow ----------
    approved = await svc.approve(result.receipt.id, admin_user=admin)
    assert approved.id == user.id
    assert approved.status == "approved"

    # Second approval call (idempotency) raises ReceiptAlreadyProcessedError.
    with pytest.raises(ReceiptAlreadyProcessedError):
        await svc.approve(result.receipt.id, admin_user=admin)


async def test_receipt_service_rejection_resets_user(session: AsyncSession) -> None:
    users = UserRepository(session)
    admins = AdminRepository(session)
    receipts = ReceiptRepository(session)
    hasher = ImageHasher()
    svc = ReceiptService(receipts, users, hasher, _settings_stub(), UserService(users))

    user = await users.create(telegram_id=601, username="rejected")
    await users.set_status(user.id, "pending_payment")
    admin = await admins.create(telegram_id=901, role="owner", added_by_admin_id=None)

    submission = await svc.submit(
        user,
        photo_file_id="file_blue",
        photo_file_unique_id="unique_blue",
        photo_bytes=_png_bytes(color=(0, 0, 220)),
    )

    rejected = await svc.reject(submission.receipt.id, admin_user=admin, reason="нечитаемое фото")
    assert rejected.id == user.id
    assert rejected.status == "rejected"

    # An empty reason is a programmer error — service raises ValueError.
    with pytest.raises(ValueError):
        await svc.reject(submission.receipt.id, admin_user=admin, reason="   ")


async def test_receipt_service_enforces_pending_limit(session: AsyncSession) -> None:
    users = UserRepository(session)
    receipts = ReceiptRepository(session)
    hasher = ImageHasher()
    svc = ReceiptService(
        receipts, users, hasher, _settings_stub(), UserService(users), max_pending_per_user=3
    )

    user = await users.create(telegram_id=602, username="spammy")
    await users.set_status(user.id, "pending_payment")

    # Three perceptually distinct images, three pending receipts — all accepted.
    for idx in range(3):
        await svc.submit(
            user,
            photo_file_id=f"file_{idx}",
            photo_file_unique_id=f"unique_{idx}",
            photo_bytes=_distinct_png(idx),
        )

    # A fourth distinct image is rejected for exceeding the cap (not for dedup).
    with pytest.raises(ReceiptLimitExceededError):
        await svc.submit(
            user,
            photo_file_id="file_overflow",
            photo_file_unique_id="unique_overflow",
            photo_bytes=_distinct_png(3),
        )


async def test_receipt_service_flags_duplicate_of_approved(session: AsyncSession) -> None:
    users = UserRepository(session)
    admins = AdminRepository(session)
    receipts = ReceiptRepository(session)
    hasher = ImageHasher()
    svc = ReceiptService(receipts, users, hasher, _settings_stub(), UserService(users))

    admin = await admins.create(telegram_id=902, role="owner", added_by_admin_id=None)

    # First user: submits and gets approved.
    first = await users.create(telegram_id=603, username="first")
    await users.set_status(first.id, "pending_payment")
    photo = _png_bytes(color=(120, 80, 200))
    first_result = await svc.submit(
        first,
        photo_file_id="f1",
        photo_file_unique_id="u1",
        photo_bytes=photo,
    )
    await svc.approve(first_result.receipt.id, admin_user=admin)

    # Second user submits the *same* image — should be flagged as a warning.
    second = await users.create(telegram_id=604, username="second")
    await users.set_status(second.id, "pending_payment")
    second_result = await svc.submit(
        second,
        photo_file_id="f2",
        photo_file_unique_id="u2",
        photo_bytes=photo,
    )
    assert ReceiptWarning.DUPLICATE_APPROVED in second_result.warnings
