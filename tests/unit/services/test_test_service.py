"""Unit tests for ``TestService.publish`` branching.

The full archive→activate transaction is covered by the integration suite
against real MySQL; here we pin the in-memory branching, especially the
CODE_REVIEW C8 conversion of a unique-index IntegrityError into a friendly
``PublishConflictError``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from app.exceptions import PublishConflictError
from app.services.test_service import TestService


def _service(
    *,
    active=None,
    mark_active_returns: int = 1,
    mark_active_raises: Exception | None = None,
    published=None,
    pending_images: list[int] | None = None,
) -> tuple[TestService, MagicMock]:
    tests_repo = MagicMock()
    tests_repo.get_active = AsyncMock(return_value=active)
    tests_repo.mark_archived = AsyncMock(return_value=1)
    if mark_active_raises is not None:
        tests_repo.mark_active = AsyncMock(side_effect=mark_active_raises)
    else:
        tests_repo.mark_active = AsyncMock(return_value=mark_active_returns)
    tests_repo.get_by_id = AsyncMock(
        return_value=published or SimpleNamespace(id=5, status="active")
    )

    questions_repo = MagicMock()
    # The publish() image-completeness guard reads this; default: nothing missing.
    questions_repo.list_missing_image_positions = AsyncMock(return_value=pending_images or [])

    svc = TestService(tests_repo, questions_repo, MagicMock())
    return svc, tests_repo


def _integrity_error() -> IntegrityError:
    return IntegrityError(
        "UPDATE tests SET status='active' ...",
        {},
        Exception("(1062, \"Duplicate entry '1' for key 'ux_tests__one_active'\")"),
    )


async def test_publish_happy_path_activates_and_returns_test() -> None:
    published = SimpleNamespace(id=5, status="active")
    svc, repo = _service(active=None, published=published)

    result = await svc.publish(5, notify=False)

    repo.mark_active.assert_awaited_once_with(5)
    assert result is published


async def test_publish_archives_prior_active_before_activating() -> None:
    prior = SimpleNamespace(id=3)
    svc, repo = _service(active=prior)

    await svc.publish(5, notify=True)

    repo.mark_archived.assert_awaited_once_with(3)
    repo.mark_active.assert_awaited_once_with(5)


async def test_publish_raises_value_error_when_draft_not_activatable() -> None:
    svc, _ = _service(active=None, mark_active_returns=0)

    with pytest.raises(ValueError):
        await svc.publish(5, notify=False)


async def test_publish_converts_unique_index_conflict_to_publish_conflict() -> None:
    # CODE_REVIEW C8: a concurrent publish already activated another test, so
    # the ux_tests__one_active index rejects this activation. The raw
    # IntegrityError must become a friendly PublishConflictError.
    svc, _ = _service(active=None, mark_active_raises=_integrity_error())

    with pytest.raises(PublishConflictError):
        await svc.publish(5, notify=False)


async def test_publish_refuses_when_image_questions_missing_their_image() -> None:
    # Defense in depth: the authoring flow shouldn't reach publish with images
    # still missing, but if it does we must not activate a half-built test.
    svc, repo = _service(active=None, pending_images=[19, 42])

    with pytest.raises(ValueError):
        await svc.publish(5, notify=False)

    repo.mark_active.assert_not_awaited()
