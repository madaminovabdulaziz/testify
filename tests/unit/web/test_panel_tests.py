"""HTTP tests for panel test CRUD, publish, delete, duplicate."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from app.bot.webhook import make_app
from app.exceptions import PublishConflictError
from tests.unit.web._fakes import login_client, make_container


def _draft(test_id: int = 5, status: str = "draft") -> SimpleNamespace:
    return SimpleNamespace(id=test_id, title="Тест от 2026-06-10", status=status)


def _question(pos: int, *, has_image: bool = False, image: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        position=pos,
        section="rus_tili" if pos <= 35 else ("pedagogik" if pos <= 45 else "kasbiy"),
        question_text=f"Вопрос {pos}",
        option_a="А",
        option_b="Б",
        option_c="В",
        option_d="Г",
        correct_option="A",
        has_image=has_image,
        image_file_id="fid" if image else None,
        image_file_unique_id="uid" if image else None,
    )


def _full_form(test_id: int, csrf: str) -> dict[str, str]:
    data = {"title": "Готовый тест", "csrf_token": csrf}
    for pos in range(1, 51):
        data.update(
            {
                f"q{pos}_text": f"Вопрос {pos}",
                f"q{pos}_a": "А",
                f"q{pos}_b": "Б",
                f"q{pos}_c": "В",
                f"q{pos}_d": "Г",
                f"q{pos}_correct": "B",
            }
        )
    return data


@pytest.fixture
async def harness():
    services = MagicMock()
    services.test.list_recent = AsyncMock(return_value=[])
    services.test.get_test = AsyncMock(return_value=_draft())
    services.test.list_questions = AsyncMock(return_value=[])
    services.test.validate_for_publish = AsyncMock(return_value=[])
    services.test.create_empty_draft = AsyncMock(return_value=_draft(7))
    services.test.update_title = AsyncMock(return_value=True)
    services.test.replace_draft_questions = AsyncMock()
    services.test.publish = AsyncMock(return_value=_draft(5, "active"))
    services.test.cancel_draft = AsyncMock(return_value=True)
    services.test.duplicate_to_draft = AsyncMock(return_value=_draft(8))

    container = make_container(services=services)
    app = make_app(container, dispatcher=MagicMock())
    client = TestClient(TestServer(app))
    await client.start_server()
    csrf = None
    try:
        csrf = await login_client(client, container)
        yield client, services, csrf, container
    finally:
        await client.close()


# ---------- list + create ----------


async def test_tests_list_renders_entries(harness) -> None:
    client, services, _, _ = harness
    services.test.list_recent = AsyncMock(
        return_value=[
            SimpleNamespace(
                id=3,
                title="Тест от 2026-06-10",
                status="active",
                question_count=50,
                attempt_count=12,
                published_at=None,
            )
        ]
    )

    resp = await client.get("/panel/")
    body = await resp.text()

    assert resp.status == 200
    assert "Тест от 2026-06-10" in body
    assert "Активный" in body
    assert "50/50" in body


async def test_create_draft_redirects_to_editor(harness) -> None:
    client, services, csrf, _ = harness

    resp = await client.post("/panel/tests/new", data={"csrf_token": csrf}, allow_redirects=False)

    assert resp.status == 303
    assert resp.headers["Location"] == "/panel/tests/7"
    services.test.create_empty_draft.assert_awaited_once_with(1)  # admin.id == 1


# ---------- editor ----------


async def test_editor_renders_50_cards_for_draft(harness) -> None:
    client, _, _, _ = harness

    resp = await client.get("/panel/tests/5")
    body = await resp.text()

    assert resp.status == 200
    assert 'name="q1_text"' in body
    assert 'name="q50_text"' in body
    assert "Русский язык (вопросы 1–35)" in body
    assert "Профессиональный стандарт (вопросы 46–50)" in body


async def test_editor_404_for_missing_test(harness) -> None:
    client, services, _, _ = harness
    services.test.get_test = AsyncMock(return_value=None)

    resp = await client.get("/panel/tests/404")
    assert resp.status == 404


async def test_save_happy_path_replaces_questions_and_redirects(harness) -> None:
    client, services, csrf, _ = harness

    resp = await client.post("/panel/tests/5", data=_full_form(5, csrf), allow_redirects=False)

    assert resp.status == 303
    assert resp.headers["Location"] == "/panel/tests/5?saved=1"
    services.test.update_title.assert_awaited_once_with(5, "Готовый тест")
    drafts = services.test.replace_draft_questions.await_args.args[1]
    assert len(drafts) == 50
    assert all(d.correct_option == "B" for d in drafts)


async def test_save_with_field_error_rerenders_422_and_echoes_value(harness) -> None:
    client, services, csrf, _ = harness
    form = _full_form(5, csrf)
    form["q3_a"] = "x" * 301  # over the option cap

    resp = await client.post("/panel/tests/5", data=form, allow_redirects=False)
    body = await resp.text()

    assert resp.status == 422
    assert "длиннее 300" in body
    assert "x" * 301 in body  # submitted value echoed, not DB value
    assert 'href="#q3"' in body
    services.test.replace_draft_questions.assert_not_awaited()


async def test_save_on_published_test_conflicts(harness) -> None:
    client, services, csrf, _ = harness
    services.test.get_test = AsyncMock(return_value=_draft(5, "active"))

    resp = await client.post("/panel/tests/5", data=_full_form(5, csrf), allow_redirects=False)

    assert resp.status == 409
    services.test.replace_draft_questions.assert_not_awaited()


# ---------- read-only view ----------


async def test_published_test_renders_readonly_view(harness) -> None:
    client, services, _, _ = harness
    services.test.get_test = AsyncMock(return_value=_draft(5, "archived"))
    services.test.list_questions = AsyncMock(return_value=[_question(1, image=False)])

    resp = await client.get("/panel/tests/5")
    body = await resp.text()

    assert resp.status == 200
    assert "Дублировать в черновик" in body
    assert 'name="q1_text"' not in body  # no editable fields


# ---------- publish ----------


async def test_publish_with_notify_calls_service(harness) -> None:
    client, services, csrf, _ = harness

    resp = await client.post(
        "/panel/tests/5",
        data={"csrf_token": csrf},
        allow_redirects=False,
    )
    # save without questions is fine (empty draft) — now publish:
    resp = await client.post(
        "/panel/tests/5/publish",
        data={"csrf_token": csrf, "notify": "1"},
        allow_redirects=False,
    )

    assert resp.status == 303
    services.test.publish.assert_awaited_once_with(5, notify=True)


async def test_publish_silent_passes_notify_false(harness) -> None:
    client, services, csrf, _ = harness

    resp = await client.post(
        "/panel/tests/5/publish", data={"csrf_token": csrf}, allow_redirects=False
    )

    assert resp.status == 303
    services.test.publish.assert_awaited_once_with(5, notify=False)


async def test_publish_blocked_by_validation(harness) -> None:
    client, services, csrf, _ = harness
    services.test.validate_for_publish = AsyncMock(
        return_value=["Ожидалось 50 вопросов, найдено 3."]
    )

    resp = await client.post(
        "/panel/tests/5/publish", data={"csrf_token": csrf}, allow_redirects=False
    )

    assert resp.status == 409
    assert "Ожидалось 50 вопросов" in await resp.text()
    services.test.publish.assert_not_awaited()


async def test_publish_conflict_shows_friendly_page(harness) -> None:
    client, services, csrf, _ = harness
    services.test.publish = AsyncMock(side_effect=PublishConflictError())

    resp = await client.post(
        "/panel/tests/5/publish", data={"csrf_token": csrf}, allow_redirects=False
    )

    assert resp.status == 409
    assert "попробуйте ещё раз" in (await resp.text()).lower()


# ---------- delete + duplicate ----------


async def test_delete_draft_redirects_to_list(harness) -> None:
    client, services, csrf, _ = harness

    resp = await client.post(
        "/panel/tests/5/delete", data={"csrf_token": csrf}, allow_redirects=False
    )

    assert resp.status == 303
    assert resp.headers["Location"] == "/panel/"
    services.test.cancel_draft.assert_awaited_once_with(5)


async def test_duplicate_redirects_to_new_draft(harness) -> None:
    client, services, csrf, _ = harness

    resp = await client.post(
        "/panel/tests/5/duplicate", data={"csrf_token": csrf}, allow_redirects=False
    )

    assert resp.status == 303
    assert resp.headers["Location"] == "/panel/tests/8"
    services.test.duplicate_to_draft.assert_awaited_once_with(5, created_by_admin_id=1)
