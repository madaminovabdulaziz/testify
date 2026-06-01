"""Common handlers: ``/start``, ``/help``, ``/chat``, ``/chatid``, menu buttons.

``/start`` is the entry point — it routes by ``user.status`` per
PRODUCT_BLUEPRINT §10.1 + §13 ("``/start`` does not reset state after
onboarding starts"). The router is included **last** in the dispatcher
so its fallback only catches updates no other router handled.

The four menu reply-keyboard buttons (▶️ Пройти тест · 📜 Мои
результаты · 💬 Чат студентов · ❓ Помощь) live here too: tapping a
button sends its label as text, and ``F.text == LABEL`` filters route
it to the same handler that the corresponding slash command uses.
"""

from __future__ import annotations

import structlog
from aiogram import F, Router
from aiogram.filters import Command, CommandStart, or_f
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.handlers.test_taking import _enter_test_flow
from app.bot.keyboards.main_menu import main_menu_keyboard
from app.bot.keyboards.onboarding import contact_request_keyboard, welcome_keyboard
from app.bot.states.onboarding import OnboardingState
from app.bot.states.test_taking import TestState
from app.bot.views.history_screen import render_history_screen
from app.bot.views.payment_screen import render_payment_instructions
from app.core.container import Container
from app.core.i18n import BTN_MENU_CHAT, BTN_MENU_HELP, BTN_MENU_HISTORY
from app.models.user import User
from app.utils.text import html_escape

logger = structlog.get_logger()

router = Router(name="common")
# Student-facing handlers (/start, the payment screen, the menu buttons)
# must never fire in a group — otherwise typing /start in any group the
# bot sits in would leak the bank card number + recipient name (CODE_REVIEW
# H4). /chatid is the one diagnostic that must work in groups, so it lives
# on its own unfiltered router below.
router.message.filter(F.chat.type == "private")

# Unfiltered router for the /chatid setup diagnostic — must answer in
# groups/channels so the operator can read off ADMIN_GROUP_ID. Included
# separately in build_dispatcher().
chatid_router = Router(name="chatid")

# Hardcoded fallbacks if the settings table lookup misses (shouldn't
# happen since they're all seeded by the initial migration, but kept
# as a safety net per PRODUCT_BLUEPRINT §15.2 graceful-degradation).
_FALLBACK_WELCOME = "Здравствуйте! Нажмите «Начать», чтобы продолжить."
_FALLBACK_PHONE_PROMPT = "Чтобы продолжить, поделитесь, пожалуйста, своим номером телефона."
_FALLBACK_NAME_PROMPT = "Спасибо! Теперь напишите, пожалуйста, ваше полное имя (как в документе)."
_APPROVED_WELCOME_BACK = (
    "С возвращением! 👋\n\n"
    "Используйте кнопки ниже:\n"
    "▶️ <b>Пройти тест</b> — открыть актуальный тест\n"
    "📜 <b>Мои результаты</b> — история ваших попыток\n"
    "💬 <b>Чат студентов</b> — ссылка в закрытый чат\n"
    "❓ <b>Помощь</b> — справка"
)
_FALLBACK_PENDING_REVIEW = "Ваш чек на проверке. Мы сообщим, как только он будет одобрен."

_HELP_TEXT = (
    "ℹ️ <b>Справка</b>\n\n"
    "▶️ <b>Пройти тест</b> — начать актуальный тест (50 вопросов, 53 минуты).\n"
    "📜 <b>Мои результаты</b> — список ваших прошлых попыток с баллами.\n"
    "💬 <b>Чат студентов</b> — ссылка-приглашение в закрытый чат, где "
    "преподаватель разбирает каждый тест.\n"
    "❓ <b>Помощь</b> — это сообщение.\n\n"
    "Команды для опытных пользователей: /test, /chat, /start."
)


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    container: Container,
) -> None:
    """Route ``/start`` by ``user.status`` (PRODUCT_BLUEPRINT §10.1)."""
    services = container.services(session)
    settings_svc = services.settings

    status = user.status

    if status == "new":
        await state.clear()
        text = (await settings_svc.get("welcome_message")) or _FALLBACK_WELCOME
        await message.answer(text, reply_markup=welcome_keyboard())
        return

    if status == "onboarding_phone":
        await state.set_state(OnboardingState.waiting_for_phone)
        await message.answer(
            _FALLBACK_PHONE_PROMPT,
            reply_markup=contact_request_keyboard(),
        )
        return

    if status == "onboarding_name":
        await state.set_state(OnboardingState.waiting_for_name)
        await message.answer(_FALLBACK_NAME_PROMPT)
        return

    if status in ("pending_payment", "rejected"):
        await state.clear()
        rendered = await _build_payment_screen(user, services)
        await message.answer(
            rendered.text,
            reply_markup=rendered.reply_markup,
            parse_mode=rendered.parse_mode,
        )
        return

    if status == "pending_approval":
        await state.clear()
        await message.answer(_FALLBACK_PENDING_REVIEW)
        return

    if status == "approved":
        # PRODUCT_BLUEPRINT §13: /start must not reset a student who is
        # mid-test. If the FSM says they're taking a test, resume it instead
        # of clearing to the menu (CODE_REVIEW H5). enter_test_flow re-reads
        # the DB, so a stale FSM (attempt already expired) cleanly falls
        # through to the prior-result / pretest screens.
        current = await state.get_state()
        if current in (TestState.in_progress.state, TestState.confirming_finish.state):
            await _enter_test_flow(
                message=message,
                state=state,
                session=session,
                user=user,
                container=container,
            )
            return
        await state.clear()
        await message.answer(
            _APPROVED_WELCOME_BACK,
            reply_markup=main_menu_keyboard(),
            parse_mode="HTML",
        )
        return

    # "banned" never reaches here — middleware short-circuits.
    logger.warning("cmd_start_unknown_status", status=status, user_id=user.id)
    await state.clear()


@router.message(or_f(Command("help"), F.text == BTN_MENU_HELP))
async def cmd_help(message: Message) -> None:
    """``/help`` or «❓ Помощь» button."""
    await message.answer(_HELP_TEXT, parse_mode="HTML")


@router.message(or_f(Command("chat"), F.text == BTN_MENU_CHAT))
async def cmd_chat(
    message: Message,
    session: AsyncSession,
    user: User,
    container: Container,
) -> None:
    """``/chat`` or «💬 Чат студентов» — re-share the closed group invite link (§8.7)."""
    if user.status != "approved":
        await message.answer("Ссылка на чат доступна только после одобрения оплаты.")
        return
    services = container.services(session)
    link = (await services.settings.get("group_invite_link")) or ""
    if not link:
        await message.answer("Ссылка на чат пока не настроена. Обратитесь к преподавателю.")
        return
    # Bot sends with parse_mode=HTML globally — escape the link (N7).
    await message.answer(f"💬 Ссылка на чат студентов:\n{html_escape(link)}")


@router.message(F.text == BTN_MENU_HISTORY)
async def cmd_history(
    message: Message,
    session: AsyncSession,
    user: User,
    container: Container,
) -> None:
    """«📜 Мои результаты» — list this user's past finished attempts."""
    if user.status != "approved":
        # Clearer for users who haven't been approved yet — they can't take a
        # test, so "после первой попытки" was misleading (CODE_REVIEW N8).
        await message.answer(
            "Результаты появятся после одобрения оплаты и первого пройденного теста."
        )
        return
    services = container.services(session)
    entries = await services.attempt.list_finished_for_user(user.id)
    await message.answer(render_history_screen(entries), parse_mode="HTML")


@chatid_router.message(Command("chatid"))
async def cmd_chatid(message: Message) -> None:
    """Diagnostic: reply with the current chat's ID.

    Works in DM, groups, and channels — no admin / approval gating, so
    the operator can drop the bot into a new chat and immediately learn
    its ID for ``ADMIN_GROUP_ID``. Commands always reach the bot even
    when group-privacy mode is on.
    """
    chat = message.chat
    await message.answer(
        f"<b>Chat ID:</b> <code>{chat.id}</code>\n"
        f"<b>Type:</b> <code>{chat.type}</code>\n"
        f"<b>Title:</b> {chat.title or '—'}\n\n"
        f"For ADMIN_GROUP_ID in .env, copy: <code>{chat.id}</code>",
        parse_mode="HTML",
    )


@router.message()
async def fallback_message(message: Message) -> None:
    """Catch-all for any message no other router handled."""
    await message.answer("Не понимаю эту команду. Напишите /start, чтобы продолжить.")


async def _build_payment_screen(user: User, services: object):
    """Pull the live settings + render :func:`render_payment_instructions`."""
    settings_svc = services.settings  # type: ignore[attr-defined]
    template = (await settings_svc.get("payment_instructions")) or ""
    amount_display = (await settings_svc.get("payment_amount_display")) or ""
    card_number = (await settings_svc.get("payment_card_number")) or ""
    recipient_name = (await settings_svc.get("payment_recipient_name")) or ""
    support_contact = await settings_svc.get("support_contact")

    return render_payment_instructions(
        user,
        instructions_template=template,
        amount_display=amount_display,
        card_number=card_number,
        recipient_name=recipient_name,
        support_contact=support_contact or None,
    )
