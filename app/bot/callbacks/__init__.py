"""aiogram CallbackData factories grouped by domain."""

from app.bot.callbacks.publish import PublishAction, PublishCD
from app.bot.callbacks.receipt import ReceiptDecisionCD
from app.bot.callbacks.test import TestAnswerCD, TestFinishCD, TestNavCD

__all__ = [
    "PublishAction",
    "PublishCD",
    "ReceiptDecisionCD",
    "TestAnswerCD",
    "TestFinishCD",
    "TestNavCD",
]
