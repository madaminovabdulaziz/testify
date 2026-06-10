"""Panel test management: list, create, edit/save, publish, delete, duplicate.

Handlers follow the bot's layering contract: aiohttp in, services in the
middle, templates out. No SQLAlchemy here — everything goes through
``container.services(session)`` inside a ``session_scope``.
"""

from __future__ import annotations

from typing import Any

import aiohttp_jinja2
import structlog
from aiohttp import web

from app.bot.handlers.admin.tests import spawn_publish_broadcast
from app.exceptions import DraftRequiredError, PublishConflictError
from app.models.admin import Admin
from app.models.test import Test
from app.web.auth import REQUEST_ADMIN, REQUEST_SESSION, login_required
from app.web.db import session_scope
from app.web.forms import ParsedTestForm, parse_test_form
from app.web.keys import KEY_CONTAINER

logger = structlog.get_logger()

_SECTION_BLOCKS = (
    ("rus_tili", "Русский язык", 1, 35),
    ("pedagogik", "Педагогическое мастерство", 36, 45),
    ("kasbiy", "Профессиональный стандарт", 46, 50),
)


def _base_context(request: web.Request, **extra: Any) -> dict[str, Any]:
    """Shared template context for authenticated pages."""
    admin: Admin = request[REQUEST_ADMIN]
    return {
        "authenticated": True,
        "admin": admin,
        "csrf_token": request[REQUEST_SESSION].csrf,
        **extra,
    }


def _test_id(request: web.Request) -> int:
    return int(request.match_info["test_id"])


async def panel_root_redirect(request: web.Request) -> web.StreamResponse:
    """GET /panel → /panel/ (canonical)."""
    raise web.HTTPMovedPermanently(location="/panel/")


@login_required
async def tests_list(request: web.Request) -> web.StreamResponse:
    """GET /panel/ — the test inventory."""
    container = request.app[KEY_CONTAINER]
    async with session_scope(container) as session:
        entries = await container.services(session).test.list_recent(limit=50)
    context = _base_context(request, active_nav="tests", entries=entries)
    return aiohttp_jinja2.render_template("tests_list.html", request, context)


@login_required
async def create_draft(request: web.Request) -> web.StreamResponse:
    """POST /panel/tests/new — create an empty draft and open the editor."""
    container = request.app[KEY_CONTAINER]
    admin: Admin = request[REQUEST_ADMIN]
    async with session_scope(container) as session:
        draft = await container.services(session).test.create_empty_draft(admin.id)
    raise web.HTTPSeeOther(location=f"/panel/tests/{draft.id}")


@login_required
async def test_detail(request: web.Request) -> web.StreamResponse:
    """GET /panel/tests/{id} — editor for drafts, read-only view otherwise."""
    container = request.app[KEY_CONTAINER]
    test_id = _test_id(request)
    async with session_scope(container) as session:
        services = container.services(session)
        test = await services.test.get_test(test_id)
        if test is None:
            return _not_found(request)
        questions = await services.test.list_questions(test_id)
        blockers = (
            await services.test.validate_for_publish(test_id) if test.status == "draft" else []
        )

    if test.status != "draft":
        context = _base_context(
            request,
            active_nav="tests",
            test=test,
            questions=questions,
            section_blocks=_SECTION_BLOCKS,
        )
        return aiohttp_jinja2.render_template("test_view.html", request, context)

    context = _editor_context(request, test, questions=questions, blockers=blockers)
    if request.query.get("saved") == "1":
        context["flash"] = "Сохранено."
    return aiohttp_jinja2.render_template("test_editor.html", request, context)


@login_required
async def save_draft(request: web.Request) -> web.StreamResponse:
    """POST /panel/tests/{id} — validate the form; replace title + questions."""
    container = request.app[KEY_CONTAINER]
    test_id = _test_id(request)
    form = await request.post()
    parsed = parse_test_form({k: v for k, v in form.items() if isinstance(v, str)})

    async with session_scope(container) as session:
        services = container.services(session)
        test = await services.test.get_test(test_id)
        if test is None:
            return _not_found(request)
        if test.status != "draft":
            return _conflict(request, DraftRequiredError.user_message)

        if parsed.has_errors:
            # Re-render with the submitted values echoed back; no DB write.
            blockers = await services.test.validate_for_publish(test_id)
            questions = await services.test.list_questions(test_id)
            context = _editor_context(
                request, test, questions=questions, blockers=blockers, parsed=parsed
            )
            return aiohttp_jinja2.render_template("test_editor.html", request, context, status=422)

        await services.test.update_title(test_id, parsed.title)
        await services.test.replace_draft_questions(test_id, parsed.drafts)

    logger.info("panel_draft_saved", test_id=test_id, questions=len(parsed.drafts))
    raise web.HTTPSeeOther(location=f"/panel/tests/{test_id}?saved=1")


@login_required
async def publish(request: web.Request) -> web.StreamResponse:
    """POST /panel/tests/{id}/publish — gate on blockers, then atomic publish."""
    container = request.app[KEY_CONTAINER]
    test_id = _test_id(request)
    form = await request.post()
    notify = form.get("notify") == "1"

    async with session_scope(container) as session:
        services = container.services(session)
        test = await services.test.get_test(test_id)
        if test is None:
            return _not_found(request)
        if test.status != "draft":
            return _conflict(request, "Этот тест уже опубликован.")

        blockers = await services.test.validate_for_publish(test_id)
        if blockers:
            questions = await services.test.list_questions(test_id)
            context = _editor_context(request, test, questions=questions, blockers=blockers)
            context["flash_error"] = "Тест не готов к публикации: " + " ".join(blockers)
            return aiohttp_jinja2.render_template("test_editor.html", request, context, status=409)

        try:
            published = await services.test.publish(test_id, notify=notify)
        except (PublishConflictError, ValueError) as exc:
            logger.warning("panel_publish_failed", test_id=test_id, reason=str(exc))
            return _conflict(
                request,
                "Не удалось опубликовать тест — попробуйте ещё раз через секунду.",
            )

    if notify:
        spawn_publish_broadcast(container, published)
    raise web.HTTPSeeOther(location=f"/panel/tests/{test_id}")


@login_required
async def delete_draft(request: web.Request) -> web.StreamResponse:
    """POST /panel/tests/{id}/delete — drafts only; published tests are history."""
    container = request.app[KEY_CONTAINER]
    test_id = _test_id(request)
    async with session_scope(container) as session:
        removed = await container.services(session).test.cancel_draft(test_id)
    logger.info("panel_draft_deleted", test_id=test_id, removed=removed)
    raise web.HTTPSeeOther(location="/panel/")


@login_required
async def duplicate(request: web.Request) -> web.StreamResponse:
    """POST /panel/tests/{id}/duplicate — copy any test into a fresh draft."""
    container = request.app[KEY_CONTAINER]
    admin: Admin = request[REQUEST_ADMIN]
    test_id = _test_id(request)
    async with session_scope(container) as session:
        try:
            draft = await container.services(session).test.duplicate_to_draft(
                test_id, created_by_admin_id=admin.id
            )
        except ValueError:
            return _not_found(request)
    raise web.HTTPSeeOther(location=f"/panel/tests/{draft.id}")


# ---------- helpers ----------


def _editor_context(
    request: web.Request,
    test: Test,
    *,
    questions: list[Any],
    blockers: list[str],
    parsed: ParsedTestForm | None = None,
) -> dict[str, Any]:
    """Assemble everything ``test_editor.html`` needs.

    ``values`` is position -> field -> str. When re-rendering after a failed
    save, the admin's submitted values win over what's stored in the DB.
    """
    values: dict[int, dict[str, str]] = {}
    image_attached: dict[int, bool] = {}
    for q in questions:
        values[q.position] = {
            "text": q.question_text,
            "a": q.option_a,
            "b": q.option_b,
            "c": q.option_c,
            "d": q.option_d,
            "correct": q.correct_option,
            "has_image": "1" if q.has_image else "",
        }
        image_attached[q.position] = q.image_file_id is not None

    title = test.title
    field_errors: dict[int, dict[str, str]] = {}
    form_errors: list[str] = []
    if parsed is not None:
        values = parsed.raw
        title = parsed.title or test.title
        field_errors = parsed.field_errors
        form_errors = parsed.form_errors

    return _base_context(
        request,
        active_nav="tests",
        test=test,
        title_value=title,
        values=values,
        image_attached=image_attached,
        field_errors=field_errors,
        form_errors=form_errors,
        blockers=blockers,
        section_blocks=_SECTION_BLOCKS,
    )


def _not_found(request: web.Request) -> web.StreamResponse:
    context = _base_context(request, message="Тест не найден.")
    return aiohttp_jinja2.render_template("error.html", request, context, status=404)


def _conflict(request: web.Request, message: str) -> web.StreamResponse:
    context = _base_context(request, message=message)
    return aiohttp_jinja2.render_template("error.html", request, context, status=409)
