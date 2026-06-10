"""HTTP tests for question-image upload + preview proxy."""

from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramAPIError
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

from app.bot.webhook import make_app
from tests.unit.web._fakes import login_client, make_container


def _jpeg_bytes(size: tuple[int, int] = (64, 48)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buf, format="JPEG")
    return buf.getvalue()


def _png_with_alpha() -> bytes:
    buf = BytesIO()
    Image.new("RGBA", (32, 32), (0, 100, 250, 120)).save(buf, format="PNG")
    return buf.getvalue()


def _telegram_message(message_id: int = 901) -> SimpleNamespace:
    return SimpleNamespace(
        message_id=message_id,
        photo=[
            SimpleNamespace(file_id="small", file_unique_id="u-small"),
            SimpleNamespace(file_id="big-fid", file_unique_id="u-big"),
        ],
    )


def _api_error() -> TelegramAPIError:
    return TelegramAPIError(method=MagicMock(), message="boom")


@pytest.fixture
async def harness():
    services = MagicMock()
    services.test.get_test = AsyncMock(
        return_value=SimpleNamespace(id=5, title="T", status="draft")
    )
    services.test.attach_question_image = AsyncMock(return_value=True)
    services.test.get_question = AsyncMock(
        return_value=SimpleNamespace(image_file_id="fid", image_file_unique_id="uid")
    )

    container = make_container(services=services)
    container.bot.send_photo = AsyncMock(return_value=_telegram_message())
    container.bot.delete_message = AsyncMock()

    async def _download(file_id, destination):
        destination.write(b"jpeg-bytes")

    container.bot.download = AsyncMock(side_effect=_download)

    app = make_app(container, dispatcher=MagicMock())
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        csrf = await login_client(client, container)
        yield client, services, container, csrf
    finally:
        await client.close()


def _upload(client, csrf: str, data: bytes, *, test_id: int = 5, pos: int = 3):
    form = {"image": data}
    return client.post(
        f"/panel/tests/{test_id}/questions/{pos}/image",
        data=form,
        headers={"X-CSRF-Token": csrf, "X-Requested-With": "fetch"},
    )


# ---------- upload ----------


async def test_upload_happy_path_launders_through_telegram(harness) -> None:
    client, services, container, csrf = harness

    resp = await _upload(client, csrf, _jpeg_bytes())
    payload = await resp.json()

    assert resp.status == 200, payload
    assert payload["ok"] is True
    assert payload["preview_url"] == "/panel/tests/5/questions/3/image?v=u-big"

    # Laundering order: send → delete → attach, largest rendition wins.
    send_kwargs = container.bot.send_photo.await_args.kwargs
    assert send_kwargs["chat_id"] == -1001
    assert send_kwargs["disable_notification"] is True
    container.bot.delete_message.assert_awaited_once_with(-1001, 901)
    services.test.attach_question_image.assert_awaited_once_with(
        5, 3, file_id="big-fid", file_unique_id="u-big"
    )


async def test_upload_reencodes_png_with_alpha_to_jpeg(harness) -> None:
    client, _, container, csrf = harness

    resp = await _upload(client, csrf, _png_with_alpha())

    assert resp.status == 200
    sent = container.bot.send_photo.await_args.kwargs["photo"]
    img = Image.open(BytesIO(sent.data))
    assert img.format == "JPEG"


async def test_upload_rejects_corrupt_bytes(harness) -> None:
    client, _, _, csrf = harness
    resp = await _upload(client, csrf, b"this is not an image")
    assert resp.status == 422


async def test_upload_rejects_extreme_aspect_ratio(harness) -> None:
    client, _, _, csrf = harness
    resp = await _upload(client, csrf, _jpeg_bytes(size=(2100, 50)))  # ratio 42:1
    assert resp.status == 422
    assert "вытянутое" in (await resp.json())["error"]


async def test_upload_rejects_oversized_file(harness) -> None:
    client, _, _, csrf = harness
    big = _jpeg_bytes() + b"\x00" * (5 * 1024 * 1024)
    resp = await _upload(client, csrf, big)
    assert resp.status == 413


async def test_upload_send_failure_returns_502(harness) -> None:
    client, services, container, csrf = harness
    container.bot.send_photo = AsyncMock(side_effect=_api_error())

    resp = await _upload(client, csrf, _jpeg_bytes())

    assert resp.status == 502
    services.test.attach_question_image.assert_not_awaited()


async def test_upload_to_published_test_conflicts(harness) -> None:
    client, services, container, csrf = harness
    services.test.get_test = AsyncMock(
        return_value=SimpleNamespace(id=5, title="T", status="active")
    )

    resp = await _upload(client, csrf, _jpeg_bytes())

    assert resp.status == 409
    container.bot.send_photo.assert_not_awaited()


async def test_upload_before_save_returns_409_save_first(harness) -> None:
    client, services, _, csrf = harness
    services.test.attach_question_image = AsyncMock(return_value=False)  # rowcount 0

    resp = await _upload(client, csrf, _jpeg_bytes())

    assert resp.status == 409
    assert "Сначала сохраните" in (await resp.json())["error"]


async def test_upload_survives_delete_message_failure(harness) -> None:
    client, _, container, csrf = harness
    container.bot.delete_message = AsyncMock(side_effect=_api_error())

    resp = await _upload(client, csrf, _jpeg_bytes())

    assert resp.status == 200  # delete is best-effort


# ---------- preview proxy ----------


async def test_preview_serves_bytes_with_etag(harness) -> None:
    client, _, _, _ = harness

    resp = await client.get("/panel/tests/5/questions/3/image")

    assert resp.status == 200
    assert resp.headers["ETag"] == "uid"
    assert resp.headers["Cache-Control"] == "private, max-age=86400"
    assert await resp.read() == b"jpeg-bytes"


async def test_preview_304_on_matching_etag(harness) -> None:
    client, _, container, _ = harness

    resp = await client.get("/panel/tests/5/questions/3/image", headers={"If-None-Match": "uid"})

    assert resp.status == 304
    container.bot.download.assert_not_awaited()


async def test_preview_404_when_no_image(harness) -> None:
    client, services, _, _ = harness
    services.test.get_question = AsyncMock(return_value=None)

    resp = await client.get("/panel/tests/5/questions/3/image")
    assert resp.status == 404


async def test_preview_404_when_download_fails(harness) -> None:
    client, _, container, _ = harness
    container.bot.download = AsyncMock(side_effect=_api_error())

    resp = await client.get("/panel/tests/5/questions/3/image")
    assert resp.status == 404
