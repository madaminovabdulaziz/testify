"""The teacher's admin panel — `/admin` opens a reply-keyboard with 10 actions.

PRODUCT_BLUEPRINT §8.9 surfaces the same admin commands via *buttons*
instead of slash-commands, because the teacher (Dilnoza opa per the
blueprint personas) isn't a developer — she taps, she doesn't type
``/leaderboard 12``.

Architecture:

* The panel is a persistent ``ReplyKeyboardMarkup`` (3×3 + 1) that
  appears in place of the student menu when /admin is invoked.
* Zero-arg buttons (📊 Статистика, ⚙️ Настройки, 📤 Шаблон Excel,
  📋 Загрузить тест) directly call the same service the slash-command
  handlers do, rendering via :mod:`app.bot.views.admin_ops`.
* Args-taking buttons (🔍 Найти, 🚫 Забанить, ✅ Разбанить, 🏆 Лидерборд,
  🔎 Детали попытки) set an :class:`AdminPanelState` FSM state and
  swap to a single-button "↩️ Отменить" keyboard while we wait for the
  admin's text input. The next message (or "Cancel" tap) finishes the
  flow and restores the panel.
* "🔙 Закрыть админ-панель" removes the keyboard entirely.

All handlers are gated by :class:`AdminOnly` at the router level —
non-admins fall through to other routers and never see the panel.
"""

from __future__ import annotations

import structlog
from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import FSInputFile, Message, ReplyKeyboardRemove
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters.admin_only import AdminOnly
from app.bot.handlers.admin.tests import _TEMPLATE_PATH
from app.bot.keyboards.admin_panel import admin_cancel_keyboard, admin_panel_keyboard
from app.bot.states.admin import AdminPanelState, AdminTestUploadState
from app.bot.views.admin_ops import (
    parse_int_arg,
    render_attempt_detail,
    render_leaderboard,
    render_stats,
    render_test_list,
    render_user_card,
)
from app.core.container import Container
from app.core.i18n import (
    BTN_ADMIN_ATTEMPT,
    BTN_ADMIN_BAN,
    BTN_ADMIN_CANCEL,
    BTN_ADMIN_CLOSE,
    BTN_ADMIN_FIND,
    BTN_ADMIN_LEADERBOARD,
    BTN_ADMIN_SETTINGS,
    BTN_ADMIN_STATS,
    BTN_ADMIN_TEMPLATE,
    BTN_ADMIN_TESTS,
    BTN_ADMIN_UNBAN,
    BTN_ADMIN_UPLOAD_TEST,
)
from app.models.user import User
from app.utils.text import html_escape

logger = structlog.get_logger()

router = Router(name="admin_panel")

# Every handler in this router is admin-only. Non-admins fall through
# to the student menu / common fallback as if the panel doesn't exist.
router.message.filter(AdminOnly())

_PANEL_INTRO = "⚙️ <b>Админ-панель</b>\n\nВыберите действие из меню ниже."
_PANEL_CLOSED = "Админ-панель закрыта."
_CANCELLED = "Действие отменено."
_USER_NOT_FOUND = "Пользователь не найден."
_TEST_NOT_FOUND = "Тест не найден."
_ATTEMPT_NOT_FOUND = "Попытка не найдена."
_PROMPT_FIND = "🔍 Введите телефон, username или код:"
_PROMPT_BAN = "🚫 Введите user_id для бана:"
_PROMPT_UNBAN = "✅ Введите user_id для разбана:"
_PROMPT_LEADERBOARD = "🏆 Введите test_id:"
_PROMPT_ATTEMPT = "🔎 Введите attempt_id:"
_INVALID_INT = "Нужно целое число. Попробуйте ещё раз или нажмите «↩️ Отменить»."
_UPLOAD_PROMPT = "Отправьте файл Excel с тестом. Шаблон: /template"
_TEMPLATE_UNAVAILABLE = "Шаблон временно недоступен. Сообщите администратору."


# ============================================================
# /admin — open the panel
# ============================================================


@router.message(Command("admin"))
async def cmd_admin_panel(message: Message, state: FSMContext) -> None:
    """Open the admin panel — clears any in-flight FSM, shows the keyboard."""
    await state.clear()
    await message.answer(_PANEL_INTRO, parse_mode="HTML", reply_markup=admin_panel_keyboard())


@router.message(F.text == BTN_ADMIN_CLOSE)
async def panel_close(message: Message, state: FSMContext) -> None:
    """Remove the admin keyboard and clear any pending FSM input."""
    await state.clear()
    await message.answer(_PANEL_CLOSED, reply_markup=ReplyKeyboardRemove())


# ============================================================
# Global cancel — works in any AdminPanelState
# ============================================================


@router.message(StateFilter(AdminPanelState), F.text == BTN_ADMIN_CANCEL)
async def panel_cancel(message: Message, state: FSMContext) -> None:
    """Abort the current input-collecting flow and return to the panel."""
    await state.clear()
    await message.answer(_CANCELLED, reply_markup=admin_panel_keyboard())


# ============================================================
# Zero-arg buttons
# ============================================================


@router.message(F.text == BTN_ADMIN_STATS)
async def panel_stats(
    message: Message,
    session: AsyncSession,
    user: User,
    container: Container,
) -> None:
    services = container.services(session)
    snapshot = await services.stats.snapshot()
    await message.answer(render_stats(snapshot), parse_mode="HTML")


@router.message(F.text == BTN_ADMIN_TESTS)
async def panel_tests(
    message: Message,
    session: AsyncSession,
    user: User,
    container: Container,
) -> None:
    """Mirrors /tests — list recent tests with ids for leaderboard/attempt lookup."""
    services = container.services(session)
    entries = await services.test.list_recent(limit=15)
    await message.answer(render_test_list(entries), parse_mode="HTML")


@router.message(F.text == BTN_ADMIN_SETTINGS)
async def panel_settings(
    message: Message,
    session: AsyncSession,
    user: User,
    container: Container,
) -> None:
    """Mirrors /settings — list all keys with one-line previews."""
    from app.bot.handlers.admin.settings import _render_settings_list

    services = container.services(session)
    all_settings = await services.settings.get_all()
    await message.answer(_render_settings_list(all_settings), parse_mode="HTML")


@router.message(F.text == BTN_ADMIN_TEMPLATE)
async def panel_template(message: Message) -> None:
    """Mirrors /template — send the bundled .xlsx skeleton."""
    if not _TEMPLATE_PATH.exists():
        logger.error("template_file_missing", path=str(_TEMPLATE_PATH))
        await message.answer(_TEMPLATE_UNAVAILABLE)
        return
    await message.answer_document(
        document=FSInputFile(_TEMPLATE_PATH, filename="template.xlsx"),
        caption="Шаблон Excel. Заполните и отправьте через «📋 Загрузить тест».",
    )


@router.message(F.text == BTN_ADMIN_UPLOAD_TEST)
async def panel_upload_test(message: Message, state: FSMContext) -> None:
    """Open the upload flow — same FSM as /upload_test."""
    await state.set_state(AdminTestUploadState.waiting_for_file)
    # Show the cancel keyboard like the other panel steps so the admin isn't
    # stranded in waiting_for_file with no way out (CODE_REVIEW L17).
    await message.answer(_UPLOAD_PROMPT, reply_markup=admin_cancel_keyboard())


# ============================================================
# Args-taking buttons — step 1: prompt + set FSM state
# ============================================================


@router.message(F.text == BTN_ADMIN_FIND)
async def panel_find_start(message: Message, state: FSMContext) -> None:
    await state.set_state(AdminPanelState.waiting_for_find_query)
    await message.answer(_PROMPT_FIND, reply_markup=admin_cancel_keyboard())


@router.message(F.text == BTN_ADMIN_BAN)
async def panel_ban_start(message: Message, state: FSMContext) -> None:
    await state.set_state(AdminPanelState.waiting_for_ban_id)
    await message.answer(_PROMPT_BAN, reply_markup=admin_cancel_keyboard())


@router.message(F.text == BTN_ADMIN_UNBAN)
async def panel_unban_start(message: Message, state: FSMContext) -> None:
    await state.set_state(AdminPanelState.waiting_for_unban_id)
    await message.answer(_PROMPT_UNBAN, reply_markup=admin_cancel_keyboard())


@router.message(F.text == BTN_ADMIN_LEADERBOARD)
async def panel_leaderboard_start(message: Message, state: FSMContext) -> None:
    await state.set_state(AdminPanelState.waiting_for_leaderboard_id)
    await message.answer(_PROMPT_LEADERBOARD, reply_markup=admin_cancel_keyboard())


@router.message(F.text == BTN_ADMIN_ATTEMPT)
async def panel_attempt_start(message: Message, state: FSMContext) -> None:
    await state.set_state(AdminPanelState.waiting_for_attempt_id)
    await message.answer(_PROMPT_ATTEMPT, reply_markup=admin_cancel_keyboard())


# ============================================================
# Args-taking buttons — step 2: consume the input
# ============================================================


@router.message(StateFilter(AdminPanelState.waiting_for_find_query))
async def panel_find_run(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    container: Container,
) -> None:
    query = (message.text or "").strip()
    if not query:
        await message.answer(_PROMPT_FIND)
        return
    await state.clear()

    services = container.services(session)
    found = await services.user.find(query)
    if found is None:
        await message.answer(_USER_NOT_FOUND, reply_markup=admin_panel_keyboard())
        return

    pending_count = await services.receipt.count_pending_for_user(found.id)
    await message.answer(
        render_user_card(found, pending_count=pending_count),
        parse_mode="HTML",
        reply_markup=admin_panel_keyboard(),
    )


@router.message(StateFilter(AdminPanelState.waiting_for_ban_id))
async def panel_ban_run(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    container: Container,
) -> None:
    target_id = parse_int_arg(message.text)
    if target_id is None:
        await message.answer(_INVALID_INT)
        return
    await state.clear()

    services = container.services(session)
    target = await services.user.get_user(target_id)
    if target is None:
        await message.answer(_USER_NOT_FOUND, reply_markup=admin_panel_keyboard())
        return

    await services.user.ban(target_id)
    logger.info(
        "admin_banned_user",
        admin_telegram_id=message.from_user.id,
        user_id=target_id,
    )
    await message.answer(
        f"🚫 Пользователь <code>{target_id}</code> забанен.",
        parse_mode="HTML",
        reply_markup=admin_panel_keyboard(),
    )


@router.message(StateFilter(AdminPanelState.waiting_for_unban_id))
async def panel_unban_run(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    container: Container,
) -> None:
    target_id = parse_int_arg(message.text)
    if target_id is None:
        await message.answer(_INVALID_INT)
        return
    await state.clear()

    services = container.services(session)
    target = await services.user.get_user(target_id)
    if target is None:
        await message.answer(_USER_NOT_FOUND, reply_markup=admin_panel_keyboard())
        return
    if target.status != "banned":
        await message.answer(
            f"Пользователь <code>{target_id}</code> не в бане "
            f"(статус: <code>{html_escape(target.status)}</code>).",
            parse_mode="HTML",
            reply_markup=admin_panel_keyboard(),
        )
        return

    await services.user.unban(target_id)
    logger.info(
        "admin_unbanned_user",
        admin_telegram_id=message.from_user.id,
        user_id=target_id,
    )
    await message.answer(
        f"✅ Пользователь <code>{target_id}</code> восстановлен (approved).",
        parse_mode="HTML",
        reply_markup=admin_panel_keyboard(),
    )


@router.message(StateFilter(AdminPanelState.waiting_for_leaderboard_id))
async def panel_leaderboard_run(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    container: Container,
) -> None:
    test_id = parse_int_arg(message.text)
    if test_id is None:
        await message.answer(_INVALID_INT)
        return
    await state.clear()

    services = container.services(session)
    test = await services.test.get_test(test_id)
    if test is None:
        await message.answer(_TEST_NOT_FOUND, reply_markup=admin_panel_keyboard())
        return

    entries = await services.attempt.list_top_for_test(test_id, limit=20)
    await message.answer(
        render_leaderboard(test_title=test.title, entries=entries),
        parse_mode="HTML",
        reply_markup=admin_panel_keyboard(),
    )


@router.message(StateFilter(AdminPanelState.waiting_for_attempt_id))
async def panel_attempt_run(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    container: Container,
) -> None:
    attempt_id = parse_int_arg(message.text)
    if attempt_id is None:
        await message.answer(_INVALID_INT)
        return
    await state.clear()

    services = container.services(session)
    detail = await services.attempt.get_attempt_detail(attempt_id)
    if detail is None:
        await message.answer(_ATTEMPT_NOT_FOUND, reply_markup=admin_panel_keyboard())
        return

    target_user = await services.user.get_user(detail.attempt.user_id)
    test = await services.test.get_test(detail.attempt.test_id)
    await message.answer(
        render_attempt_detail(detail, owner=target_user, test_title=test.title if test else None),
        parse_mode="HTML",
        reply_markup=admin_panel_keyboard(),
    )
