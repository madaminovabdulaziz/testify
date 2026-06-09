"""Pure rendering helpers for admin /stats /find /leaderboard /attempt output.

Both the slash-command handlers (:mod:`app.bot.handlers.admin.operations`)
and the admin-panel button handlers (:mod:`app.bot.handlers.admin.panel`)
import these so the two entry points produce identical output. The
functions are pure: every input is a DTO; no DB, no Telegram, no
services.
"""

from __future__ import annotations

from app.models.user import User
from app.repositories.attempt_repository import LeaderboardEntry
from app.repositories.test_repository import TestListEntry
from app.services.attempt_service import AttemptDetail
from app.services.stats_service import StatsSnapshot
from app.utils.datetime import format_timestamp_local
from app.utils.text import html_escape

# Icon + Russian label per test status, for the «🗂 Тесты» list.
_TEST_STATUS_LABEL: dict[str, tuple[str, str]] = {
    "active": ("🟢", "активный"),
    "archived": ("📦", "архив"),
    "draft": ("📝", "черновик"),
}


def render_stats(snapshot: StatsSnapshot) -> str:
    """Single multi-line HTML block for the eight stat counters."""
    users = snapshot.users_by_status
    receipts = snapshot.receipts_by_status
    tests = snapshot.tests_by_status
    attempts = snapshot.attempts_by_status

    def _n(d: dict[str, int], key: str) -> int:
        return int(d.get(key, 0))

    finished_attempts = _n(attempts, "submitted") + _n(attempts, "expired")

    lines = [
        "📊 <b>Статистика бота</b>",
        "",
        "<b>Пользователи</b>",
        f"  всего: {snapshot.total_users}",
        f"  одобрено: {_n(users, 'approved')}",
        f"  на проверке: {_n(users, 'pending_approval')}",
        f"  ждёт оплаты: {_n(users, 'pending_payment')}",
        f"  отклонено: {_n(users, 'rejected')}",
        f"  забанено: {_n(users, 'banned')}",
        "",
        "<b>Чеки</b>",
        f"  на проверке: {_n(receipts, 'pending')}",
        f"  одобрено: {_n(receipts, 'approved')}",
        f"  отклонено: {_n(receipts, 'rejected')}",
        "",
        "<b>Тесты</b>",
        f"  активный: {_n(tests, 'active')}",
        f"  архив: {_n(tests, 'archived')}",
        f"  черновики: {_n(tests, 'draft')}",
        "",
        "<b>Попытки</b>",
        f"  завершено: {finished_attempts}",
        f"  в процессе: {_n(attempts, 'in_progress')}",
    ]
    return "\n".join(lines)


def render_user_card(found_user: User, *, pending_count: int) -> str:
    """Compact admin card with identifying fields + receipt count."""
    return "\n".join(
        [
            f"👤 <b>{html_escape(found_user.full_name) if found_user.full_name else '—'}</b>",
            f"id: <code>{found_user.id}</code>",
            f"telegram: <code>{found_user.telegram_id}</code>",
            f"username: {('@' + html_escape(found_user.username)) if found_user.username else '—'}",
            f"телефон: {html_escape(found_user.phone) if found_user.phone else '—'}",
            f"код: #{html_escape(found_user.reference_code) if found_user.reference_code else '—'}",
            f"статус: <code>{html_escape(found_user.status)}</code>",
            f"бот заблокирован: {'да' if found_user.bot_blocked else 'нет'}",
            f"чеки на проверке: {pending_count}",
        ]
    )


def render_test_list(entries: list[TestListEntry]) -> str:
    """Recent-tests list — the admin's discovery surface for ``test_id``.

    Each row leads with ``#<id>`` so the teacher can read it off and chain
    into ``/leaderboard <id>`` (and from there into ``/attempt <id>``). Counts
    use the colon form ("вопросов: 50") to sidestep Russian numeral agreement.
    """
    if not entries:
        return (
            "🗂 <b>Тесты</b>\n\n"
            "Пока нет ни одного теста. Загрузите первый через «📋 Загрузить тест»."
        )

    lines = [f"🗂 <b>Тесты</b> (последние {len(entries)})", ""]
    for entry in entries:
        icon, label = _TEST_STATUS_LABEL.get(entry.status, ("•", html_escape(entry.status)))
        lines.append(f"{icon} <b>#{entry.id}</b> · {html_escape(entry.title)}")
        lines.append(
            f"   {label} · вопросов: {entry.question_count} · попыток: {entry.attempt_count}"
        )
    lines.append("")
    lines.append(
        "Дальше: /leaderboard &lt;id&gt; — результаты, /attempt &lt;id&gt; — детали попытки."
    )
    return "\n".join(lines)


def render_leaderboard(*, test_title: str, entries: list[LeaderboardEntry]) -> str:
    """Top-N leaderboard table for one test.

    The ``попытка`` column carries each row's ``attempt_id`` so the admin can
    chain straight into ``/attempt <id>`` for a per-question breakdown.
    """
    if not entries:
        return f"🏆 <b>{html_escape(test_title)}</b>\n\nПока нет завершённых попыток."

    rows = ["#  балл  попытка  имя"]
    for rank, entry in enumerate(entries, start=1):
        name = entry.full_name or f"user_{entry.user_id}"
        # Truncate to keep rows narrow (tighter now that the id column is here).
        if len(name) > 20:
            name = name[:19] + "…"
        rows.append(f"{rank:>2} {entry.score_total_correct:>4}  {entry.attempt_id:>7}  {name}")

    return "\n".join(
        [
            f"🏆 <b>{html_escape(test_title)}</b> (топ {len(entries)})",
            "",
            "<pre>" + html_escape("\n".join(rows)) + "</pre>",
            "",
            "Детали попытки: /attempt &lt;номер попытки&gt;",
        ]
    )


def render_attempt_detail(
    detail: AttemptDetail,
    *,
    owner: User | None,
    test_title: str | None,
) -> str:
    """Full per-question breakdown for one attempt."""
    attempt = detail.attempt
    started_at = format_timestamp_local(attempt.started_at)
    finished_at = format_timestamp_local(attempt.finished_at) if attempt.finished_at else "—"
    owner_label = (
        f"{html_escape(owner.full_name) if owner and owner.full_name else '—'} "
        f"(id={attempt.user_id})"
    )
    test_label = (
        f"{html_escape(test_title)} (id={attempt.test_id})"
        if test_title
        else f"id={attempt.test_id}"
    )

    # Per-question summary table: position · selected · correct · ✓/✗
    answers = detail.answers_by_question_id
    rows = ["№  picked correct mark"]
    for question in detail.questions:
        ans = answers.get(question.id)
        selected = ans.selected_option if ans else "—"
        mark = "✓" if ans and ans.is_correct else ("·" if ans is None else "✗")
        rows.append(f"{question.position:>2}  {selected:>6}  {question.correct_option:>7}  {mark}")

    score_total = attempt.score_total_correct
    score_line = f"балл: {score_total}/50" if score_total is not None else "балл: ещё не подсчитан"
    return "\n".join(
        [
            f"🔍 <b>Попытка #{attempt.id}</b>",
            f"статус: <code>{html_escape(attempt.status)}</code>",
            f"пользователь: {owner_label}",
            f"тест: {test_label}",
            f"начало: {started_at}",
            f"конец: {finished_at}",
            score_line,
            "",
            "<pre>" + html_escape("\n".join(rows)) + "</pre>",
        ]
    )


def parse_int_arg(raw: str | None) -> int | None:
    """Return the int value of the first whitespace-split token, or ``None``."""
    if not raw:
        return None
    token = raw.split()[0]
    try:
        return int(token)
    except ValueError:
        return None
