"""Unit tests for AttemptService error paths (CODE_REVIEW M1/M4/M5)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from app.exceptions import AttemptAlreadyExistsError, AttemptNotVisibleError
from app.services.attempt_service import AttemptService


def _service() -> tuple[AttemptService, MagicMock]:
    attempts = MagicMock()
    svc = AttemptService(
        attempts,
        MagicMock(),  # answer repo
        MagicMock(),  # question repo
        MagicMock(),  # scoring
        MagicMock(),  # scheduler
    )
    return svc, attempts


def _attempt(*, user_id: int = 7, status: str = "in_progress") -> SimpleNamespace:
    return SimpleNamespace(
        id=42,
        user_id=user_id,
        test_id=3,
        status=status,
        current_position=1,
        started_at=datetime(2026, 5, 24, 10, 0, tzinfo=UTC),
        expires_at=datetime(2026, 5, 24, 10, 53, 20, tzinfo=UTC),
    )


# ---------- M5: ownership mismatch is a UserError, not SystemError ----------


async def test_get_state_raises_not_visible_for_foreign_attempt() -> None:
    svc, attempts = _service()
    attempts.get_by_id = AsyncMock(return_value=_attempt(user_id=999))

    with pytest.raises(AttemptNotVisibleError):
        await svc.get_state(42, user_id=7)


async def test_get_state_raises_not_visible_for_missing_attempt() -> None:
    svc, attempts = _service()
    attempts.get_by_id = AsyncMock(return_value=None)

    with pytest.raises(AttemptNotVisibleError):
        await svc.get_state(404, user_id=7)


# ---------- M4: ownership-checked fetch ----------


async def test_get_attempt_for_user_returns_none_on_mismatch() -> None:
    svc, attempts = _service()
    attempts.get_by_id = AsyncMock(return_value=_attempt(user_id=999))

    assert await svc.get_attempt_for_user(42, user_id=7) is None


async def test_get_attempt_for_user_returns_attempt_when_owned() -> None:
    svc, attempts = _service()
    owned = _attempt(user_id=7)
    attempts.get_by_id = AsyncMock(return_value=owned)

    assert await svc.get_attempt_for_user(42, user_id=7) is owned


# ---------- M1: concurrent-start IntegrityError → friendly error ----------


async def test_start_converts_unique_violation_to_already_exists() -> None:
    svc, attempts = _service()
    attempts.get_in_progress_for_user = AsyncMock(return_value=None)
    attempts.get_by_user_and_test = AsyncMock(return_value=None)
    attempts.create = AsyncMock(
        side_effect=IntegrityError("INSERT", {}, Exception("Duplicate ux_attempts__user_test"))
    )

    user = SimpleNamespace(id=7, status="approved")
    test = SimpleNamespace(id=3, duration_seconds=3200)

    with pytest.raises(AttemptAlreadyExistsError) as caught:
        await svc.start(user, test)

    # No id available (poisoned session) — handler will surface the friendly
    # message rather than trying to resume a specific attempt.
    assert caught.value.existing_attempt_id is None
