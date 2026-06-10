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

from app.exceptions import DraftRequiredError, PublishConflictError
from app.repositories.question_repository import QuestionDraft
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


# ---------- web-panel draft editing ----------


def _draft_service(
    *,
    test=None,
    questions: list | None = None,
    image_map: dict | None = None,
    missing_images: list[int] | None = None,
) -> tuple[TestService, MagicMock, MagicMock]:
    tests_repo = MagicMock()
    tests_repo.get_by_id = AsyncMock(return_value=test)
    tests_repo.create_draft = AsyncMock(
        return_value=SimpleNamespace(id=9, title="Тест от 2026-06-10", status="draft")
    )
    tests_repo.update_title = AsyncMock(return_value=1)

    questions_repo = MagicMock()
    questions_repo.list_by_test = AsyncMock(return_value=questions or [])
    questions_repo.map_images_by_position = AsyncMock(return_value=image_map or {})
    questions_repo.delete_by_test = AsyncMock(return_value=50)
    questions_repo.bulk_create = AsyncMock(return_value=[])
    questions_repo.list_missing_image_positions = AsyncMock(return_value=missing_images or [])

    svc = TestService(tests_repo, questions_repo, MagicMock())
    return svc, tests_repo, questions_repo


def _qd(pos: int, *, has_image: bool = False) -> QuestionDraft:
    section = "rus_tili" if pos <= 35 else ("pedagogik" if pos <= 45 else "kasbiy")
    return QuestionDraft(
        section=section,
        position=pos,
        question_text=f"Вопрос {pos}",
        option_a="А",
        option_b="Б",
        option_c="В",
        option_d="Г",
        correct_option="A",
        has_image=has_image,
    )


async def test_create_empty_draft_uses_default_title() -> None:
    svc, tests_repo, _ = _draft_service()

    test = await svc.create_empty_draft(7)

    assert test.id == 9
    kwargs = tests_repo.create_draft.await_args.kwargs
    assert kwargs["title"].startswith("Тест от ")
    assert kwargs["created_by_admin_id"] == 7


async def test_update_title_rejects_empty_and_overlong() -> None:
    svc, _, _ = _draft_service()
    with pytest.raises(ValueError):
        await svc.update_title(1, "   ")
    with pytest.raises(ValueError):
        await svc.update_title(1, "x" * 201)


async def test_update_title_returns_false_for_non_draft() -> None:
    svc, tests_repo, _ = _draft_service()
    tests_repo.update_title = AsyncMock(return_value=0)
    assert await svc.update_title(1, "Новое имя") is False


async def test_replace_questions_requires_draft_status() -> None:
    svc, _, questions_repo = _draft_service(test=SimpleNamespace(id=1, status="active"))

    with pytest.raises(DraftRequiredError):
        await svc.replace_draft_questions(1, [_qd(1)])

    questions_repo.delete_by_test.assert_not_awaited()


async def test_replace_questions_carries_over_existing_images() -> None:
    svc, _, questions_repo = _draft_service(
        test=SimpleNamespace(id=1, status="draft"),
        image_map={5: ("fid-5", "uid-5")},
    )

    await svc.replace_draft_questions(1, [_qd(5, has_image=True), _qd(6)])

    questions_repo.delete_by_test.assert_awaited_once_with(1)
    created = questions_repo.bulk_create.await_args.args[1]
    by_pos = {d.position: d for d in created}
    assert by_pos[5].image_file_id == "fid-5"
    assert by_pos[5].image_file_unique_id == "uid-5"
    assert by_pos[6].image_file_id is None


async def test_replace_questions_drops_image_when_flag_unchecked() -> None:
    svc, _, questions_repo = _draft_service(
        test=SimpleNamespace(id=1, status="draft"),
        image_map={5: ("fid-5", "uid-5")},
    )

    await svc.replace_draft_questions(1, [_qd(5, has_image=False)])

    created = questions_repo.bulk_create.await_args.args[1]
    assert created[0].image_file_id is None


async def test_validate_for_publish_reports_counts_and_missing_images() -> None:
    questions = [
        SimpleNamespace(
            section="rus_tili" if p <= 35 else "pedagogik" if p <= 45 else "kasbiy", position=p
        )
        for p in range(1, 50)  # only 49 questions
    ]
    svc, _, _ = _draft_service(questions=questions, missing_images=[3, 17])

    blockers = await svc.validate_for_publish(1)

    assert any("Ожидалось 50 вопросов, найдено 49" in b for b in blockers)
    assert any("Нет изображения для вопросов: 3, 17." in b for b in blockers)


async def test_validate_for_publish_empty_when_complete() -> None:
    questions = [
        SimpleNamespace(
            section="rus_tili" if p <= 35 else "pedagogik" if p <= 45 else "kasbiy", position=p
        )
        for p in range(1, 51)
    ]
    svc, _, _ = _draft_service(questions=questions)

    assert await svc.validate_for_publish(1) == []


async def test_duplicate_to_draft_copies_questions_and_images() -> None:
    source_rows = [
        SimpleNamespace(
            section="rus_tili",
            position=1,
            question_text="Q",
            option_a="a",
            option_b="b",
            option_c="c",
            option_d="d",
            correct_option="A",
            has_image=True,
            image_file_id="fid",
            image_file_unique_id="uid",
        )
    ]
    svc, tests_repo, questions_repo = _draft_service(
        test=SimpleNamespace(id=3, title="Тест от 2026-06-01", status="archived"),
        questions=source_rows,
    )

    new_draft = await svc.duplicate_to_draft(3, created_by_admin_id=7)

    assert new_draft.id == 9
    assert tests_repo.create_draft.await_args.kwargs["title"] == "Тест от 2026-06-01 (копия)"
    copied = questions_repo.bulk_create.await_args.args[1]
    assert copied[0].image_file_id == "fid"
    assert copied[0].has_image is True


async def test_duplicate_to_draft_raises_for_missing_source() -> None:
    svc, _, _ = _draft_service(test=None)
    with pytest.raises(ValueError):
        await svc.duplicate_to_draft(404, created_by_admin_id=None)
