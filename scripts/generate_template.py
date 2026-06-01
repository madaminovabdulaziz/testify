"""Generate ``app/static/template.xlsx`` — the file the bot ships to admins via ``/template``.

The example rows are intentionally synthetic so the published template
does not reproduce any copyrighted DTM question (CLAUDE.md forbids that
explicitly). Five rows are enough to show the column conventions for
each of the three sections.

Run with::

    python -m scripts.generate_template
"""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

_HEADERS: list[str] = [
    "section",
    "position",
    "question_text",
    "option_a",
    "option_b",
    "option_c",
    "option_d",
    "correct_option",
    "has_image",
]

# Five synthetic example rows — one from rus_tili, two from pedagogik,
# two from kasbiy is uneven; we use 3/1/1 so admins see what a rus_tili
# entry looks like (since that's the bulk of the test).
#
# ``has_image`` is the optional last column: leave it "нет" (or blank) for a
# plain text question; set it "да" when the question needs a table / chart /
# diagram and the bot will ask for the photo after upload.
_EXAMPLE_ROWS: list[tuple[str, int, str, str, str, str, str, str, str]] = [
    (
        "rus_tili",
        1,
        "Какой синоним к слову «красивый»?",
        "Прекрасный",
        "Грубый",
        "Холодный",
        "Громкий",
        "A",
        "нет",
    ),
    (
        "rus_tili",
        2,
        "Сколько падежей в современном русском языке?",
        "Четыре",
        "Шесть",
        "Восемь",
        "Десять",
        "B",
        "нет",
    ),
    (
        "rus_tili",
        3,
        "Какой из глаголов относится к первому спряжению?",
        "Видеть",
        "Слышать",
        "Читать",
        "Держать",
        "C",
        "нет",
    ),
    (
        "pedagogik",
        36,
        "Что относится к активным методам обучения?",
        "Лекция",
        "Дискуссия",
        "Чтение учебника",
        "Просмотр презентации",
        "B",
        "нет",
    ),
    (
        "kasbiy",
        46,
        "Кем утверждается профессиональный стандарт педагога?",
        "Школьной администрацией",
        "Министерством образования",
        "Родительским комитетом",
        "Профсоюзом",
        "B",
        "нет",
    ),
]


def main() -> None:
    project_root = Path(__file__).resolve().parent.parent
    out_path = project_root / "app" / "static" / "template.xlsx"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Questions"
    sheet.append(_HEADERS)
    for row in _EXAMPLE_ROWS:
        sheet.append(list(row))

    # Tidy column widths so the file is readable when the teacher opens it.
    for column_letter, width in zip(
        ["A", "B", "C", "D", "E", "F", "G", "H", "I"],
        [12, 10, 60, 22, 22, 22, 22, 16, 12],
        strict=True,
    ):
        sheet.column_dimensions[column_letter].width = width

    workbook.save(out_path)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
