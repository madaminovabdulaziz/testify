"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-05-21

Creates every table defined in DATABASE_SPEC §5 and seeds the ``settings``
table with the canonical Russian copy from §8.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

# Revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Shared kwargs applied to every CREATE TABLE so every table is InnoDB +
# utf8mb4 / utf8mb4_unicode_ci (DATABASE_SPEC §1).
_INNODB_UTF8MB4: dict[str, str] = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}


def upgrade() -> None:
    # ---------- users ----------
    op.create_table(
        "users",
        sa.Column(
            "id",
            mysql.BIGINT(unsigned=True),
            autoincrement=True,
            primary_key=True,
        ),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(64), nullable=True),
        sa.Column("full_name", sa.String(200), nullable=True),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("reference_code", sa.CHAR(6), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="new"),
        sa.Column(
            "bot_blocked",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
        ),
        sa.Column(
            "updated_at",
            mysql.DATETIME(fsp=6),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)"),
        ),
        sa.Column("approved_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.UniqueConstraint("telegram_id", name="ux_users__telegram_id"),
        sa.UniqueConstraint("reference_code", name="ux_users__reference_code"),
        sa.CheckConstraint(
            "status IN ('new','onboarding_phone','onboarding_name','pending_payment',"
            "'pending_approval','rejected','approved','banned')",
            name="ck_users__status_enum",
        ),
        **_INNODB_UTF8MB4,
    )
    op.create_index("ix_users__phone", "users", ["phone"])
    op.create_index("ix_users__username", "users", ["username"])
    op.create_index("ix_users__status", "users", ["status"])

    # ---------- admins ----------
    op.create_table(
        "admins",
        sa.Column(
            "id",
            mysql.BIGINT(unsigned=True),
            autoincrement=True,
            primary_key=True,
        ),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column(
            "role",
            sa.String(16),
            nullable=False,
            server_default="moderator",
        ),
        sa.Column("added_by_admin_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column(
            "added_at",
            mysql.DATETIME(fsp=6),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
        ),
        sa.UniqueConstraint("telegram_id", name="ux_admins__telegram_id"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_admins__user_id",
        ),
        sa.ForeignKeyConstraint(
            ["added_by_admin_id"],
            ["admins.id"],
            ondelete="SET NULL",
            name="fk_admins__added_by",
        ),
        sa.CheckConstraint("role IN ('owner','moderator')", name="ck_admins__role_enum"),
        **_INNODB_UTF8MB4,
    )
    op.create_index("ix_admins__user_id", "admins", ["user_id"])

    # ---------- payment_receipts ----------
    op.create_table(
        "payment_receipts",
        sa.Column(
            "id",
            mysql.BIGINT(unsigned=True),
            autoincrement=True,
            primary_key=True,
        ),
        sa.Column("user_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("telegram_file_id", sa.String(256), nullable=False),
        sa.Column("telegram_file_unique_id", sa.String(64), nullable=False),
        # SIGNED 64-bit on purpose — asyncmy can't escape Python ints
        # larger than ``sys.maxsize``. The hasher reinterprets the
        # unsigned pHash as signed before insert; the bit pattern is
        # preserved, so duplicate detection is unaffected. Diverges
        # from DATABASE_SPEC §5.3 (BIGINT UNSIGNED) for driver
        # compatibility.
        sa.Column("image_phash", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("rejection_reason", sa.String(500), nullable=True),
        sa.Column("reviewed_by_admin_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("admin_notification_message_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
        ),
        sa.Column("reviewed_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name="fk_receipts__user_id",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_admin_id"],
            ["admins.id"],
            ondelete="SET NULL",
            name="fk_receipts__reviewed_by",
        ),
        sa.CheckConstraint(
            "status IN ('pending','approved','rejected')",
            name="ck_receipts__status_enum",
        ),
        sa.CheckConstraint(
            "status <> 'rejected' OR rejection_reason IS NOT NULL",
            name="ck_receipts__rejected_has_reason",
        ),
        # NOTE: the ``ck_receipts__reviewed_has_admin`` check from
        # DATABASE_SPEC §5.3 is intentionally omitted — MySQL 8.4
        # rejects a CHECK that references a column targeted by an
        # ``ON DELETE SET NULL`` FK (error 3823). The matching
        # invariant is enforced at the service layer by
        # ``ReceiptService.approve`` / ``reject``.
        **_INNODB_UTF8MB4,
    )
    op.create_index(
        "ix_receipts__user_id_status", "payment_receipts", ["user_id", "status"]
    )
    op.create_index(
        "ix_receipts__status_created", "payment_receipts", ["status", "created_at"]
    )
    op.create_index("ix_receipts__phash", "payment_receipts", ["image_phash"])

    # ---------- tests ----------
    op.create_table(
        "tests",
        sa.Column(
            "id",
            mysql.BIGINT(unsigned=True),
            autoincrement=True,
            primary_key=True,
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column(
            "duration_seconds",
            mysql.INTEGER(unsigned=True),
            nullable=False,
            server_default="3200",
        ),
        sa.Column("created_by_admin_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
        ),
        sa.Column("published_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("archived_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.ForeignKeyConstraint(
            ["created_by_admin_id"],
            ["admins.id"],
            ondelete="SET NULL",
            name="fk_tests__created_by",
        ),
        sa.CheckConstraint(
            "status IN ('draft','active','archived')",
            name="ck_tests__status_enum",
        ),
        sa.CheckConstraint(
            "duration_seconds > 0",
            name="ck_tests__duration_positive",
        ),
        sa.CheckConstraint(
            "status = 'draft' OR published_at IS NOT NULL",
            name="ck_tests__active_has_published_at",
        ),
        sa.CheckConstraint(
            "status <> 'archived' OR archived_at IS NOT NULL",
            name="ck_tests__archived_has_archived_at",
        ),
        **_INNODB_UTF8MB4,
    )
    op.create_index("ix_tests__status", "tests", ["status"])
    op.create_index("ix_tests__published_at", "tests", ["published_at"])

    # ---------- questions ----------
    op.create_table(
        "questions",
        sa.Column(
            "id",
            mysql.BIGINT(unsigned=True),
            autoincrement=True,
            primary_key=True,
        ),
        sa.Column("test_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("section", sa.String(16), nullable=False),
        sa.Column("position", mysql.TINYINT(unsigned=True), nullable=False),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("option_a", sa.String(500), nullable=False),
        sa.Column("option_b", sa.String(500), nullable=False),
        sa.Column("option_c", sa.String(500), nullable=False),
        sa.Column("option_d", sa.String(500), nullable=False),
        sa.Column("correct_option", sa.CHAR(1), nullable=False),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
        ),
        sa.ForeignKeyConstraint(
            ["test_id"],
            ["tests.id"],
            ondelete="CASCADE",
            name="fk_questions__test_id",
        ),
        sa.UniqueConstraint("test_id", "position", name="ux_questions__test_position"),
        sa.CheckConstraint(
            "section IN ('rus_tili','pedagogik','kasbiy')",
            name="ck_questions__section_enum",
        ),
        sa.CheckConstraint(
            "correct_option IN ('A','B','C','D')",
            name="ck_questions__correct_enum",
        ),
        sa.CheckConstraint(
            "position BETWEEN 1 AND 50",
            name="ck_questions__position_range",
        ),
        sa.CheckConstraint(
            "(section='rus_tili'  AND position BETWEEN 1  AND 35) OR "
            "(section='pedagogik' AND position BETWEEN 36 AND 45) OR "
            "(section='kasbiy'    AND position BETWEEN 46 AND 50)",
            name="ck_questions__section_position_consistent",
        ),
        **_INNODB_UTF8MB4,
    )

    # ---------- attempts ----------
    op.create_table(
        "attempts",
        sa.Column(
            "id",
            mysql.BIGINT(unsigned=True),
            autoincrement=True,
            primary_key=True,
        ),
        sa.Column("user_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("test_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="in_progress",
        ),
        sa.Column(
            "current_position",
            mysql.TINYINT(unsigned=True),
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "started_at",
            mysql.DATETIME(fsp=6),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
        ),
        sa.Column("finished_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("expires_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("score_total_correct", mysql.TINYINT(unsigned=True), nullable=True),
        sa.Column("score_rus_tili_correct", mysql.TINYINT(unsigned=True), nullable=True),
        sa.Column("score_pedagogik_correct", mysql.TINYINT(unsigned=True), nullable=True),
        sa.Column("score_kasbiy_correct", mysql.TINYINT(unsigned=True), nullable=True),
        sa.Column("warning_10min_sent_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("warning_5min_sent_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("warning_1min_sent_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name="fk_attempts__user_id",
        ),
        sa.ForeignKeyConstraint(
            ["test_id"],
            ["tests.id"],
            ondelete="RESTRICT",
            name="fk_attempts__test_id",
        ),
        sa.UniqueConstraint("user_id", "test_id", name="ux_attempts__user_test"),
        sa.CheckConstraint(
            "status IN ('in_progress','submitted','expired')",
            name="ck_attempts__status_enum",
        ),
        sa.CheckConstraint(
            "current_position BETWEEN 1 AND 50",
            name="ck_attempts__current_position_range",
        ),
        sa.CheckConstraint(
            "(status = 'in_progress' AND finished_at IS NULL) OR "
            "(status <> 'in_progress' AND finished_at IS NOT NULL)",
            name="ck_attempts__finished_consistent",
        ),
        sa.CheckConstraint(
            "status = 'in_progress' OR score_total_correct IS NOT NULL",
            name="ck_attempts__score_total_when_finished",
        ),
        sa.CheckConstraint(
            "(score_rus_tili_correct  IS NULL OR score_rus_tili_correct  BETWEEN 0 AND 35) AND "
            "(score_pedagogik_correct IS NULL OR score_pedagogik_correct BETWEEN 0 AND 10) AND "
            "(score_kasbiy_correct    IS NULL OR score_kasbiy_correct    BETWEEN 0 AND 5)  AND "
            "(score_total_correct     IS NULL OR score_total_correct     BETWEEN 0 AND 50)",
            name="ck_attempts__score_ranges",
        ),
        **_INNODB_UTF8MB4,
    )
    op.create_index("ix_attempts__status", "attempts", ["status"])
    op.create_index(
        "ix_attempts__test_score",
        "attempts",
        ["test_id", sa.text("score_total_correct DESC")],
    )
    op.create_index("ix_attempts__expires", "attempts", ["expires_at", "status"])

    # ---------- answers ----------
    op.create_table(
        "answers",
        sa.Column(
            "id",
            mysql.BIGINT(unsigned=True),
            autoincrement=True,
            primary_key=True,
        ),
        sa.Column("attempt_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("question_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("selected_option", sa.CHAR(1), nullable=False),
        sa.Column("is_correct", sa.Boolean(), nullable=False),
        sa.Column(
            "answered_at",
            mysql.DATETIME(fsp=6),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
        ),
        sa.Column(
            "updated_at",
            mysql.DATETIME(fsp=6),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)"),
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id"],
            ["attempts.id"],
            ondelete="CASCADE",
            name="fk_answers__attempt_id",
        ),
        sa.ForeignKeyConstraint(
            ["question_id"],
            ["questions.id"],
            ondelete="RESTRICT",
            name="fk_answers__question_id",
        ),
        sa.UniqueConstraint(
            "attempt_id",
            "question_id",
            name="ux_answers__attempt_question",
        ),
        sa.CheckConstraint(
            "selected_option IN ('A','B','C','D')",
            name="ck_answers__selected_enum",
        ),
        **_INNODB_UTF8MB4,
    )
    op.create_index(
        "ix_answers__question_is_correct", "answers", ["question_id", "is_correct"]
    )

    # ---------- settings ----------
    op.create_table(
        "settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("updated_by_admin_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column(
            "updated_at",
            mysql.DATETIME(fsp=6),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)"),
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_admin_id"],
            ["admins.id"],
            ondelete="SET NULL",
            name="fk_settings__updated_by",
        ),
        **_INNODB_UTF8MB4,
    )

    # ---------- seed settings ----------
    _seed_settings()


def _seed_settings() -> None:
    """Insert default settings rows.

    Verbatim transcription of DATABASE_SPEC §8. ``\\n`` escape sequences
    inside single-quoted SQL string literals are interpreted by MySQL as
    newlines at parse time, so the stored values contain real line breaks.
    A Python raw-string (``r``-prefixed) wraps the INSERT so Python does
    not eagerly convert those backslashes before MySQL sees them.
    """
    op.execute(
        r"""
        INSERT INTO settings (`key`, value, description) VALUES
        (
          'welcome_message',
          'Здравствуйте! 👋\n\nЭто бот для подготовки к аттестации учителей русского языка.\n\nЗдесь вы сможете:\n✅ Пройти полный пробный тест (50 вопросов)\n✅ Узнать свой балл и оценить готовность\n✅ Попасть в закрытый чат студентов, где преподаватель разбирает каждый тест\n\nСтруктура теста:\n📚 Русский язык — 35 вопросов\n👨‍🏫 Педагогическое мастерство — 10 вопросов\n📋 Профессиональный стандарт — 5 вопросов\n\n⏱ На весь тест отводится 53 минуты 20 секунд.\n\nЧтобы начать, нам нужно немного познакомиться.',
          'Первое сообщение пользователю при /start'
        ),
        (
          'payment_amount',
          '150000',
          'Сумма оплаты в сумах (только число)'
        ),
        (
          'payment_amount_display',
          '150 000 сум',
          'Сумма оплаты в формате для показа пользователю'
        ),
        (
          'payment_card_number',
          '8600 1234 5678 9012',
          'Номер карты для приёма платежей'
        ),
        (
          'payment_recipient_name',
          '[ИМЯ ПРЕПОДАВАТЕЛЯ]',
          'Имя получателя на карте'
        ),
        (
          'payment_instructions',
          'Чтобы получить доступ к тестам, оплатите подготовку:\n\n💰 Сумма: {amount_display}\n💳 Карта: {card_number}\n👤 Получатель: {recipient_name}\n\n📌 ВАЖНО: в комментарии к платежу укажите ваш код:\n#{reference_code}\n\nЭто поможет нам быстро найти ваш платёж.\n\nПосле оплаты нажмите кнопку ниже и отправьте скриншот чека.',
          'Инструкция по оплате (плейсхолдеры: {amount_display}, {card_number}, {recipient_name}, {reference_code})'
        ),
        (
          'group_invite_link',
          '',
          'Ссылка-приглашение в закрытый чат студентов'
        ),
        (
          'support_contact',
          '',
          'Username администратора для кнопки "У меня вопрос" (например, @username)'
        ),
        (
          'msg_receipt_accepted',
          '✅ Чек получен. Мы проверим его в ближайшее время и сообщим вам о решении.',
          'Сообщение пользователю после отправки чека'
        ),
        (
          'msg_approved',
          '🎉 Поздравляем! Ваш платёж подтверждён.\n\nВот ссылка на закрытый чат студентов:\n{group_invite_link}\n\nКогда преподаватель опубликует тест, вы получите уведомление, и сможете пройти его в этом боте.',
          'Сообщение пользователю при одобрении чека (плейсхолдер: {group_invite_link})'
        ),
        (
          'msg_rejected',
          '❌ К сожалению, ваш чек не был одобрен.\n\nПричина: {reason}\n\nВы можете отправить новый чек.',
          'Сообщение пользователю при отклонении чека (плейсхолдер: {reason})'
        ),
        (
          'msg_new_test_broadcast',
          '📢 Доступен новый тест!\n\nОткройте бота и нажмите «Пройти тест», чтобы начать.\n\n⏱ У вас будет 53 минуты 20 секунд.',
          'Рассылка студентам при публикации нового теста'
        ),
        (
          'msg_warning_10min',
          '⏱ Осталось 10 минут до конца теста.',
          'Предупреждение во время теста'
        ),
        (
          'msg_warning_5min',
          '⏱ Осталось 5 минут!',
          'Предупреждение во время теста'
        ),
        (
          'msg_warning_1min',
          '⏱ Осталась 1 минута!',
          'Предупреждение во время теста'
        ),
        (
          'msg_auto_submitted',
          '⏰ Время вышло. Тест автоматически завершён.',
          'Сообщение при автозавершении теста'
        ),
        (
          'msg_already_attempted',
          'Вы уже проходили этот тест.\n\nВаш результат: {score}/50',
          'Сообщение при повторной попытке (плейсхолдер: {score})'
        ),
        (
          'msg_no_active_test',
          'Сейчас нет доступных тестов. Преподаватель опубликует следующий — мы вам сообщим.',
          'Сообщение при отсутствии активного теста'
        ),
        (
          'msg_banned',
          'Доступ к боту ограничён.',
          'Сообщение заблокированному пользователю'
        );
        """
    )


def downgrade() -> None:
    # Drop child tables before their parents so foreign keys don't block the
    # drops. ``drop_table`` removes each table's own indexes + constraints, so
    # we must NOT drop them explicitly first — dropping the FK-backing index
    # ``ix_answers__question_is_correct`` (used by fk_answers__question_id)
    # ahead of the table fails with "needed in a foreign key constraint".
    op.drop_table("settings")
    op.drop_table("answers")
    op.drop_table("attempts")
    op.drop_table("questions")
    op.drop_table("tests")
    op.drop_table("payment_receipts")
    op.drop_table("admins")
    op.drop_table("users")
