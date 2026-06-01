"""Short Russian UI strings used directly in code.

These are the labels that appear inside ``InlineKeyboardButton.text``
and the few other places where loading from the DB on every render would
be wasteful. They are intentionally short and stable — anything longer
than a button label, or that the teacher might want to tweak per cohort,
lives in the ``settings`` table instead (see
:class:`app.services.settings_service.SettingsService`).

Editing one of these constants requires a redeploy. That's the trade-off
we accept for not paying a DB round-trip on every keyboard render.
"""

from __future__ import annotations

from typing import Final

# ---------- Onboarding ----------
BTN_START_ONBOARDING: Final[str] = "Начать ▶️"
BTN_SHARE_PHONE: Final[str] = "📱 Поделиться номером"

# ---------- Payment ----------
BTN_I_PAID: Final[str] = "💳 Я оплатил, отправить чек"
BTN_HAVE_QUESTION: Final[str] = "❓ У меня вопрос"

# ---------- Test entry ----------
BTN_TAKE_TEST: Final[str] = "▶️ Начать тест"
BTN_CANCEL: Final[str] = "🔙 Назад"

# ---------- Test taking ----------
BTN_BACK: Final[str] = "⬅️ Назад"
BTN_FORWARD: Final[str] = "Вперёд ➡️"
BTN_FINISH_TEST: Final[str] = "🏁 Завершить тест"
BTN_CONFIRM_FINISH: Final[str] = "✅ Да, завершить"
BTN_CONTINUE_TEST: Final[str] = "↩️ Продолжить"

# ---------- Results ----------
BTN_GO_TO_CHAT: Final[str] = "💬 Перейти в чат"

# ---------- Main menu (persistent reply keyboard for approved students) ----------
BTN_MENU_TAKE_TEST: Final[str] = "▶️ Пройти тест"
BTN_MENU_HISTORY: Final[str] = "📜 Мои результаты"
BTN_MENU_CHAT: Final[str] = "💬 Чат студентов"
BTN_MENU_HELP: Final[str] = "❓ Помощь"

# ---------- Admin panel (reply keyboard after /admin) ----------
BTN_ADMIN_STATS: Final[str] = "📊 Статистика"
BTN_ADMIN_UPLOAD_TEST: Final[str] = "📋 Загрузить тест"
BTN_ADMIN_SETTINGS: Final[str] = "⚙️ Настройки"
BTN_ADMIN_FIND: Final[str] = "🔍 Найти ученика"
BTN_ADMIN_LEADERBOARD: Final[str] = "🏆 Лидерборд"
BTN_ADMIN_ATTEMPT: Final[str] = "🔎 Детали попытки"
BTN_ADMIN_BAN: Final[str] = "🚫 Забанить"
BTN_ADMIN_UNBAN: Final[str] = "✅ Разбанить"
BTN_ADMIN_TEMPLATE: Final[str] = "📤 Шаблон Excel"
BTN_ADMIN_CLOSE: Final[str] = "🔙 Закрыть админ-панель"
BTN_ADMIN_CANCEL: Final[str] = "↩️ Отменить"

# ---------- Admin: receipt review ----------
BTN_APPROVE: Final[str] = "✅ Одобрить"
BTN_REJECT: Final[str] = "❌ Отклонить"

# ---------- Admin: test publish ----------
BTN_PUBLISH_NOTIFY: Final[str] = "📢 Опубликовать с уведомлением"
BTN_PUBLISH_SILENT: Final[str] = "📤 Опубликовать тихо"
BTN_PUBLISH_CANCEL: Final[str] = "🗑 Отменить"
BTN_RENAME: Final[str] = "✏️ Изменить название"

# ---------- Section labels (the test screen legend) ----------
# These show up in both the test-screen header text and the result-screen
# breakdown, so they live here rather than being repeated in each view.
SECTION_LABEL_RUS_TILI: Final[str] = "Русский язык"
SECTION_LABEL_PEDAGOGIK: Final[str] = "Педагогическое мастерство"
SECTION_LABEL_KASBIY: Final[str] = "Профессиональный стандарт"

# Maps the DB ``section`` enum value to its display label.
SECTION_LABELS: Final[dict[str, str]] = {
    "rus_tili": SECTION_LABEL_RUS_TILI,
    "pedagogik": SECTION_LABEL_PEDAGOGIK,
    "kasbiy": SECTION_LABEL_KASBIY,
}
