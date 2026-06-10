"""Question-image upload (AJAX) + preview proxy.

Web-uploaded bytes are laundered through Telegram to obtain a *photo*-type
``file_id`` — the only thing the student-facing test screen can re-send.
Pipeline: validate with Pillow → re-encode to JPEG (guarantees sendPhoto
compatibility regardless of the source container/EXIF quirks) →
``send_photo`` to the admin group → capture ``message.photo[-1]`` → delete
the laundering message (best-effort; the file_id outlives it).
"""

from __future__ import annotations

import contextlib
from io import BytesIO

import structlog
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BufferedInputFile
from aiohttp import BodyPartReader, web
from PIL import Image, UnidentifiedImageError

from app.web.auth import login_required
from app.web.db import session_scope
from app.web.keys import KEY_CONTAINER

logger = structlog.get_logger()

_MAX_IMAGE_BYTES = 5 * 1024 * 1024  # PRODUCT_BLUEPRINT §13 file cap
_ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
# Telegram sendPhoto rejects aspect ratios beyond 20:1; size is fixed by resize.
_MAX_ASPECT_RATIO = 20
_RESIZE_LIMIT = 2560  # Telegram downscales photos anyway; keep uploads fast
_JPEG_QUALITY = 87

_ERR_TOO_LARGE = "Файл слишком большой. Максимум 5 МБ."
_ERR_BAD_IMAGE = "Не удалось прочитать изображение. Поддерживаются JPEG, PNG и WebP."
_ERR_BAD_GEOMETRY = "Изображение слишком вытянутое или слишком большое для Telegram."
_ERR_SEND_FAILED = (
    "Бот не смог отправить изображение в группу администраторов. "
    "Проверьте, что бот состоит в группе."
)
_ERR_NOT_DRAFT = "Изображения можно менять только у черновика."
_ERR_SAVE_FIRST = "Сначала сохраните тест — затем загрузите изображение."
_ERR_NO_FILE = "Файл не получен. Выберите изображение и повторите."


@login_required
async def upload_image(request: web.Request) -> web.Response:
    """POST /panel/tests/{id}/questions/{pos}/image — multipart field ``image``."""
    container = request.app[KEY_CONTAINER]
    test_id = int(request.match_info["test_id"])
    position = int(request.match_info["position"])

    data = await _read_upload(request)
    if data is None:
        return web.json_response({"error": _ERR_NO_FILE}, status=400)
    if len(data) > _MAX_IMAGE_BYTES:
        return web.json_response({"error": _ERR_TOO_LARGE}, status=413)

    try:
        jpeg_bytes = _normalize_to_jpeg(data)
    except UnidentifiedImageError:
        return web.json_response({"error": _ERR_BAD_IMAGE}, status=422)
    except _GeometryError:
        return web.json_response({"error": _ERR_BAD_GEOMETRY}, status=422)
    except Exception:
        logger.exception("web_image_decode_failed", test_id=test_id, position=position)
        return web.json_response({"error": _ERR_BAD_IMAGE}, status=422)

    # Pre-flight: the question must exist on a draft with has_image=1 before
    # we spend a Telegram round-trip on it.
    async with session_scope(container) as session:
        services = container.services(session)
        test = await services.test.get_test(test_id)
        if test is None or test.status != "draft":
            return web.json_response({"error": _ERR_NOT_DRAFT}, status=409)

    # Launder through Telegram for a photo-type file_id.
    settings = container.settings
    try:
        msg = await container.bot.send_photo(
            chat_id=settings.admin_group_id,
            photo=BufferedInputFile(jpeg_bytes, filename=f"test{test_id}_q{position}.jpg"),
            caption=f"🖼 Веб-панель: тест #{test_id}, вопрос {position}",
            disable_notification=True,
        )
    except TelegramAPIError:
        logger.exception("web_image_send_failed", test_id=test_id, position=position)
        return web.json_response({"error": _ERR_SEND_FAILED}, status=502)

    photo = msg.photo[-1] if msg.photo else None
    with contextlib.suppress(TelegramAPIError):
        await container.bot.delete_message(settings.admin_group_id, msg.message_id)
    if photo is None:  # pragma: no cover — sendPhoto always returns sizes
        return web.json_response({"error": _ERR_SEND_FAILED}, status=502)

    async with session_scope(container) as session:
        attached = await container.services(session).test.attach_question_image(
            test_id,
            position,
            file_id=photo.file_id,
            file_unique_id=photo.file_unique_id,
        )
    if not attached:
        # Row absent or has_image=0 — the admin hasn't saved the card yet.
        return web.json_response({"error": _ERR_SAVE_FIRST}, status=409)

    logger.info("web_question_image_attached", test_id=test_id, position=position)
    return web.json_response(
        {
            "ok": True,
            "preview_url": (
                f"/panel/tests/{test_id}/questions/{position}/image?v={photo.file_unique_id}"
            ),
        }
    )


@login_required
async def image_preview(request: web.Request) -> web.StreamResponse:
    """GET /panel/tests/{id}/questions/{pos}/image — proxy the Telegram photo."""
    container = request.app[KEY_CONTAINER]
    test_id = int(request.match_info["test_id"])
    position = int(request.match_info["position"])

    async with session_scope(container) as session:
        question = await container.services(session).test.get_question(test_id, position)

    if question is None or question.image_file_id is None:
        raise web.HTTPNotFound()

    etag = question.image_file_unique_id or question.image_file_id
    if request.headers.get("If-None-Match") == etag:
        return web.Response(status=304)

    buf = BytesIO()
    try:
        await container.bot.download(question.image_file_id, destination=buf)
    except TelegramAPIError:
        logger.warning("web_image_download_failed", test_id=test_id, position=position)
        raise web.HTTPNotFound() from None

    return web.Response(
        body=buf.getvalue(),
        content_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=86400", "ETag": etag},
    )


# ---------- image pipeline helpers ----------


class _GeometryError(Exception):
    """Image dimensions Telegram would reject."""


async def _read_upload(request: web.Request) -> bytes | None:
    """Pull the ``image`` part from the multipart body, capped at the size limit."""
    reader = await request.multipart()
    async for part in reader:
        if not isinstance(part, BodyPartReader) or part.name != "image":
            continue
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = await part.read_chunk(64 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > _MAX_IMAGE_BYTES:
                # Over cap — signal via an oversized sentinel so the caller 413s.
                return b"x" * (_MAX_IMAGE_BYTES + 1)
            chunks.append(chunk)
        return b"".join(chunks)
    return None


def _normalize_to_jpeg(data: bytes) -> bytes:
    """Validate and re-encode to an RGB JPEG Telegram will accept as a photo."""
    img: Image.Image = Image.open(BytesIO(data))
    img.load()
    if (img.format or "").upper() not in _ALLOWED_FORMATS:
        raise UnidentifiedImageError(f"unsupported format {img.format}")

    # Oversized dimensions are fixed by the thumbnail below; only an extreme
    # aspect ratio is unfixable (Telegram rejects ratio > 20).
    width, height = img.size
    if min(width, height) == 0 or max(width, height) / min(width, height) > _MAX_ASPECT_RATIO:
        raise _GeometryError()

    if img.mode not in ("RGB", "L"):
        # Flatten alpha onto white so PNGs with transparency look right.
        background = Image.new("RGB", img.size, (255, 255, 255))
        rgba = img.convert("RGBA")
        background.paste(rgba, mask=rgba.getchannel("A"))
        img = background
    elif img.mode == "L":
        img = img.convert("RGB")

    if max(img.size) > _RESIZE_LIMIT:
        img.thumbnail((_RESIZE_LIMIT, _RESIZE_LIMIT))

    out = BytesIO()
    img.convert("RGB").save(out, format="JPEG", quality=_JPEG_QUALITY)
    return out.getvalue()
