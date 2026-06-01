"""Unit tests for the PII/secret scrubbers (CODE_REVIEW M24/M25)."""

from __future__ import annotations

from app.core.logging import _scrub_secrets
from app.core.sentry import _scrub_event


def test_structlog_scrubber_redacts_top_level_and_nested() -> None:
    event = {
        "event": "receipt_submitted",
        "phone": "998901234567",
        "user_phone": "998900000000",  # substring match (M24)
        "update": {"contact": {"phone_number": "12345"}, "text": "hi"},  # nested (M25)
        "items": [{"bot_token": "abc"}],
        "telegram_id": 7,
    }
    out = _scrub_secrets(None, "info", event)

    assert out["phone"] == "***"
    assert out["user_phone"] == "***"
    assert out["update"]["contact"]["phone_number"] == "***"
    assert out["update"]["text"] == "hi"
    assert out["items"][0]["bot_token"] == "***"
    # Non-sensitive keys untouched.
    assert out["event"] == "receipt_submitted"
    assert out["telegram_id"] == 7


def test_sentry_scrubber_redacts_nested_and_substring_keys() -> None:
    event = {
        "extra": {"user_phone": "x", "phone_number": "y", "user_id": 1},
        "request": {"headers": {"X-Telegram-Bot-Api-Secret-Token": "s"}},
    }
    _scrub_event(event, {})

    assert event["extra"]["user_phone"] == "***"
    assert event["extra"]["phone_number"] == "***"
    assert event["extra"]["user_id"] == 1
    assert event["request"]["headers"]["X-Telegram-Bot-Api-Secret-Token"] == "***"
