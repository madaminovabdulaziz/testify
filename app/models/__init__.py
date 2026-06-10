"""Re-exports for every ORM model.

Importing this package is what populates ``Base.metadata`` with every
table — Alembic's ``env.py`` relies on it so autogenerate can see the
full schema.
"""

from app.models.admin import Admin
from app.models.answer import Answer
from app.models.attempt import Attempt
from app.models.base import Base
from app.models.broadcast import Broadcast
from app.models.question import Question
from app.models.receipt import PaymentReceipt
from app.models.setting import Setting
from app.models.test import Test
from app.models.user import User

__all__ = [
    "Admin",
    "Answer",
    "Attempt",
    "Base",
    "Broadcast",
    "PaymentReceipt",
    "Question",
    "Setting",
    "Test",
    "User",
]
