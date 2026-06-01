"""Repository layer: SQLAlchemy queries shaped to the domain."""

from app.repositories.admin_repository import AdminRepository
from app.repositories.answer_repository import AnswerRepository, QuestionStats
from app.repositories.attempt_repository import (
    AttemptRepository,
    AttemptScores,
    LeaderboardEntry,
    WarningSlot,
)
from app.repositories.base import BaseRepository
from app.repositories.question_repository import QuestionDraft, QuestionRepository
from app.repositories.receipt_repository import ReceiptRepository
from app.repositories.settings_repository import SettingsRepository
from app.repositories.test_repository import TestRepository
from app.repositories.user_repository import UserRepository

__all__ = [
    "AdminRepository",
    "AnswerRepository",
    "AttemptRepository",
    "AttemptScores",
    "BaseRepository",
    "LeaderboardEntry",
    "QuestionDraft",
    "QuestionRepository",
    "QuestionStats",
    "ReceiptRepository",
    "SettingsRepository",
    "TestRepository",
    "UserRepository",
    "WarningSlot",
]
