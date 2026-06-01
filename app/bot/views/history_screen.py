"""Render the «Мои результаты» history list for one student.

Pure view: handler passes the list of ``(Attempt, Test)`` pairs from
:meth:`AttemptService.list_finished_for_user`; this function turns it
into the HTML message body the user sees when they tap the «📜 Мои
результаты» menu button.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.models.attempt import Attempt
from app.models.test import Test
from app.utils.datetime import format_timestamp_local
from app.utils.text import html_escape

_EMPTY_TEXT = (
    "📜 <b>Мои результаты</b>\n\n"
    "У вас пока нет завершённых попыток.\n"
    "Нажмите «▶️ Пройти тест», чтобы начать."
)


def render_history_screen(entries: Iterable[tuple[Attempt, Test]]) -> str:
    """Build the HTML body for the «Мои результаты» response."""
    items = list(entries)
    if not items:
        return _EMPTY_TEXT

    lines: list[str] = ["📜 <b>Мои результаты</b>", ""]
    for attempt, test in items:
        score = int(attempt.score_total_correct or 0)
        percent = round(score / 50 * 100, 1)
        title = html_escape(test.title or "—")
        finished = (
            format_timestamp_local(attempt.finished_at) if attempt.finished_at is not None else "—"
        )
        status_label = "✅" if attempt.status == "submitted" else "⏰"
        lines.append(f"{status_label} <b>{title}</b>")
        lines.append(f"   Балл: <b>{score}/50</b> ({percent}%)")
        lines.append(f"   Завершён: {finished}")
        lines.append("")

    # Drop the trailing blank line for a tighter render.
    return "\n".join(lines).rstrip()
